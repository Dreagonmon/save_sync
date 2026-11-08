"""zip 后端（DESIGN.md §8.2）。单包多游戏，不做追加式增量。

缓冲全部 put/delete，write_manifest 时整包重写：
本次指定游戏取新数据，其余游戏条目从旧包原样搬运（合并语义），
tmp 写完后 os.replace 原子替换。成员名 UTF-8，ZIP_DEFLATED。
"""
from __future__ import annotations

from typing import BinaryIO, Iterator

from ..config import BackendConfig
from ..manifest import RemoteManifest
from .base import FileMeta


class ZipBackend:
    def __init__(self, cfg: BackendConfig):
        self.zip_path = cfg.path  # type: ignore[assignment]
        # 待写条目缓冲：game_id -> {成员名: 来源}；以及删除集
        self._pending: dict[str, dict[str, object]] = {}
        self._deleted: set[tuple[str, str]] = set()

    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        raise NotImplementedError

    def write_manifest(self, manifest: RemoteManifest) -> None:
        """提交点：整包重写（合并旧包中其余游戏条目）。"""
        raise NotImplementedError

    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        raise NotImplementedError

    def get(self, game_id: str, relpath: str) -> Iterator[bytes]:
        raise NotImplementedError

    def put(self, game_id: str, relpath: str, stream: BinaryIO, size: int) -> None:
        raise NotImplementedError

    def delete(self, game_id: str, relpath: str) -> None:
        raise NotImplementedError
