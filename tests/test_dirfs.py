"""dirfs 后端契约测试（其余后端将来对照此参照实现）。"""
from __future__ import annotations

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from savesync.backends.dirfs import DirBackend
from savesync.config import BackendConfig
from savesync.manifest import RemoteFile, RemoteManifest


class TestDirBackend(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.b = DirBackend(BackendConfig(id="bk", type="dir", path=self.root))

    def tearDown(self):
        self._td.cleanup()

    def test_put_get_list_delete(self):
        rel = "存档/第1章/options.txt"
        self.b.put("g1", rel, io.BytesIO(b"hello"), 5)
        self.assertEqual(b"".join(self.b.get("g1", rel)), b"hello")
        self.assertIn(rel, self.b.list_files("g1"))
        self.assertEqual(self.b.list_files("g1")[rel].size, 5)
        self.b.delete("g1", rel)
        self.assertNotIn(rel, self.b.list_files("g1"))
        self.b.delete("g1", rel)  # 幂等

    def test_manifest_roundtrip_missing(self):
        self.assertIsNone(self.b.read_manifest("g1"))
        m = RemoteManifest(game="g1", version=1,
                           upload_time="2026-11-08T13:00:00+08:00",
                           time_source="local", source_platform="linux",
                           files=[RemoteFile("a", "h", 1,
                                             "2026-11-08T12:00:00+08:00")])
        self.b.write_manifest(m)
        self.assertEqual(self.b.read_manifest("g1"), m)
        # 清单在数据目录之外
        self.assertTrue((self.root / "g1.json").exists())

    def test_overwrite_atomic(self):
        self.b.put("g1", "a.bin", io.BytesIO(b"old"), 3)
        self.b.put("g1", "a.bin", io.BytesIO(b"new"), 3)
        self.assertEqual(b"".join(self.b.get("g1", "a.bin")), b"new")
        # 无残留 tmp
        leftovers = [p.name for p in (self.root / "g1").rglob("*")
                     if "tmp" in p.name]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
