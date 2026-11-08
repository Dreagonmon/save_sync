"""后端工厂：type -> 实现类分发。"""
from __future__ import annotations

import unittest
from pathlib import Path

from savesync.backends import create_backend
from savesync.backends.dirfs import DirBackend
from savesync.backends.webdav import WebDAVBackend
from savesync.backends.zipfs import ZipBackend
from savesync.config import BackendConfig


class TestFactory(unittest.TestCase):
    def mk(self, t, **kw):
        return BackendConfig(id="b", type=t, **kw)

    def test_dispatch(self):
        p = Path("/tmp/x")
        self.assertIsInstance(create_backend(self.mk("dir", path=p)),
                              DirBackend)
        self.assertIsInstance(create_backend(self.mk("zip", path=p)),
                              ZipBackend)
        self.assertIsInstance(
            create_backend(self.mk("webdav", url="http://x/dav")),
            WebDAVBackend)

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            create_backend(self.mk("ftp", path=Path("/x")))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
