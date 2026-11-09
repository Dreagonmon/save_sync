"""Backend 抽象基类（DESIGN.md §8）。

三个后端显式继承同一契约（名义类型：实例化即强制实现全部抽象方法）；
write_manifest 是提交点：备份先 put/delete 数据文件，最后
write_manifest；崩溃后清单未动，下次运行按 hash 差集自愈。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from ..config import BackendConfig
from ..manifest import RemoteManifest


@dataclass
class FileMeta:
    size: int
    mtime: float | None = None  # 后端可得则填，仅作展示/参考


class Backend(ABC):
    """后端接口：远端只保留最新镜像，无历史条目。

    relpath 一律为存档根相对路径、正斜杠；清单是 mtime 的唯一来源。
    """

    def __init__(self, cfg: BackendConfig) -> None:
        self.cfg = cfg
        self.id = cfg.id
        self.type = cfg.type

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}>"

    @abstractmethod
    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        """远端清单不存在返回 None。"""

    @abstractmethod
    def write_manifest(self, manifest: RemoteManifest) -> None:
        """提交点。dir/webdav: 原子覆盖；zip: 触发整包重写（合并其余游戏）。"""

    @abstractmethod
    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        """游戏数据目录实际内容（相对路径 -> meta），删除差集以此为准。"""

    @abstractmethod
    def get(self, game_id: str, relpath: str) -> Iterator[bytes]:
        """分块流式读取；不存在时抛 BackendError。"""

    @abstractmethod
    def put(self, game_id: str, relpath: str, stream: BinaryIO,
            size: int) -> None:
        """流式写入单文件（size 已知，全程不落盘缓冲）。"""

    @abstractmethod
    def delete(self, game_id: str, relpath: str) -> None:
        """删除远端文件；目标不存在视为幂等 no-op。"""
