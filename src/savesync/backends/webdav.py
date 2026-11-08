"""WebDAV 后端（DESIGN.md §8.3）。urllib 裸实现，零第三方依赖。

PROPFIND Depth:1 递归列举 / MKCOL 建目录 / PUT 上传 / DELETE 删除；
路径分段 URL 编码；Basic/Digest 认证。

密码解析链（DESIGN.md §2.1）：cfg.password（明文）> cfg.password_env（环境变量）
> 首次 401 时回调 password_provider（交互 getpass，输入后本次会话复用）；
非交互无密码 → NeedsInputError（rc 3）。匿名服务器不 401 则全程不需要密码。
不依赖服务器 Last-Modified / ETag / COPY 语义；
清单 PUT 覆盖视为原子提交点，写后由 sync 层重读校验 version。
"""
from __future__ import annotations

import os
from typing import BinaryIO, Callable, Iterator

from ..config import BackendConfig
from ..manifest import RemoteManifest
from .base import FileMeta


class WebDAVBackend:
    def __init__(self, cfg: BackendConfig,
                 password_provider: Callable[[], str] | None = None):
        self.base_url = cfg.url.rstrip("/") if cfg.url else ""
        self.username = cfg.username
        self._password = cfg.password or os.environ.get(
            cfg.password_env or "", "")
        self._password_provider = password_provider  # 首次 401 时调用

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
