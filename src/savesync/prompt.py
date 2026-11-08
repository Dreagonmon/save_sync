"""CLI 侧 Decision 作答（DESIGN.md §4.4/§9），仅被 cli.py 使用。

core 不交互；本模块把 Plan.decisions 变成终端问答或 NeedsInputError。
未来 WebUI 自行渲染 decisions 为表单，不经过本模块。
"""
from __future__ import annotations

from .errors import NeedsInputError
from .sync import Answers, Plan


def resolve_decisions(plan: Plan, interactive: bool) -> Answers:
    """交互模式逐条询问返回 answers；非交互模式存在未答决策 → NeedsInputError。

    有 default 的决策自动取默认项（如 path 决策沿用状态清单 chosen_path）。
    """
    raise NotImplementedError


def is_interactive(stdin=None) -> bool:
    """stdin 非 tty 即非交互（cron/Tasker 场景）。"""
    raise NotImplementedError
