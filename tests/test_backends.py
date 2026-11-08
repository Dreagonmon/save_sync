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

    def test_webdav_password_chain(self):
        import os
        from unittest import mock
        prov = lambda: "interactive"
        b = create_backend(self.mk("webdav", url="http://x/dav",
                                   password="plain"), prov)
        self.assertEqual(b._password, "plain")          # 明文优先
        with mock.patch.dict(os.environ, {"WD_PW": "from-env"}):
            b = create_backend(self.mk("webdav", url="http://x/dav",
                                       password_env="WD_PW"), prov)
            self.assertEqual(b._password, "from-env")   # 其次环境变量
        self.assertIs(b._password_provider, prov)       # 留空 → 401 时回调

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            create_backend(self.mk("ftp", path=Path("/x")))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
