"""CLI 端到端测试：真实目录 + dir 后端跑完整 backup/restore 链路。"""
from __future__ import annotations

import io
import contextlib
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from savesync import cli, sync as S


class TestCli(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name) / "app"
        (self.root / "config").mkdir(parents=True)
        self.backend_dir = self.root / "remote"
        self.save = self.root / "saves" / "game"
        self.save.mkdir(parents=True)
        self.write_config()
        mock.patch.object(S, "now_with_source",
                          lambda: (datetime.now().astimezone(), "local")
                          ).start()
        mock.patch.object(cli, "is_interactive", lambda: False).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        mock.patch.stopall()
        self._td.cleanup()

    def write_config(self, extra_game: str | None = None):
        (self.root / "config" / "s-local.toml").write_text(
            f'type = "dir"\npath = "{self.backend_dir}"\n', encoding="utf-8")
        (self.root / "config" / "g-test.toml").write_text(
            'name = "测试游戏"\nignore = ["*.bak"]\n'
            f'[paths.linux]\n"{self.save}" = "主存档"\n', encoding="utf-8")
        if extra_game:
            (self.root / "config" / f"g-{extra_game}.toml").write_text(
                f'name = "缺路径"\n'
                f'[paths.linux]\n"{self.root / "nowhere"}" = "缺"\n',
                encoding="utf-8")

    def run_cli(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cli.main(["--root", str(self.root), *argv])
        return rc, buf.getvalue() + err.getvalue()

    def w(self, rel, data):
        (self.save / rel).write_bytes(data)

    def test_games_and_status(self):
        rc, out = self.run_cli("games")
        self.assertEqual(rc, 0)
        self.assertIn("test", out)
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("本地 v0", out)

    def test_backup_restore_flow(self):
        self.w("a.sav", b"v1")
        self.w("junk.bak", b"ignore me")
        rc, out = self.run_cli("backup")
        self.assertEqual(rc, 0, out)
        self.assertIn("上传 1", out)  # junk.bak 被 ignore
        self.assertTrue((self.backend_dir / "test" / "a.sav").exists())
        self.assertTrue((self.root / "data" / "test.json").exists())
        # 无变化 → 跳过
        rc, out = self.run_cli("backup")
        self.assertEqual(rc, 0)
        self.assertIn("跳过", out)
        # dry-run 不写
        self.w("a.sav", b"v2")
        rc, out = self.run_cli("backup", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("上传 1", out)
        self.assertEqual((self.backend_dir / "test" / "a.sav").read_bytes(), b"v1")
        # 真备份
        rc, out = self.run_cli("backup")
        self.assertEqual((self.backend_dir / "test" / "a.sav").read_bytes(), b"v2")
        # 本地再改 → 非交互 restore 需要人工输入 → 退出码 3
        self.w("a.sav", b"local-unsaved")
        rc, out = self.run_cli("restore")
        self.assertEqual(rc, 3)
        self.assertIn("覆盖", out)
        # 交互作答「用远端覆盖本地」
        with mock.patch.object(cli, "is_interactive", lambda: True), \
                mock.patch("builtins.input", lambda *a: "1"):
            rc, out = self.run_cli("restore")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.save / "a.sav").read_bytes(), b"v2")

    def test_unknown_game(self):
        rc, _ = self.run_cli("backup", "nope")
        self.assertEqual(rc, 1)

    def test_all_skips_missing_path_but_named_errors(self):
        self.write_config(extra_game="missing")
        self.w("a.sav", b"1")
        rc, out = self.run_cli("backup")  # all：missing 隐式跳过
        self.assertEqual(rc, 0, out)
        self.assertIn("跳过", out)
        rc, out = self.run_cli("backup", "missing")  # 点名 → 报错
        self.assertEqual(rc, 1)
        self.assertIn("无已存在的存档路径", out)

    def test_path_flag_multi(self):
        save2 = self.root / "saves" / "game2"
        save2.mkdir(parents=True)
        (self.root / "config" / "g-test.toml").write_text(
            'name = "测试游戏"\n'
            f'[paths.linux]\n"{self.save}" = "A"\n"{save2}" = "B"\n',
            encoding="utf-8")
        (save2 / "b.sav").write_bytes(b"from-B")
        rc, out = self.run_cli("backup", "--path", "2")
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.backend_dir / "test" / "b.sav").exists())
        # 非法编号
        rc, out = self.run_cli("backup", "--path", "9")
        self.assertEqual(rc, 1)

    def test_state_file_shape(self):
        import json
        state_file = self.root / "data" / "test.json"
        for i in (1, 2, 3):
            self.w("a.sav", f"v{i}".encode())
            rc, _ = self.run_cli("backup")
            self.assertEqual(rc, 0)
        d = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(d["version"], 3)      # 唯一同步点，无历史
        self.assertNotIn("history", d)
        self.assertEqual(d["last_backend"], "local")
        self.assertIn("a.sav", d["files"])
        self.assertEqual(d["files"]["a.sav"]["size"], 2)

    def test_bad_config(self):
        (self.root / "config" / "g-test.toml").write_text(
            'name = "x"\n'
            f'[paths.android]\n"{self.save}" = "主"\n', encoding="utf-8")
        rc, out = self.run_cli("backup")
        self.assertEqual(rc, 1)
        self.assertIn("android", out)

    def test_backend_selection(self):
        self.w("a.sav", b"1")
        # 两个后端、非交互、未指定 → rc 3 并提示 --backend
        (self.root / "config" / "s-nas.toml").write_text(
            f'type = "dir"\npath = "{self.root / "remote2"}"\n', encoding="utf-8")
        rc, out = self.run_cli("backup")
        self.assertEqual(rc, 3)
        self.assertIn("--backend", out)
        # --backend 指定生效
        rc, out = self.run_cli("backup", "--backend", "nas")
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.root / "remote2" / "test" / "a.sav").exists())
        # last_backend=nas 成为默认 → 非交互也可跑
        rc, out = self.run_cli("backup", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("nas", out)
        # 未知后端
        rc, out = self.run_cli("backup", "--backend", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("nope", out)


if __name__ == "__main__":
    unittest.main()
