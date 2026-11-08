"""scanner 模块单元测试：ignore、剪枝、两级 hash、符号链接。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from savesync.scanner import is_ignored, iter_dir_prefixes, scan


def _w(p: Path, data: bytes = b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class TestIgnore(unittest.TestCase):
    def test_match(self):
        self.assertTrue(is_ignored("a.bak", ["*.bak"]))
        self.assertTrue(is_ignored("logs/x.log", ["logs/*"]))       # * 跨 /
        self.assertTrue(is_ignored("a/b/cache/k.dat", ["*/cache/*"]))
        self.assertFalse(is_ignored("save/sav", ["*.bak"]))
        self.assertFalse(is_ignored("SAVE.TXT", ["*.txt"]))          # 大小写敏感

    def test_prefixes(self):
        self.assertEqual(list(iter_dir_prefixes("a/b/c")), ["a", "a/b"])


class TestScan(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _w(self.root / "a.txt", b"111")
        _w(self.root / "sub/b.bin", b"2222")
        _w(self.root / "logs/x.log", b"333")
        _w(self.root / "tmp/y.tmp", b"4")
        self.link = self.root / "link.txt"
        try:
            os.symlink(self.root / "a.txt", self.link)
        except (OSError, NotImplementedError):
            self.link = None

    def tearDown(self):
        self._td.cleanup()

    def test_ignore_and_prune(self):
        got = {f.path for f in scan(self.root, ["logs/*", "*.tmp"])}
        self.assertEqual(got, {"a.txt", "sub/b.bin"})  # logs 整树剪枝、*.tmp 忽略

    def test_symlink_skipped(self):
        if self.link is None:
            self.skipTest("symlink 不可用")
        got = {f.path for f in scan(self.root, [])}
        self.assertNotIn("link.txt", got)

    def test_two_stage_hash(self):
        first = {f.path: f for f in scan(self.root, [])}
        known = {p: (f.size, f.mtime, "KNOWN") for p, f in first.items()}
        again = {f.path: f for f in scan(self.root, [], known)}
        self.assertEqual(again["a.txt"].hash, "KNOWN")  # (size,mtime) 一致 → 沿用
        # mtime 变化 → 重算
        st = (self.root / "a.txt").stat()
        os.utime(self.root / "a.txt", (st.st_atime, st.st_mtime + 5))
        again2 = {f.path: f for f in scan(self.root, [], known)}
        self.assertNotEqual(again2["a.txt"].hash, "KNOWN")

    def test_hash_matches_content(self):
        import hashlib
        got = {f.path: f for f in scan(self.root, [])}
        self.assertEqual(got["a.txt"].hash, hashlib.sha256(b"111").hexdigest())
        self.assertEqual(got["a.txt"].size, 3)


if __name__ == "__main__":
    unittest.main()
