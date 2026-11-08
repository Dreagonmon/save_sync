"""配置加载：config/g-<id>.toml（游戏）与 config/s-<id>.toml（后端）。

文件名 stem 即 id（DESIGN.md §1/§2）；toml 只读（tomllib），工具不改写用户配置。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import ConfigError  # noqa: F401  （对外从 config 导入亦可）
from .paths import PLATFORMS, expand

BackendType = Literal["dir", "zip", "webdav"]


@dataclass
class BackendConfig:
    id: str
    type: BackendType
    path: Path | None = None          # dir / zip
    url: str | None = None            # webdav
    username: str | None = None
    password_env: str | None = None


@dataclass
class GameConfig:
    id: str
    name: str
    ignore: list[str] = field(default_factory=list)
    # platform -> {路径原文: 备注}，平台 ∈ {linux, macos, windows}；
    # 同一平台可多条；Android(Termux) 归 linux。
    # 游戏不绑定后端：备份/恢复时指定或选择（--backend）。
    paths: dict[str, dict[str, str]] = field(default_factory=dict)


def config_root(app_root: Path) -> Path:
    return app_root / "config"


def _load_toml(p: Path) -> dict:
    try:
        with open(p, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p.name}: TOML 解析失败: {e}") from e
    except OSError as e:
        raise ConfigError(f"{p.name}: 读取失败: {e}") from e


def load_backends(root: Path) -> dict[str, BackendConfig]:
    """加载全部 s-*.toml，校验 type 合法且必填字段齐全。"""
    out: dict[str, BackendConfig] = {}
    for p in sorted(Path(root).glob("s-*.toml")):
        d = _load_toml(p)
        bid = p.stem.removeprefix("s-")
        t = d.get("type")
        if t not in ("dir", "zip", "webdav"):
            raise ConfigError(f"{p.name}: type 必须是 dir/zip/webdav，得到 {t!r}")
        cfg = BackendConfig(id=bid, type=t)
        if t in ("dir", "zip"):
            if not d.get("path"):
                raise ConfigError(f"{p.name}: type={t} 需要 path")
            cfg.path = expand(str(d["path"]))
        else:
            if not d.get("url"):
                raise ConfigError(f"{p.name}: type=webdav 需要 url")
            cfg.url = str(d["url"])
            cfg.username = d.get("username")
            cfg.password_env = d.get("password_env")
        out[bid] = cfg
    return out


def load_games(root: Path) -> dict[str, GameConfig]:
    """加载全部 g-*.toml，校验 paths 平台键合法。"""
    out: dict[str, GameConfig] = {}
    for p in sorted(Path(root).glob("g-*.toml")):
        d = _load_toml(p)
        gid = p.stem.removeprefix("g-")
        paths_raw = d.get("paths", {})
        if not isinstance(paths_raw, dict) or not paths_raw:
            raise ConfigError(f"{p.name}: paths 至少配置一个平台")
        paths: dict[str, dict[str, str]] = {}
        for plat, entries in paths_raw.items():
            if plat not in PLATFORMS:
                raise ConfigError(
                    f"{p.name}: 未知平台键 {plat!r}（可用: {'/'.join(PLATFORMS)}；"
                    f"Android 归 linux）")
            if not isinstance(entries, dict) or not entries:
                raise ConfigError(f"{p.name}: paths.{plat} 需要至少一条 路径=备注")
            paths[plat] = {str(k): str(v) for k, v in entries.items()}
        ignore = d.get("ignore", [])
        if not isinstance(ignore, list):
            raise ConfigError(f"{p.name}: ignore 必须是字符串数组")
        out[gid] = GameConfig(
            id=gid, name=str(d.get("name", gid)),
            ignore=[str(x) for x in ignore], paths=paths,
        )
    return out


def require_backends(backends: dict[str, BackendConfig]) -> None:
    """至少配置一个后端。"""
    if not backends:
        raise ConfigError("未配置任何后端（应有 config/s-<id>.toml）")
