"""清单读写（DESIGN.md §3）。

远端清单：version / upload_time / time_source / source_platform / files[]。
本地状态清单：上次同步点的 version + 文件 hash 表 + chosen_path。
本地写入一律 tmp + os.replace 原子落盘。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RemoteFile:
    path: str
    hash: str
    size: int
    mtime: str  # ISO8601 带时区


@dataclass
class RemoteManifest:
    game: str
    version: int
    upload_time: str
    time_source: str            # "ntp" | "local"
    source_platform: str
    files: list[RemoteFile]

    def to_json(self) -> dict[str, Any]: ...
    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RemoteManifest": ...


@dataclass
class LocalState:
    game: str
    version: int
    chosen_path: int | None
    files: dict[str, str]       # path -> sha256（上次同步点）
    updated: str

    def to_json(self) -> dict[str, Any]: ...
    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LocalState": ...


def load_local_state(data_dir: Path, game_id: str) -> LocalState | None:
    """不存在或损坏返回 None（视为版本 0，触发全量流程）。"""
    raise NotImplementedError


def save_local_state(data_dir: Path, state: LocalState) -> None:
    """tmp + os.replace 原子写。"""
    raise NotImplementedError
