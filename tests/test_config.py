"""config 模块单元测试：s-*/g-* 加载、id 前缀、校验错误。"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from savesync.config import (load_backends, load_games, require_backends)
from savesync.errors import ConfigError


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def w(self, name: str, text: str) -> None:
        (self.root / name).write_text(text, encoding="utf-8")


class TestBackends(ConfigCase):
    def test_all_types_and_id_prefix(self):
        self.w("s-local.toml", 'type = "dir"\npath = "/srv/bk"')
        self.w("s-pkg.toml", 'type = "zip"\npath = "/srv/bk.zip"')
        self.w("s-nas.toml", 'type = "webdav"\nurl = "https://nas/dav"\n'
                             'username = "me"\npassword = "pw"\n'
                             'password_env = "WD_PW"')
        bs = load_backends(self.root)
        self.assertEqual(sorted(bs), ["local", "nas", "pkg"])  # s- 前缀剥离
        self.assertEqual((bs["local"].type, str(bs["local"].path)),
                         ("dir", "/srv/bk"))
        self.assertEqual((bs["nas"].url, bs["nas"].username,
                          bs["nas"].password, bs["nas"].password_env),
                         ("https://nas/dav", "me", "pw", "WD_PW"))
        self.assertIsNone(bs["local"].password)  # 可缺省

    def test_unknown_type(self):
        self.w("s-x.toml", 'type = "ftp"\npath = "/x"')
        with self.assertRaises(ConfigError):
            load_backends(self.root)

    def test_missing_required_field(self):
        self.w("s-d.toml", 'type = "dir"')
        with self.assertRaises(ConfigError):
            load_backends(self.root)
        self.w("s-d.toml", 'type = "webdav"')
        with self.assertRaises(ConfigError):
            load_backends(self.root)

    def test_bad_toml(self):
        self.w("s-b.toml", "type = = =")
        with self.assertRaises(ConfigError):
            load_backends(self.root)

    def test_require_backends(self):
        with self.assertRaises(ConfigError):
            require_backends({})
        require_backends(load_backends(self.root) or {"a": None})


class TestGames(ConfigCase):
    MIN = 'name = "G"\n[paths.linux]\n"/save" = "主"\n'

    def test_full(self):
        self.w("g-stardew.toml",
               'name = "星露谷"\nignore = ["*.log", "cache/*"]\n'
               '[paths.linux]\n"/linux/save" = "主"\n"/alt" = "备用"\n'
               '[paths.windows]\n"C:/save" = "Win"\n')
        gs = load_games(self.root)
        g = gs["stardew"]  # g- 前缀剥离
        self.assertEqual(g.name, "星露谷")
        self.assertEqual(g.ignore, ["*.log", "cache/*"])
        self.assertEqual(len(g.paths["linux"]), 2)
        self.assertEqual(list(g.paths["windows"].values()), ["Win"])

    def test_name_defaults_to_id(self):
        self.w("g-foo.toml", '[paths.linux]\n"/s" = "主"\n')
        self.assertEqual(load_games(self.root)["foo"].name, "foo")

    def test_no_paths(self):
        self.w("g-p.toml", 'name = "P"')
        with self.assertRaises(ConfigError):
            load_games(self.root)

    def test_unknown_platform(self):
        self.w("g-p.toml", '[paths.android]\n"/s" = "主"\n')
        with self.assertRaises(ConfigError) as cm:
            load_games(self.root)
        self.assertIn("android", str(cm.exception))

    def test_empty_platform_entries(self):
        self.w("g-p.toml", "[paths.linux]\n")
        with self.assertRaises(ConfigError):
            load_games(self.root)

    def test_ignore_must_be_list(self):
        self.w("g-p.toml", 'ignore = "*.log"\n[paths.linux]\n"/s" = "主"\n')
        with self.assertRaises(ConfigError):
            load_games(self.root)


if __name__ == "__main__":
    unittest.main()
