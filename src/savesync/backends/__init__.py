"""后端工厂：按 BackendConfig.type 实例化。"""
from __future__ import annotations

from ..config import BackendConfig
from .base import Backend, FileMeta


def create_backend(cfg: BackendConfig) -> Backend:
    if cfg.type == "dir":
        from .dirfs import DirBackend
        return DirBackend(cfg)
    if cfg.type == "zip":
        from .zipfs import ZipBackend
        return ZipBackend(cfg)
    if cfg.type == "webdav":
        from .webdav import WebDAVBackend
        return WebDAVBackend(cfg)
    raise ValueError(f"unknown backend type: {cfg.type}")


__all__ = ["Backend", "FileMeta", "create_backend"]
