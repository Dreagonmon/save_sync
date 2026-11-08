"""WebDAV 后端（DESIGN.md §8.3）。urllib 裸实现，零第三方依赖。

PROPFIND Depth:1 递归列举 / MKCOL 建目录 / PUT 上传 / DELETE 删除；
路径分段 URL 编码；Basic/Digest 认证。
不依赖服务器 Last-Modified / ETag / COPY 语义；
清单 PUT 覆盖视为原子提交点，写后由 sync 层重读校验 version。
"""
from __future__ import annotations

from typing import BinaryIO, Iterator

from ..config import BackendConfig
from ..manifest import RemoteManifest
from .base import FileMeta


class WebDAVBackend:
    def __init__(self, cfg: BackendConfig):
        self.base_url = cfg.url.rstrip("/") if cfg.url else ""
        self.username = cfg.username
        import os
        self.password = os.environ.get(cfg.password_env or "", "")

    def _url(self, *parts: str) -> str:
        """分段 URL 编码拼接（不编码 '/'）。"""
        raise NotImplementedError

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
