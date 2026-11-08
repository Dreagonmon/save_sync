"""sync 状态机测试：§4 两张表的全部情形 + 自愈 + 并发。"""
from __future__ import annotations

import hashlib
import io
import os
import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from savesync import sync as S
from savesync.backends.dirfs import DirBackend
from savesync.config import BackendConfig, GameConfig
from savesync.errors import (ConcurrentWriteError, NeedsInputError,
                             NoBackupError, NoSourcePathError)
from savesync.manifest import (LocalState, RemoteFile, RemoteManifest,
                               iso_to_ts, ts_to_iso)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class SyncCase(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        td = Path(self._td.name)
        self.save = td / "save"
        self.save.mkdir()
        self.b = DirBackend(BackendConfig(id="bk", type="dir", path=td / "bk"))
        mock.patch.object(S, "now_with_source",
                          lambda: (datetime.now().astimezone(), "local")
                          ).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        self._td.cleanup()

    def game(self, paths=None, ignore=None):
        if paths is None:
            paths = {str(self.save): "主"}
        return GameConfig(id="g1", name="测试",
                          ignore=ignore or [], paths={"linux": paths})

    def w(self, rel, data):
        p = self.save / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def bump_remote(self, version):
        m = self.b.read_manifest("g1")
        m.version = version
        self.b.write_manifest(m)
        return m

    def do_backup(self, g, state, answers=None, save_root=None):
        plan = S.plan_backup(g, self.b, state, platform="linux",
                             save_root=save_root)
        rep, st = S.execute_backup(plan, dict(answers or {}), g, self.b,
                                   state, backend_id="bk")
        return plan, rep, st


class TestBackup(SyncCase):
    def test_first_full(self):
        self.w("a.txt", b"1")
        self.w("sub/b.txt", b"2")
        plan, rep, st = self.do_backup(self.game(), None)
        self.assertEqual(plan.upload, ["a.txt", "sub/b.txt"])
        self.assertEqual(plan.new_version, 1)
        self.assertTrue(rep.ok and rep.uploaded == 2)
        self.assertEqual(self.b.read_manifest("g1").version, 1)
        self.assertEqual(st.version, 1)
        self.assertEqual(b"".join(self.b.get("g1", "sub/b.txt")), b"2")

    def test_no_change_skipped(self):
        self.w("a.txt", b"1")
        g = self.game()
        _, _, st = self.do_backup(g, None)
        plan = S.plan_backup(g, self.b, st, platform="linux")
        self.assertTrue(plan.skipped)
        rep, st2 = S.execute_backup(plan, {}, g, self.b, st)
        self.assertTrue(rep.skipped)
        self.assertIsNone(st2)

    def test_incremental_and_delete(self):
        self.w("a.txt", b"1")
        self.w("sub/b.txt", b"2")
        g = self.game()
        _, _, st = self.do_backup(g, None)
        (self.save / "a.txt").write_bytes(b"changed!")
        os.remove(self.save / "sub/b.txt")
        self.w("c.txt", b"3")
        plan, rep, st2 = self.do_backup(g, st)
        self.assertEqual(plan.upload, ["a.txt", "c.txt"])
        self.assertEqual(plan.delete, ["sub/b.txt"])
        self.assertEqual(plan.new_version, 2)
        self.assertEqual(self.b.read_manifest("g1").version, 2)
        self.assertNotIn("sub/b.txt", self.b.list_files("g1"))

    def test_remote_ahead(self):
        self.w("a.txt", b"1")
        g = self.game()
        _, _, st = self.do_backup(g, None)
        self.bump_remote(5)
        (self.save / "a.txt").write_bytes(b"local-new")  # 有差集才构成冲突
        plan = S.plan_backup(g, self.b, st, platform="linux")
        self.assertEqual([d.id for d in plan.decisions], [S.DECISION_REMOTE_AHEAD])
        # 中止
        rep, st2 = S.execute_backup(plan, {S.DECISION_REMOTE_AHEAD: 1}, g, self.b, st)
        self.assertFalse(rep.ok)
        self.assertEqual(self.b.read_manifest("g1").version, 5)
        # 覆盖 → max+1 = 6
        plan, rep, st2 = self.do_backup(g, st, {S.DECISION_REMOTE_AHEAD: 0})
        self.assertEqual(plan.new_version, 6)
        self.assertEqual(self.b.read_manifest("g1").version, 6)

    def test_remote_behind(self):
        self.w("a.txt", b"1")
        g = self.game()
        _, _, st = self.do_backup(g, None)
        self.bump_remote(0)  # 远端被带外回退
        (self.save / "a.txt").write_bytes(b"x")
        plan = S.plan_backup(g, self.b, st, platform="linux")
        self.assertEqual([d.id for d in plan.decisions], [S.DECISION_REMOTE_BEHIND])
        plan, rep, st2 = self.do_backup(g, st, {S.DECISION_REMOTE_BEHIND: 0})
        self.assertEqual(plan.new_version, 2)  # L+1

    def test_no_diff_align_state(self):
        """崩溃自愈：远端已 v2、本地状态 v1、内容一致 → 仅对齐状态。"""
        self.w("a.txt", b"1")
        g = self.game()
        _, _, st = self.do_backup(g, None)
        self.bump_remote(2)
        plan = S.plan_backup(g, self.b, st, platform="linux")
        self.assertFalse(plan.skipped)
        self.assertEqual((plan.upload, plan.delete, plan.new_version),
                         ([], [], 2))
        rep, st2 = S.execute_backup(plan, {}, g, self.b, st)
        self.assertEqual(rep.uploaded, 0)
        self.assertEqual(st2.version, 2)

    def test_remote_manifest_deleted_full(self):
        self.w("a.txt", b"1")
        g = self.game()
        _, _, st = self.do_backup(g, None)
        os.remove(self.b.root / "g1.json")
        plan, rep, st2 = self.do_backup(g, st)
        self.assertEqual(plan.upload, ["a.txt"])  # 全量重传
        self.assertEqual(plan.new_version, 2)

    def test_zero_path(self):
        g = self.game({str(self.save / "nope"): "缺"})
        with self.assertRaises(NoSourcePathError):
            S.plan_backup(g, self.b, None, platform="linux")
        plan = S.plan_backup(g, self.b, None, platform="linux", explicit=False)
        self.assertTrue(plan.skipped)

    def test_multi_path_decision(self):
        s2 = self.save.parent / "save2"
        s2.mkdir()
        g = self.game({str(self.save): "A", str(s2): "B"})
        self.w("a.txt", b"1")
        plan = S.plan_backup(g, self.b, None, platform="linux")
        d = plan.decisions[0]
        self.assertEqual(d.id, S.DECISION_PATH)
        self.assertIsNone(d.default)
        with self.assertRaises(NeedsInputError):
            S.execute_backup(plan, {}, g, self.b, None)
        # 记住 chosen_path=2 → 默认选 B
        plan = S.plan_backup(g, self.b, None, platform="linux", chosen_path=2)
        self.assertEqual(plan.decisions[0].default, 1)

    def test_concurrent_writer(self):
        self.w("a.txt", b"1")
        g = self.game()

        class Racy(DirBackend):
            def write_manifest(self, m):
                super().write_manifest(m)
                # 模拟并发写入者随后又推了一版
                super().write_manifest(RemoteManifest(
                    game=m.game, version=m.version + 1,
                    upload_time=m.upload_time, time_source=m.time_source,
                    source_platform=m.source_platform, files=m.files))

        racy = Racy(BackendConfig(id="bk", type="dir", path=self.b.root))
        plan = S.plan_backup(g, racy, None, platform="linux")
        with self.assertRaises(ConcurrentWriteError):
            S.execute_backup(plan, {}, g, racy, None)


class TestRestore(SyncCase):
    def backup_once(self):
        self.w("a.txt", b"v1")
        g = self.game()
        _, _, st = SyncCase.do_backup(self, g, None)
        return g, st

    def push_remote_v2(self):
        """模拟他设备上传 v2。"""
        self.b.put("g1", "a.txt", io.BytesIO(b"v2"), 2)
        m = self.b.read_manifest("g1")
        m.files = [RemoteFile("a.txt", sha(b"v2"), 2, ts_to_iso(time.time()))]
        m.version = 2
        self.b.write_manifest(m)

    def test_no_backup(self):
        with self.assertRaises(NoBackupError):
            S.plan_restore(self.game(), self.b, None, platform="linux")

    def test_restore_newer_clean(self):
        g, st = self.backup_once()
        self.push_remote_v2()
        plan = S.plan_restore(g, self.b, st, platform="linux")
        self.assertEqual(plan.decisions, [])  # 本地干净 → 直接恢复
        rep, st2 = S.execute_restore(plan, {}, g, self.b, st)
        self.assertEqual((self.save / "a.txt").read_bytes(), b"v2")
        self.assertEqual(st2.version, 2)

    def test_restore_newer_local_dirty(self):
        g, st = self.backup_once()
        self.push_remote_v2()
        (self.save / "a.txt").write_bytes(b"unsaved")  # 本地未备份修改
        plan = S.plan_restore(g, self.b, st, platform="linux")
        self.assertEqual([d.id for d in plan.decisions],
                         [S.DECISION_RESTORE_LOCAL_DIRTY])
        rep, _ = S.execute_restore(plan, {S.DECISION_RESTORE_LOCAL_DIRTY: 1},
                                   g, self.b, st)
        self.assertFalse(rep.ok)  # 中止，本地不动
        self.assertEqual((self.save / "a.txt").read_bytes(), b"unsaved")
        rep, st2 = S.execute_restore(plan, {S.DECISION_RESTORE_LOCAL_DIRTY: 0},
                                     g, self.b, st)
        self.assertEqual((self.save / "a.txt").read_bytes(), b"v2")
        self.assertEqual(st2.version, 2)

    def test_restore_same_version_dirty(self):
        g, st = self.backup_once()
        (self.save / "a.txt").write_bytes(b"local-edit")
        plan = S.plan_restore(g, self.b, st, platform="linux")
        self.assertEqual([d.id for d in plan.decisions],
                         [S.DECISION_RESTORE_OVERWRITE])
        rep, st2 = S.execute_restore(plan, {S.DECISION_RESTORE_OVERWRITE: 0},
                                     g, self.b, st)
        self.assertEqual((self.save / "a.txt").read_bytes(), b"v1")
        self.assertEqual(st2.version, 1)

    def test_restore_keeps_extra_local(self):
        g, st = self.backup_once()
        (self.save / "a.txt").write_bytes(b"v1-modified")
        (self.save / "extra.txt").write_bytes(b"e")  # 远端没有
        plan = S.plan_restore(g, self.b, st, platform="linux")
        self.assertEqual(plan.keep_local, ["extra.txt"])
        rep, st2 = S.execute_restore(plan, {S.DECISION_RESTORE_OVERWRITE: 0},
                                     g, self.b, st)
        self.assertTrue((self.save / "extra.txt").exists())  # 保留不删
        self.assertNotIn("extra.txt", st2.files)  # 不入状态 → 下次备份上传
        rep2, _ = S.execute_backup(
            S.plan_backup(g, self.b, st2, platform="linux"),
            {}, g, self.b, st2)
        self.assertEqual(rep2.uploaded, 1)

    def test_restore_path_decision_creates_dir(self):
        g, st = self.backup_once()
        p1, p2 = self.save.parent / "r1", self.save.parent / "r2"
        g2 = self.game({str(p1): "新设备1", str(p2): "新设备2"})
        plan = S.plan_restore(g2, self.b, None, platform="linux")
        d = plan.decisions[0]
        self.assertEqual(d.id, S.DECISION_PATH)
        self.assertEqual(d.default, 0)  # 0 条存在 → 默认第一条
        target = plan.path_options[1]
        plan2 = S.plan_restore(g2, self.b, None, platform="linux",
                               save_root=target)
        rep, st2 = S.execute_restore(plan2, {}, g2, self.b, None)
        self.assertEqual((target / "a.txt").read_bytes(), b"v1")
        self.assertEqual(st2.chosen_path, 2)

    def test_restore_without_remote_mtime(self):
        """无 mtime 环境：清单缺 mtime 仍可恢复，状态记 0.0（不信任缓存）。"""
        g, st = self.backup_once()
        self.b.put("g1", "a.txt", io.BytesIO(b"v2"), 2)
        m = self.b.read_manifest("g1")
        m.files = [RemoteFile("a.txt", sha(b"v2"), 2, "")]
        m.version = 2
        self.b.write_manifest(m)
        plan = S.plan_restore(g, self.b, st, platform="linux")
        rep, st2 = S.execute_restore(plan, {}, g, self.b, st)
        self.assertEqual((self.save / "a.txt").read_bytes(), b"v2")
        self.assertEqual(st2.files["a.txt"].mtime, 0.0)
        # 下次备份重算 hash：差集仍为空，且状态以实际 stat 自愈
        plan2 = S.plan_backup(g, self.b, st2, platform="linux")
        self.assertEqual((plan2.upload, plan2.delete), ([], []))
        # 内容改动后正常增量，状态 mtime 已是实际 stat → 再备份两级过滤命中
        (self.save / "a.txt").write_bytes(b"v3")
        plan3, rep3, st3 = self.do_backup(g, st2)
        self.assertEqual(plan3.upload, ["a.txt"])
        self.assertAlmostEqual(
            st3.files["a.txt"].mtime,
            (self.save / "a.txt").stat().st_mtime, places=2)
        plan4 = S.plan_backup(g, self.b, st3, platform="linux")
        self.assertTrue(plan4.skipped)

    def test_restore_mtime_restored(self):
        g, st = self.backup_once()
        self.push_remote_v2()
        plan = S.plan_restore(g, self.b, st, platform="linux")
        S.execute_restore(plan, {}, g, self.b, st)
        m = self.b.read_manifest("g1")
        self.assertAlmostEqual((self.save / "a.txt").stat().st_mtime,
                               iso_to_ts(m.files[0].mtime), places=0)


if __name__ == "__main__":
    unittest.main()
