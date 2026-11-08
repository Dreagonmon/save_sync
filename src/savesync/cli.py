"""CLI 薄适配层（DESIGN.md §9）。

savesync backup [game ...] [--backend B] [--path N] [--dry-run]   # 缺省 = all
savesync restore [game ...] [--backend B] [--path N]
savesync status [game ...]                             # 各游戏 L/R 版本与差异摘要
savesync games

游戏不绑定后端：--backend 指定，缺省交互选择（仅一个后端时自动选中，
有 last_backend 时作为默认项）。流程（§4.4）：plan → resolve_decisions →
(含 path 决策则重 plan) → execute → 渲染 SyncReport。交互与渲染只在本模块。

退出码：0 成功(含跳过) / 1 错误(含用户中止) / 2 用法错误
/ 3 NeedsInputError（打印问题+建议参数）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .backends import create_backend
from .config import BackendConfig, load_backends, load_games, require_backends
from .errors import BackendError, ConfigError, NeedsInputError, SaveSyncError
from .manifest import LocalState, load_local_state, save_local_state
from .paths import (current_platform, exists_quietly, find_candidate,
                    resolve_candidates)
from .prompt import (ask_decision, ask_password, is_interactive,
                     resolve_decisions)
from .sync import (DECISION_PATH, Decision, SyncReport, execute_backup,
                   execute_restore, plan_backup, plan_restore)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NEEDS_INPUT = 0, 1, 2, 3

DECISION_BACKEND = "backend"


def app_root(argv_root: str | None) -> Path:
    """软件根目录：--root 参数 > SAVESYNC_ROOT 环境变量 > 当前目录。"""
    return Path(argv_root or os.environ.get("SAVESYNC_ROOT") or Path.cwd())


def _select_games(args, games: dict) -> tuple[list[str], bool]:
    """返回 (选中的游戏 id, 是否显式点名)。"""
    if args.games:
        unknown = [g for g in args.games if g not in games]
        if unknown:
            raise ConfigError(f"未知游戏: {', '.join(unknown)}（见 savesync games）")
        return list(dict.fromkeys(args.games)), True
    return sorted(games), False


def _password_provider(cfg: BackendConfig, interactive: bool):
    """webdav 密码兜底回调：401 时触发（匿名服务器不打扰）。"""
    return lambda: ask_password(cfg.id, cfg.url or "", interactive)


def _pick_backend(args, backends: dict[str, BackendConfig],
                  local: LocalState | None, interactive: bool) -> str:
    """--backend 指定 > 唯一后端自动选 > 决策（last_backend 为默认项）。"""
    if getattr(args, "backend", None):
        if args.backend not in backends:
            raise ConfigError(
                f"未知后端: {args.backend}（见 savesync games）")
        return args.backend
    ids = sorted(backends)
    if len(ids) == 1:
        return ids[0]
    default = None
    if local and local.last_backend in ids:
        default = ids.index(local.last_backend)
    d = Decision(
        id=DECISION_BACKEND,
        question="选择存储后端",
        options=[f"{i}  {backends[i].type}  "
                 f"{backends[i].path or backends[i].url}" for i in ids],
        default=default,
        hint="--backend <id>（可选: " + ", ".join(ids) + "）",
    )
    return ids[ask_decision(d, interactive)]


def _run(cmd: str, args, root: Path, interactive: bool) -> int:
    cfg_dir = root / "config"
    backends = load_backends(cfg_dir)
    require_backends(backends)
    games = load_games(cfg_dir)
    selected, explicit = _select_games(args, games)
    if getattr(args, "path", None) is not None and len(selected) != 1:
        raise ConfigError("--path 仅支持单游戏操作")
    platform = current_platform()
    data_dir = root / "data"
    rc = EXIT_OK
    for gid in selected:
        game = games[gid]
        local = load_local_state(data_dir, gid)
        backend_id = _pick_backend(args, backends, local, interactive)
        bcfg = backends[backend_id]
        backend = create_backend(bcfg, _password_provider(bcfg, interactive))
        state = local          # 版本线按游戏全局唯一，所有后端共享
        chosen = local.chosen_path if local else None
        save_root = None
        if getattr(args, "path", None) is not None:
            c = find_candidate(resolve_candidates(game.paths.get(platform, {})),
                               args.path)
            if c is None:
                raise ConfigError(
                    f"{gid}: 无编号 {args.path} 的路径条目"
                    f"（见 savesync status {gid}）")
            save_root = c.path

        plan_kw = {"explicit": explicit} if cmd == "backup" else {}
        plan_fn = plan_backup if cmd == "backup" else plan_restore
        exec_fn = execute_backup if cmd == "backup" else execute_restore

        answers: dict[str, int] = {}
        plan = plan_fn(game, backend, state, platform=platform,
                       save_root=save_root, chosen_path=chosen, **plan_kw)
        while plan.decisions:
            answers.update(resolve_decisions(plan, interactive))
            if plan.save_root is None and DECISION_PATH in answers:
                save_root = plan.path_options[answers[DECISION_PATH]]
                answers.pop(DECISION_PATH)
                plan = plan_fn(game, backend, state, platform=platform,
                               save_root=save_root, chosen_path=chosen,
                               **({"explicit": True} if cmd == "backup" else {}))
                continue
            break
        report, new_state = exec_fn(plan, answers, game, backend, state,
                                    backend_id=backend_id, dry_run=args.dry_run)
        if new_state is not None and not args.dry_run:
            save_local_state(data_dir, new_state)
        print(_render(report, backend_id, args.dry_run))
        if not report.ok:
            rc = EXIT_ERROR
    return rc


def _render(report: SyncReport, backend_id: str, dry: bool) -> str:
    tag = f"[{report.game}@{backend_id}]"
    prefix = "(dry-run) " if dry else ""
    if report.skipped:
        return f"{tag} {prefix}跳过: {report.reason}"
    if not report.ok:
        return f"{tag} {prefix}已中止（用户选择）"
    parts: list[str] = []
    if report.action == "backup":
        parts.append(f"上传 {report.uploaded}")
        parts.append(f"删除远端 {report.deleted}")
        vb, va = report.version_before, report.version_after
        if va is not None and va != vb:
            parts.append(f"版本 {vb} → {va}")
        else:
            parts.append(f"版本 {va}（对齐）")
    else:
        parts.append(f"恢复 {report.overwritten} 个文件")
        if report.kept_local:
            parts.append(f"保留本地多余 {report.kept_local} 个")
        parts.append(f"版本 → {report.version_after}")
    act = "备份" if report.action == "backup" else "恢复"
    return f"{tag} {prefix}{act}完成: " + ", ".join(parts)


def _status(args, root: Path) -> int:
    cfg_dir = root / "config"
    backends = load_backends(cfg_dir)
    require_backends(backends)
    games = load_games(cfg_dir)
    selected, _ = _select_games(args, games)
    platform = current_platform()
    for gid in selected:
        game = games[gid]
        local = load_local_state(root / "data", gid)
        L = local.version if local else 0
        print(f"[{gid}] {game.name}  本地版本线 v{L}")
        for bid in sorted(backends):
            try:
                bcfg = backends[bid]
                R = create_backend(
                    bcfg, _password_provider(bcfg, is_interactive())
                ).read_manifest(gid)
            except BackendError as e:
                print(f"    后端 {bid}: 本地 v{L} / 远端 读取失败: {e}")
                continue
            Rv = R.version if R else None
            if Rv is None:
                rel = "远端无备份"
            elif Rv == L:
                rel = "已同步"
            elif Rv > L:
                rel = "远端较新（他处上传过，可 backup 覆盖或 restore 拉取）"
            else:
                rel = "远端较旧（被带外回退，或其他后端已备份到更新版本）"
            mark = " ←上次使用" if local and local.last_backend == bid else ""
            print(f"    后端 {bid}: 本地 v{L} / 远端 "
                  f"{'v' + str(Rv) if Rv is not None else '—'}  {rel}{mark}")
        for c in resolve_candidates(game.paths.get(platform, {})):
            mk = "存在" if exists_quietly(c.path) else "不存在"
            chosen = " ←上次选择" if local and local.chosen_path == c.index else ""
            print(f"    --path {c.index}  [{mk}] {c.path}  ({c.note}){chosen}")
    return EXIT_OK


def _games(root: Path) -> int:
    cfg_dir = root / "config"
    backends = load_backends(cfg_dir)
    require_backends(backends)
    games = load_games(cfg_dir)
    for bid, b in sorted(backends.items()):
        loc = b.path if b.path else b.url
        print(f"后端 {bid}: type={b.type}  {loc}")
    platform = current_platform()
    for gid, g in sorted(games.items()):
        print(f"游戏 {gid}: {g.name}")
        for c in resolve_candidates(g.paths.get(platform, {})):
            mark = "存在" if exists_quietly(c.path) else "不存在"
            print(f"    [{mark}] {c.path}  ({c.note})")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="savesync", description="游戏存档备份同步工具（后端仅保留最新镜像）")
    parser.epilog = """\
示例:
  savesync games                     列出配置的游戏与后端
  savesync backup                    备份全部游戏
  savesync backup delta-v -B nas     备份单个游戏到指定后端
  savesync restore -B nas --path 1   从指定后端恢复，并指定存档路径编号
  savesync status                    逐后端对比本地/远端版本线

后端选择（backup/restore，详见 savesync <命令> -h）:
  -B/--backend ID 显式指定 > 仅一个后端时自动选中 > 交互选择
  （默认项 = 该游戏上次使用的后端；非交互模式报退出码 3 并给出建议参数）
"""
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("--root", metavar="DIR",
                        help="软件根目录（含 config/ 与 data/），"
                             "默认 $SAVESYNC_ROOT 或当前目录")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd, hlp in (("backup", "备份存档到后端"), ("restore", "从后端恢复存档")):
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("games", nargs="*", metavar="GAME",
                        help="游戏 id，缺省为全部（隐式游戏找不到路径时跳过）")
        sp.add_argument("-B", "--backend", metavar="ID",
                        help="存储后端 id（缺省：唯一后端自动选，否则交互选择）")
        sp.add_argument("--path", type=int, metavar="N",
                        help="指定存档路径编号（仅单游戏操作）")
        sp.add_argument("--dry-run", action="store_true", help="只打印差集计划")
    st = sub.add_parser("status", help="各游戏版本与路径状态")
    st.add_argument("games", nargs="*", metavar="GAME")
    sub.add_parser("games", help="列出配置的游戏与后端")
    args = parser.parse_args(argv)

    root = app_root(args.root)
    try:
        if args.cmd == "games":
            return _games(root)
        if args.cmd == "status":
            return _status(args, root)
        return _run(args.cmd, args, root, is_interactive())
    except NeedsInputError as e:
        print("需要人工输入（非交互模式）:", file=sys.stderr)
        for d in e.decisions:
            print(f"  ? {d.question}", file=sys.stderr)
            for i, opt in enumerate(d.options, 1):
                print(f"      [{i}] {opt}", file=sys.stderr)
            if d.hint:
                print(f"      建议: {d.hint}", file=sys.stderr)
        return EXIT_NEEDS_INPUT
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return EXIT_ERROR
    except SaveSyncError as e:
        print(f"错误: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
