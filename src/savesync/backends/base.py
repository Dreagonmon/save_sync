"""Backend 协议（DESIGN.md §8）。

三个后端共享同一契约；write_manifest 是提交点：
备份先 put/delete 数据文件，最后 write_manifest；崩溃后清单未动，
下次运行按 hash 差集自愈。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator, Protocol

from ..manifest import RemoteManifest


@dataclass
class FileMeta:
    size: int
    mtime: float | None = None  # 后端可得则填，仅作展示/参考


class Backend(Protocol):
    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        """远端清单不存在返回 None。"""
        ...

    def write_manifest(self, manifest: RemoteManifest) -> None:
        """提交点。dir/webdav: 原子覆盖；zip: 触发整包重写（合并其余游戏）。"""
        ...

    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        """游戏数据目录实际内容（相对路径 -> meta），删除差集以此为准。"""
        ...

    def get(self, game_id: str, relpath: str) -> Iterator[bytes]: ...

    def put(self, game_id: str, relpath: str, stream: BinaryIO, size: int) -> None: ...

    def delete(self, game_id: str, relpath: str) -> None: ...
