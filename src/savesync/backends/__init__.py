"""后端工厂：按 BackendConfig.type 实例化。"""
from __future__ import annotations

from ..config import BackendConfig
from .base import Backend, FileMeta


def create_backend(cfg: BackendConfig,
                   password_provider=None) -> Backend:
    """password_provider: 仅 webdav 使用，首次 401 时回调取密码（交互输入）。"""
    if cfg.type == "dir":
        from .dirfs import DirBackend
        return DirBackend(cfg)
    if cfg.type == "zip":
        from .zipfs import ZipBackend
        return ZipBackend(cfg)
    if cfg.type == "webdav":
        from .webdav import WebDAVBackend
        return WebDAVBackend(cfg, password_provider)
    raise ValueError(f"unknown backend type: {cfg.type}")


__all__ = ["Backend", "FileMeta", "create_backend"]
