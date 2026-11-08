"""本地目录后端（DESIGN.md §8.1）。参照实现，其余后端对它做契约对照测试。

布局：<root>/<game_id>/…（数据镜像） + <root>/<game_id>.json（清单）。
写入 tmp + os.replace 原子落盘；游戏目录视为本工具独占。
"""
from __future__ import annotations

from typing import BinaryIO, Iterator

from ..config import BackendConfig
from ..manifest import RemoteManifest
from .base import FileMeta


class DirBackend:
    def __init__(self, cfg: BackendConfig):
        self.root = cfg.path  # type: ignore[assignment]

    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        raise NotImplementedError

    def write_manifest(self, manifest: RemoteManifest) -> None:
        raise NotImplementedError

    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        raise NotImplementedError

    def get(self, game_id: str, relpath: str) -> Iterator[bytes]:
        raise NotImplementedError

    def put(self, game_id: str, relpath: str, stream: BinaryIO, size: int) -> None:
        raise NotImplementedError

    def delete(self, game_id: str, relpath: str) -> None:
        raise NotImplementedError
