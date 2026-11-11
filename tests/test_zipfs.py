"""zipfs 后端契约测试：与 dirfs 参照一致的行为 + zip 特有的合并/搬运语义。"""
from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from savesync.backends.zipfs import ZipBackend
from savesync.config import BackendConfig, GameConfig
from savesync.errors import BackendError
from savesync.manifest import RemoteFile, RemoteManifest


def manifest(game: str, version: int, files: list[str]) -> RemoteManifest:
    return RemoteManifest(
        game=game, version=version, upload_time="2026-11-08T13:00:00+08:00",
        time_source="local", source_platform="linux",
        files=[RemoteFile(p, f"h{i}", 1, "2026-11-08T12:00:00+08:00")
               for i, p in enumerate(files)])


class TestZipBackend(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.zip = self.root / "backup.zip"
        self.b = ZipBackend(BackendConfig(id="bk", type="zip", path=self.zip))

    def tearDown(self):
        self._td.cleanup()

    def commit(self, game: str, version: int, files: dict[str, bytes]):
        for rel, data in files.items():
            self.b.put(game, rel, io.BytesIO(data), len(data))
        self.b.write_manifest(manifest(game, version, list(files)))

    # ---- 与 dirfs 参照一致的契约行为 ----

    def test_put_get_list_delete(self):
        rel = "存档/第1章/options.txt"
        self.b.put("g1", rel, io.BytesIO(b"hello"), 5)
        self.assertEqual(b"".join(self.b.get("g1", rel)), b"hello")  # 读己之写
        self.assertIn(rel, self.b.list_files("g1"))
        self.assertEqual(self.b.list_files("g1")[rel].size, 5)
        self.b.delete("g1", rel)
        self.assertNotIn(rel, self.b.list_files("g1"))
        self.b.delete("g1", rel)  # 幂等
        with self.assertRaises(BackendError):
            b"".join(self.b.get("g1", rel))

    def test_manifest_roundtrip_missing(self):
        self.assertIsNone(self.b.read_manifest("g1"))
        self.b.write_manifest(manifest("g1", 1, ["a"]))
        m = self.b.read_manifest("g1")
        self.assertEqual((m.version, m.files[0].path), (1, "a"))
        self.assertIsNone(self.b.read_manifest("g2"))  # 同包其他游戏不受影响

    def test_get_missing_raises(self):
        self.b.write_manifest(manifest("g1", 1, []))
        with self.assertRaises(BackendError):
            b"".join(self.b.get("g1", "nope.dat"))

    def test_corrupt_zip(self):
        self.zip.write_bytes(b"not a zip")
        with self.assertRaises(BackendError):
            self.b.read_manifest("g1")
        with self.assertRaises(BackendError):
            self.b.write_manifest(manifest("g1", 1, []))

    # ---- zip 特有语义 ----

    def test_incremental_carryover(self):
        """未上传的条目从旧包搬运；差集删除的跳过；新数据替换。"""
        self.commit("g1", 1, {"keep.dat": b"k", "mod.dat": b"v1",
                              "gone.dat": b"x"})
        self.b.put("g1", "mod.dat", io.BytesIO(b"v2"), 2)
        self.b.delete("g1", "gone.dat")
        self.b.write_manifest(manifest("g1", 2, ["keep.dat", "mod.dat"]))
        with zipfile.ZipFile(self.zip) as zf:
            self.assertEqual(sorted(zf.namelist()),
                             ["g1.json", "g1/keep.dat", "g1/mod.dat"])
            self.assertEqual(zf.read("g1/keep.dat"), b"k")
            self.assertEqual(zf.read("g1/mod.dat"), b"v2")
        self.assertEqual(self.b.read_manifest("g1").version, 2)

    def test_multi_game_merge(self):
        """单包多游戏：提交 A 不动 B，反之亦然。"""
        self.commit("g1", 1, {"a.dat": b"a"})
        self.commit("g2", 1, {"b.dat": b"b"})
        self.b.put("g1", "a2.dat", io.BytesIO(b"a2"), 2)
        self.b.write_manifest(manifest("g1", 2, ["a.dat", "a2.dat"]))
        with zipfile.ZipFile(self.zip) as zf:
            self.assertEqual(sorted(zf.namelist()),
                             ["g1.json", "g1/a.dat", "g1/a2.dat",
                              "g2.json", "g2/b.dat"])
            self.assertEqual(zf.read("g2/b.dat"), b"b")
        self.assertEqual(self.b.read_manifest("g2").version, 1)

    def test_no_temp_leftovers(self):
        self.commit("g1", 1, {"a.dat": b"a"})
        self.assertEqual([p.name for p in self.root.iterdir()],
                         ["backup.zip"])

    def test_deflate_and_utf8_names(self):
        self.commit("g1", 1, {"存档/第一章.sav": b"z" * 200})
        with zipfile.ZipFile(self.zip) as zf:
            info = zf.getinfo("g1/存档/第一章.sav")
            self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
            self.assertTrue(info.flag_bits & 0x800)  # UTF-8 标志
            self.assertEqual(zf.read("g1/存档/第一章.sav"), b"z" * 200)

    def test_double_put_last_wins(self):
        """put 直写新包：同名重复 put 以最后一次为准（中央目录后写覆盖）。"""
        self.b.put("g1", "a.dat", io.BytesIO(b"old"), 3)
        self.b.put("g1", "a.dat", io.BytesIO(b"new"), 3)
        self.assertEqual(b"".join(self.b.get("g1", "a.dat")), b"new")
        self.b.write_manifest(manifest("g1", 1, ["a.dat"]))
        with zipfile.ZipFile(self.zip) as zf:
            self.assertEqual(zf.read("g1/a.dat"), b"new")
        self.assertEqual([p.name for p in self.root.iterdir()],
                         ["backup.zip"])

    def test_interleaved_games_committed_together(self):
        """同实例交错写两游戏：提交 A 时 B 数据随包入库（清单未动，
        属设计允许的孤儿中间态）；提交 B 后完全一致。"""
        self.b.put("g1", "a.dat", io.BytesIO(b"a"), 1)
        self.b.put("g2", "b.dat", io.BytesIO(b"b"), 1)
        self.b.write_manifest(manifest("g1", 1, ["a.dat"]))
        with zipfile.ZipFile(self.zip) as zf:
            self.assertEqual(sorted(zf.namelist()),
                             ["g1.json", "g1/a.dat", "g2/b.dat"])
            self.assertIsNone(self.b.read_manifest("g2"))  # B 清单未提交
        self.b.write_manifest(manifest("g2", 1, ["b.dat"]))
        with zipfile.ZipFile(self.zip) as zf:
            self.assertEqual(sorted(zf.namelist()),
                             ["g1.json", "g1/a.dat", "g2.json", "g2/b.dat"])

    def test_crash_leftover_tmp_cleaned(self):
        """上次崩溃残留的 tmp 在新会话首次写入前被清理，不污染新包。"""
        (self.root / "backup.zip.savesync-tmp").write_bytes(b"garbage")
        self.commit("g1", 1, {"a.dat": b"a"})
        self.assertEqual([p.name for p in self.root.iterdir()],
                         ["backup.zip"])
        with zipfile.ZipFile(self.zip) as zf:
            self.assertEqual(sorted(zf.namelist()), ["g1.json", "g1/a.dat"])


class TestZipSync(unittest.TestCase):
    """状态机直接跑在 zip 后端上（不依赖 dir 参照）。"""

    def setUp(self):
        from datetime import datetime
        from unittest import mock
        from savesync import sync as S
        self.S = S
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.save = self.root / "saves"
        self.save.mkdir()
        self.b = ZipBackend(BackendConfig(id="z", type="zip",
                                          path=self.root / "b.zip"))
        mock.patch.object(S, "now_with_source",
                          lambda: (datetime.now().astimezone(), "local")
                          ).start()
        self.addCleanup(mock.patch.stopall)
        self.g = GameConfig(id="g1", name="G", ignore=[],
                            paths={"linux": {str(self.save): "主"}})

    def tearDown(self):
        self._td.cleanup()

    def test_backup_restore_cycle(self):
        S = self.S
        (self.save / "a.dat").write_bytes(b"v1")
        st = None
        _, st = S.execute_backup(
            S.plan_backup(self.g, self.b, st, platform="linux"),
            {}, self.g, self.b, st, backend_id="z")
        self.assertEqual(st.version, 1)
        # 换机器恢复：目标目录不存在，选路径 1 新建
        target = self.root / "other"
        g2 = GameConfig(id="g1", name="G", ignore=[],
                        paths={"linux": {str(target): "新机器"}})
        plan = S.plan_restore(g2, self.b, None, platform="linux")
        self.assertEqual(plan.save_root, target)  # 唯一候选 → 自动选中创建
        rep, st2 = S.execute_restore(plan, {}, g2, self.b, None,
                                     backend_id="z")
        self.assertEqual((target / "a.dat").read_bytes(), b"v1")
        self.assertEqual(st2.version, 1)
        # zip 内布局：数据 + 清单
        with zipfile.ZipFile(self.root / "b.zip") as zf:
            self.assertEqual(sorted(zf.namelist()), ["g1.json", "g1/a.dat"])


if __name__ == "__main__":
    unittest.main()
