"""prompt 模块单元测试：Decision 作答的三条路径。"""
from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest import mock

from savesync.errors import NeedsInputError
from savesync import prompt
from savesync.sync import Decision


def d(default=None, n=2):
    return Decision(id="x", question="问题？",
                    options=[f"选项{i}" for i in range(1, n + 1)],
                    default=default, hint="--flag")


class TestIsInteractive(unittest.TestCase):
    def test_tty_true(self):
        self.assertTrue(prompt.is_interactive(
            SimpleNamespace(isatty=lambda: True)))

    def test_pipe_false(self):
        self.assertFalse(prompt.is_interactive(io.StringIO("1\n")))

    def test_broken_stdin_false(self):
        self.assertFalse(prompt.is_interactive(
            SimpleNamespace(isatty=lambda: (_ for _ in ()).throw(OSError))))


class TestAskDecision(unittest.TestCase):
    def test_default_auto(self):
        with mock.patch("builtins.input",
                        side_effect=AssertionError("不应提问")):
            self.assertEqual(prompt.ask_decision(d(default=1), True), 1)

    def test_non_interactive_no_default_raises(self):
        with self.assertRaises(NeedsInputError) as cm:
            prompt.ask_decision(d(), False)
        self.assertEqual(cm.exception.decisions[0].id, "x")

    def test_interactive_retries_invalid(self):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=["x", "9", "2"]), \
                mock.patch("sys.stdout", out):
            self.assertEqual(prompt.ask_decision(d(), True), 1)  # 2 → idx 1
        text = out.getvalue()
        self.assertIn("无效输入", text)
        self.assertIn("[1] 选项1", text)

    def test_interactive_eof_raises(self):
        with mock.patch("builtins.input", side_effect=EOFError), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(NeedsInputError):
                prompt.ask_decision(d(), True)


class TestResolveDecisions(unittest.TestCase):
    def plan(self, decisions):
        return SimpleNamespace(decisions=decisions)

    def test_defaults_auto_pending_interactive(self):
        plan = self.plan([d(default=0), d()])
        with mock.patch("builtins.input", side_effect=["1"]), \
                mock.patch("sys.stdout", io.StringIO()):
            answers = prompt.resolve_decisions(plan, True)
        self.assertEqual(answers, {"x": 0})  # 同 id 后者覆盖前者作答

    def test_pending_non_interactive_raises_with_all_pending(self):
        d1 = Decision(id="a", question="q", options=["o"], default=None,
                      hint="")
        d2 = Decision(id="b", question="q", options=["o"], default=0, hint="")
        d3 = Decision(id="c", question="q", options=["o"], default=None,
                      hint="")
        with self.assertRaises(NeedsInputError) as cm:
            prompt.resolve_decisions(self.plan([d1, d2, d3]), False)
        self.assertEqual([x.id for x in cm.exception.decisions], ["a", "c"])

    def test_all_defaults_no_interaction(self):
        plan = self.plan([d(default=1)])
        with mock.patch("builtins.input",
                        side_effect=AssertionError("不应提问")):
            self.assertEqual(prompt.resolve_decisions(plan, False), {"x": 1})


class TestAskPassword(unittest.TestCase):
    def test_non_interactive_raises_with_hint(self):
        with self.assertRaises(NeedsInputError) as cm:
            prompt.ask_password("nas", "https://x/dav", False)
        d = cm.exception.decisions[0]
        self.assertEqual(d.id, "password")
        self.assertIn("password_env", d.hint)

    def test_interactive_getpass_retries_empty(self):
        out = io.StringIO()
        with mock.patch("getpass.getpass", side_effect=["", "s3cret"]) as gp, \
                mock.patch("sys.stdout", out):
            self.assertEqual(
                prompt.ask_password("nas", "https://x/dav", True), "s3cret")
        self.assertEqual(gp.call_count, 2)
        self.assertIn("不能为空", out.getvalue())


if __name__ == "__main__":
    unittest.main()
