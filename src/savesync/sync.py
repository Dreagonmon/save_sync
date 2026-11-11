"""backup / restore 状态机（DESIGN.md §4），两阶段执行模型（§4.4）。

纯逻辑层：不 print、不读 stdin、无全局状态。人工确认以 Decision 数据
返回给调用方（CLI / 未来 WebUI 收集 answers），再 execute。

版本号推进：新版本 = max(本地 L, 远端 R) + 1，单调不回退。
本地 L 为游戏全局版本线（所有后端共享一份本地清单），远端各镜像均对照它判前后。
提交点：备份先传/删数据、最后写远端清单；恢复先写本地文件、最后写状态清单。
备份写完远端清单后重读校验 version，防并发写入者（ConcurrentWriteError）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from .backends.base import Backend
from .config import GameConfig
from .errors import (ConcurrentWriteError, NeedsInputError, NoBackupError,
                     NoSourcePathError)
from .manifest import (FileEntry, LocalState, RemoteFile, RemoteManifest,
                       iso_now, iso_to_ts, ts_to_iso)
from .ntp import now_with_source
from .paths import (current_platform, existing_only, exists_quietly,
                    find_candidate, resolve_candidates)
from .scanner import ScannedFile, scan

# Decision.id 约定（core 产生，交互层按 id 渲染控件/映射参数）
DECISION_PATH = "path"                        # 多条候选路径，选择编号
DECISION_REMOTE_AHEAD = "remote_ahead"        # 备份：R>L 他处上传过，是否覆盖远端
DECISION_REMOTE_BEHIND = "remote_behind"      # 备份：R<L 远端被带外回退，是否仍覆盖
DECISION_RESTORE_OVERWRITE = "restore_overwrite"      # 恢复：是否用远端覆盖本地
DECISION_RESTORE_LOCAL_DIRTY = "restore_local_dirty"  # 恢复：本地有未备份修改，是否继续

Answers = dict[str, int]          # Decision.id -> 选项下标（0 基）
Progress = Callable[[str], None]  # 可选进度回调（WebUI 用），参数为人读文本


@dataclass
class Decision:
    id: str
    question: str
    options: list[str]
    default: int | None = None
    abort_option: int | None = None   # 该选项下标代表「中止」
    hint: str | None = None           # 非交互建议，如 "--path 2" / "先执行 backup"


@dataclass
class Plan:
    game: str
    action: Literal["backup", "restore"]
    save_root: Path | None = None
    chosen_path: int | None = None          # 选定的路径编号（1 基，入状态清单）
    upload: list[str] = field(default_factory=list)        # 新增或 hash 变化
    delete: list[str] = field(default_factory=list)        # 远端目录中本地不存在
    overwrite_local: list[str] = field(default_factory=list)
    keep_local: list[str] = field(default_factory=list)    # 恢复：本地多余，保留不删
    version_before: tuple[int, int | None] = (0, None)     # (L, R)
    new_version: int | None = None
    decisions: list[Decision] = field(default_factory=list)
    skipped: bool = False         # all 隐式包含且 0 匹配 → 跳过，不算失败
    skip_reason: str | None = None
    path_options: list[Path] = field(default_factory=list)   # DECISION_PATH 作答映射
    path_indices: list[int] = field(default_factory=list)    # 对应 1 基编号
    # plan 内部缓存扫描结果供 execute 复用；重 plan 时刷新，不参与序列化
    _scan: list[ScannedFile] | None = field(default=None, repr=False)


@dataclass
class SyncReport:
    game: str
    action: Literal["backup", "restore"]
    ok: bool                      # 用户中止 → ok=False, reason="aborted"（非异常）
    skipped: bool = False
    reason: str | None = None
    uploaded: int = 0
    deleted: int = 0
    overwritten: int = 0
    kept_local: int = 0
    version_before: int | None = None
    version_after: int | None = None


# ---------------------------------------------------------------- 内部工具

def _resolve_save_root(game: GameConfig, platform: str,
                       chosen_path: int | None,
                       save_root: Path | None, plan: Plan, *, for_restore: bool,
                       explicit: bool) -> bool:
    """填充 plan.save_root/chosen_path 或产出 DECISION_PATH。

    返回 True 表示 save_root 已确定；False 表示 plan 携带 path 决策待作答。
    """
    cands = resolve_candidates(game.paths.get(platform, {}))
    if save_root is not None:
        c = find_candidate(cands, _index_of_path(cands, save_root)) \
            if _index_of_path(cands, save_root) else None
        plan.save_root = save_root
        plan.chosen_path = c.index if c else None
        if not for_restore and not exists_quietly(save_root):
            raise NoSourcePathError(
                f"{game.id}: 指定的存档路径不存在: {save_root}")
        return True
    existing = existing_only(cands)
    if len(existing) == 1:
        plan.save_root = existing[0].path
        plan.chosen_path = existing[0].index
        return True
    if not existing and for_restore and len(cands) == 1:
        plan.save_root = cands[0].path  # 唯一候选无歧义：直接创建
        plan.chosen_path = cands[0].index
        return True
    if len(existing) > 1:
        pool = existing
    elif for_restore and cands:
        pool = cands  # 恢复：0 条存在 → 列出全部（含不存在）供选择，选中则创建
    else:
        pool = []
    if not pool:
        if explicit:
            raise NoSourcePathError(
                f"{game.id}: 平台 {platform} 无已存在的存档路径")
        plan.skipped = True
        plan.skip_reason = "no path found"
        return False
    default = None
    if chosen_path is not None:
        # 状态记得上次选过 → 自动沿用（resolve_decisions 对默认项不询问）
        if chosen_path in [c.index for c in pool]:
            default = [c.index for c in pool].index(chosen_path)
    # 注意：0 条存在且从未选过时不给默认——静默挑第一条曾把新游戏
    # 恢复到错误路径；交互必须询问，非交互退出码 3 提示 --path N
    plan.decisions.append(Decision(
        id=DECISION_PATH,
        question=("选择恢复目标路径（不存在的将自动创建）" if pool is cands
                  else "存在多条已存在的存档路径，选择本次操作的目标"),
        options=[f"{c.note}  {c.path}" for c in pool],
        default=default,
        hint=f"--path N（N ∈ {[c.index for c in pool]}）",
    ))
    plan.path_options = [c.path for c in pool]
    plan.path_indices = [c.index for c in pool]
    return False


def _index_of_path(cands, path: Path) -> int | None:
    for c in cands:
        if c.path == Path(path):
            return c.index
    return None


def _check_decisions(plan: Plan, answers: Answers) -> str | None:
    """校验决策作答完备；返回中止原因或 None。未答且无默认 → NeedsInputError。"""
    missing = []
    for d in plan.decisions:
        if d.id not in answers:
            if d.default is not None:
                answers[d.id] = d.default
            else:
                missing.append(d)
    if missing:
        raise NeedsInputError(missing)
    for d in plan.decisions:
        if d.abort_option is not None and answers[d.id] == d.abort_option:
            return "aborted"
    return None


def _known(state: LocalState | None):
    if not state:
        return None
    return {p: (e.size, e.mtime, e.hash) for p, e in state.files.items()}


# ---------------------------------------------------------------- 备份

def plan_backup(
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    platform: str,
    save_root: Path | None = None,
    explicit: bool = True,
    chosen_path: int | None = None,
) -> Plan:
    """§4.1 状态机的只读部分。

    - save_root 未显式给出且多条候选 → decisions 含 DECISION_PATH，
      差集为空，调用方作答后带 save_root 重新 plan。
    - 0 条匹配：显式点名 → NoSourcePathError；隐式(all) → skipped=True。
    """
    plan = Plan(game=game.id, action="backup")
    if not _resolve_save_root(game, platform, chosen_path, save_root, plan,
                              for_restore=False, explicit=explicit):
        return plan

    R = backend.read_manifest(game.id)
    scan_files = scan(plan.save_root, game.ignore, _known(state))
    plan._scan = scan_files
    local = {f.path: f.hash for f in scan_files}
    remote = {f.path: f.hash for f in R.files} if R else {}
    plan.upload = sorted(p for p, h in local.items() if remote.get(p) != h)
    plan.delete = sorted(p for p in remote if p not in local)

    L = state.version if state else 0
    Rv = R.version if R else None
    plan.version_before = (L, Rv)

    if R is None:                      # 首次备份（或远端清单被删）→ 全量
        plan.new_version = L + 1
        return plan
    if not plan.upload and not plan.delete:
        # 无差集：远端即最新，本地状态对齐到 R（含崩溃后 L 落后的自愈）
        plan.new_version = Rv
        if state and state.version == Rv:
            plan.skipped = True
            plan.skip_reason = "已是最新"
        return plan
    if Rv > L:
        plan.new_version = Rv + 1
        plan.decisions.append(Decision(
            id=DECISION_REMOTE_AHEAD,
            question=f"远端版本 {Rv} 高于本地 {L}（其他设备上传过），是否以本地覆盖远端？",
            options=["覆盖远端", "中止（建议先 restore）"],
            abort_option=1,
            hint="确认覆盖请交互作答，或先执行 restore",
        ))
    elif Rv < L:
        plan.new_version = L + 1
        plan.decisions.append(Decision(
            id=DECISION_REMOTE_BEHIND,
            question=f"远端版本 {Rv} 低于本地 {L}（远端被带外回退，或其他后端已备份到更新版本），是否仍以本地覆盖？",
            options=["覆盖远端", "中止"],
            abort_option=1,
        ))
    else:
        plan.new_version = L + 1       # 正常增量
    return plan


def execute_backup(
    plan: Plan,
    answers: Answers,
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    backend_id: str = "",
    dry_run: bool = False,
    progress: Progress | None = None,
) -> tuple[SyncReport, LocalState | None]:
    """按 §4.1 执行。返回 (报告, 新状态清单)，调用方在提交点后落盘。"""
    report = SyncReport(game=plan.game, action="backup", ok=True,
                        version_before=plan.version_before[0])
    if plan.skipped:
        report.skipped = True
        report.reason = plan.skip_reason
        return report, None
    if plan.save_root is None:
        raise NeedsInputError(plan.decisions)
    abort = _check_decisions(plan, answers)
    if abort:
        report.ok = False
        report.reason = abort
        return report, None

    scan_files = plan._scan if plan._scan is not None \
        else scan(plan.save_root, game.ignore)
    by_path = {f.path: f for f in scan_files}
    report.uploaded = len(plan.upload)
    report.deleted = len(plan.delete)
    report.version_after = plan.new_version
    if dry_run:
        return report, None

    if plan.upload or plan.delete:
        for rel in plan.upload:
            if progress:
                progress(f"上传 {rel}")
            with open(plan.save_root / rel, "rb") as fh:
                backend.put(plan.game, rel, fh, by_path[rel].size)
        for rel in plan.delete:
            if progress:
                progress(f"删除远端 {rel}")
            backend.delete(plan.game, rel)
        upload_time, time_source = now_with_source()
        backend.write_manifest(RemoteManifest(
            game=plan.game, version=plan.new_version,
            upload_time=upload_time.isoformat(timespec="seconds"),
            time_source=time_source, source_platform=current_platform(),
            files=[RemoteFile(f.path, f.hash, f.size, ts_to_iso(f.mtime))
                   for f in scan_files],
        ))
        verify = backend.read_manifest(plan.game)
        if verify is None or verify.version != plan.new_version:
            raise ConcurrentWriteError(
                f"{plan.game}: 写入后远端版本为 "
                f"{verify.version if verify else None}，预期 {plan.new_version}")
    elif progress and state and state.version != plan.new_version:
        progress("无差集，仅对齐本地状态版本")

    new_state = LocalState(
        game=plan.game, version=plan.new_version,
        files={f.path: FileEntry(f.hash, f.size, f.mtime) for f in scan_files},
        chosen_path=plan.chosen_path if plan.chosen_path is not None
        else (state.chosen_path if state else None),
        last_backend=backend_id,
    )
    return report, new_state


# ---------------------------------------------------------------- 恢复

def plan_restore(
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    platform: str,
    save_root: Path | None = None,
    chosen_path: int | None = None,
) -> Plan:
    """§4.2 状态机的只读部分。远端无清单 → NoBackupError。"""
    plan = Plan(game=game.id, action="restore")
    R = backend.read_manifest(game.id)
    if R is None:
        raise NoBackupError(f"{game.id}: 远端无备份清单，无法恢复")

    if not _resolve_save_root(game, platform, chosen_path, save_root, plan,
                              for_restore=True, explicit=True):
        return plan

    local_scan = scan(plan.save_root, game.ignore) if exists_quietly(plan.save_root) \
        else []
    plan._scan = local_scan
    local = {f.path: f.hash for f in local_scan}
    remote = {f.path: f.hash for f in R.files}
    plan.overwrite_local = sorted(p for p, h in remote.items() if local.get(p) != h)
    plan.keep_local = sorted(p for p in local if p not in remote)

    L = state.version if state else 0
    Rv = R.version
    plan.version_before = (L, Rv)
    plan.new_version = Rv

    if not plan.overwrite_local:
        plan.skipped = True
        plan.skip_reason = "已是最新"
        return plan

    state_hashes = {p: e.hash for p, e in state.files.items()} if state else {}
    if Rv > L:
        # 远端为准；但本地存在「未备份且将被覆盖」的修改时加问（§4.4 边界②）
        at_risk = [p for p in plan.overwrite_local
                   if p in local and local[p] != state_hashes.get(p)]
        if at_risk:
            plan.decisions.append(Decision(
                id=DECISION_RESTORE_LOCAL_DIRTY,
                question=f"本地有 {len(at_risk)} 个未备份的修改将被远端覆盖，"
                         f"是否继续恢复？",
                options=["继续恢复", "中止（建议先 backup）"],
                abort_option=1,
                hint="先执行 backup 保留本地修改",
            ))
    else:
        # R == L：本地与远端 hash 有差异 → 本地有未备份修改；R < L：远端被覆盖过
        why = ("本地与远端同版本但内容不同（本地有未备份修改）"
               if Rv == L else f"远端版本 {Rv} 低于本地 {L}（远端被覆盖过）")
        plan.decisions.append(Decision(
            id=DECISION_RESTORE_OVERWRITE,
            question=f"{why}，是否用远端版本覆盖本地？",
            options=["用远端覆盖本地", "中止"],
            abort_option=1,
        ))
    return plan


def execute_restore(
    plan: Plan,
    answers: Answers,
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    backend_id: str = "",
    dry_run: bool = False,
    progress: Progress | None = None,
) -> tuple[SyncReport, LocalState | None]:
    """按 §4.2 执行。恢复不删除本地多余文件。"""
    report = SyncReport(game=plan.game, action="restore", ok=True,
                        version_before=plan.version_before[0])
    if plan.skipped:
        report.skipped = True
        report.reason = plan.skip_reason
        return report, None
    if plan.save_root is None:
        raise NeedsInputError(plan.decisions)
    abort = _check_decisions(plan, answers)
    if abort:
        report.ok = False
        report.reason = abort
        return report, None

    R = backend.read_manifest(plan.game)
    if R is None:
        raise NoBackupError(f"{plan.game}: 执行时远端清单消失")
    report.overwritten = len(plan.overwrite_local)
    report.kept_local = len(plan.keep_local)
    report.version_after = R.version
    if dry_run:
        return report, None

    target = set(plan.overwrite_local)
    plan.save_root.mkdir(parents=True, exist_ok=True)
    for rf in R.files:
        if rf.path not in target:
            continue
        if progress:
            progress(f"恢复 {rf.path}")
        dest = plan.save_root / rf.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".savesync-tmp")
        with open(tmp, "wb") as out:
            for chunk in backend.get(plan.game, rf.path):
                out.write(chunk)
        os.replace(tmp, dest)
        try:
            if rf.mtime:  # 尽力而为：无 mtime 的环境跳过，失败不影响正确性
                os.utime(dest, (iso_to_ts(rf.mtime),) * 2)
        except (OSError, ValueError):
            pass

    # 状态条目直接记清单 mtime（与远端清单一致；utime 尽力设置后通常吻合，
    # 失败则下次备份重算一次 hash 并以实际 stat 自愈；无 mtime 记 0.0，
    # 两级过滤不命中、每次备份重算，仅慢不影响正确）
    files = {rf.path: FileEntry(rf.hash, rf.size,
                                iso_to_ts(rf.mtime) if rf.mtime else 0.0)
             for rf in R.files}
    new_state = LocalState(
        game=plan.game, version=R.version, files=files,
        chosen_path=plan.chosen_path if plan.chosen_path is not None
        else (state.chosen_path if state else None),
        last_backend=backend_id,
    )
    return report, new_state
