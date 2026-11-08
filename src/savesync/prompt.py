"""CLI 侧 Decision 作答（DESIGN.md §4.4/§9），仅被 cli.py 使用。

core 不交互；本模块把 Plan.decisions 变成终端问答或 NeedsInputError。
未来 WebUI 自行渲染 decisions 为表单，不经过本模块。
"""
from __future__ import annotations

import sys

from .errors import NeedsInputError
from .sync import Answers, Decision, Plan


def is_interactive(stdin=None) -> bool:
    """stdin 非 tty 即非交互（cron/Tasker 场景）。"""
    stdin = stdin if stdin is not None else sys.stdin
    try:
        return bool(stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def ask_decision(d: Decision, interactive: bool) -> int:
    """单个决策：默认项自动作答；交互终端问答；非交互无默认 → NeedsInputError。"""
    if d.default is not None:
        return d.default
    if not interactive:
        raise NeedsInputError([d])
    print(f"\n{d.question}")
    for i, opt in enumerate(d.options, 1):
        print(f"  [{i}] {opt}")
    while True:
        try:
            raw = input(f"选择 [1-{len(d.options)}]: ").strip()
        except EOFError:
            raise NeedsInputError([d]) from None
        if raw.isdigit() and 1 <= int(raw) <= len(d.options):
            return int(raw) - 1
        print("无效输入，请重新选择")


def resolve_decisions(plan: Plan, interactive: bool) -> Answers:
    """交互模式逐条询问返回 answers；非交互模式存在未答决策 → NeedsInputError。

    有 default 的决策自动取默认项（如 path 决策沿用状态清单 chosen_path）。
    """
    answers: Answers = {}
    pending = []
    for d in plan.decisions:
        if d.default is not None:
            answers[d.id] = d.default
        else:
            pending.append(d)
    if pending and not interactive:
        raise NeedsInputError(pending)
    for d in pending:
        answers[d.id] = ask_decision(d, interactive)
    return answers
