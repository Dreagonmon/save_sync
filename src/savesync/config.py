"""配置加载：config/g-<id>.toml（游戏）与 config/s-<id>.toml（后端）。

文件名 stem 即 id（DESIGN.md §1/§2）；toml 只读（tomllib），工具不改写用户配置。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import ConfigError  # noqa: F401  （对外从 config 导入亦可）

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
    backend: str                      # 引用 BackendConfig.id
    ignore: list[str] = field(default_factory=list)
    # platform -> {路径原文: 备注}，平台 ∈ {linux, macos, windows}；
    # 同一平台可多条；Android(Termux) 归 linux
    paths: dict[str, dict[str, str]] = field(default_factory=dict)


def config_root(app_root: Path) -> Path:
    return app_root / "config"


def load_backends(root: Path) -> dict[str, BackendConfig]:
    """加载全部 s-*.toml，校验 type 合法且必填字段齐全。"""
    raise NotImplementedError


def load_games(root: Path) -> dict[str, GameConfig]:
    """加载全部 g-*.toml，校验 backend 引用存在、paths 平台键合法。"""
    raise NotImplementedError
