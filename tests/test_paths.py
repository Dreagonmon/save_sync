"""paths 模块单元测试。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from savesync.paths import (expand, exists_quietly, find_candidate,
                            resolve_candidates)


class TestExpand(unittest.TestCase):
    def test_tilde(self):
        p = expand("~/saves/x")
        self.assertFalse(str(p).startswith("~"))
        self.assertTrue(str(p).endswith("saves/x"))

    def test_dollar_var(self):
        os.environ["SS_TEST_VAR"] = "/opt/gg"
        try:
            self.assertEqual(expand("$SS_TEST_VAR/saves"), Path("/opt/gg/saves"))
        finally:
            del os.environ["SS_TEST_VAR"]

    def test_percent_var(self):
        os.environ["SS_TEST_VAR"] = "/opt/gg"
        try:
            self.assertEqual(expand("%SS_TEST_VAR%/saves"), Path("/opt/gg/saves"))
        finally:
            del os.environ["SS_TEST_VAR"]

    def test_undefined_kept(self):
        self.assertEqual(expand("$SS_NOPE_X/y"), Path("$SS_NOPE_X/y"))


class TestCandidates(unittest.TestCase):
    def test_indices_and_expand(self):
        os.environ["SS_T2"] = "/mnt/x"
        try:
            cands = resolve_candidates({"/home/u/saves": "Steam版",
                                        "$SS_T2/saves": "外挂盘"})
        finally:
            del os.environ["SS_T2"]
        self.assertEqual([c.index for c in cands], [1, 2])
        self.assertEqual(cands[1].path, Path("/mnt/x/saves"))
        self.assertEqual(cands[0].note, "Steam版")

    def test_find(self):
        cands = resolve_candidates({"/a": "n1", "/b": "n2"})
        self.assertEqual(find_candidate(cands, 2).path, Path("/b"))
        self.assertIsNone(find_candidate(cands, 9))

    def test_exists_quietly(self):
        with TemporaryDirectory() as td:
            self.assertTrue(exists_quietly(Path(td)))
            self.assertFalse(exists_quietly(Path(td) / "nope"))


if __name__ == "__main__":
    unittest.main()
