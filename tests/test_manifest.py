"""manifest 模块单元测试：roundtrip、原子保存、损坏容错。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from savesync.manifest import (FileEntry, LocalState, RemoteFile,
                               RemoteManifest, iso_to_ts, load_local_state,
                               save_local_state, ts_to_iso)


class TestRoundtrip(unittest.TestCase):
    def test_remote(self):
        m = RemoteManifest(
            game="g1", version=3, upload_time="2026-11-08T13:00:00+08:00",
            time_source="ntp", source_platform="linux",
            files=[RemoteFile("存档/第1.sav", "ab" * 32, 10,
                              "2026-11-08T12:00:00+08:00")])
        self.assertEqual(RemoteManifest.from_json(json.loads(json.dumps(m.to_json()))), m)

    def test_remote_bad(self):
        with self.assertRaises(ValueError):
            RemoteManifest.from_json({"game": "g"})

    def test_remote_mtime_optional(self):
        # 无 mtime 的环境：清单缺字段也能解析，mtime 视为未知
        m = RemoteManifest.from_json({
            "game": "g", "version": 1, "upload_time": "t",
            "time_source": "local", "source_platform": "linux",
            "files": [{"path": "a", "hash": "h", "size": 1}]})
        self.assertEqual(m.files[0].mtime, "")

    def test_local(self):
        s = LocalState(game="g1", version=2,
                       files={"a": FileEntry("h", 3, 1762000000.5)},
                       chosen_path=1, last_backend="nas")
        self.assertEqual(LocalState.from_json(json.loads(json.dumps(s.to_json()))), s)
        empty = LocalState(game="g1")
        self.assertEqual((empty.version, empty.files), (0, {}))

    def test_iso_ts(self):
        t = 1762000000.0
        self.assertAlmostEqual(iso_to_ts(ts_to_iso(t)), t, places=0)


class TestLocalStateIO(unittest.TestCase):
    def test_save_load_missing_corrupt(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            self.assertIsNone(load_local_state(d, "g1"))
            s = LocalState(game="g1", version=1)
            save_local_state(d, s)
            self.assertEqual(load_local_state(d, "g1").version, 1)
            (d / "g1.json").write_text("{broken", encoding="utf-8")
            self.assertIsNone(load_local_state(d, "g1"))
            # 损坏后保存可恢复
            save_local_state(d, LocalState(game="g1", version=9,
                                           chosen_path=2, last_backend="zip"))
            got = load_local_state(d, "g1")
            self.assertEqual(got.version, 9)
            self.assertEqual(got.chosen_path, 2)
            self.assertEqual(got.last_backend, "zip")


if __name__ == "__main__":
    unittest.main()
