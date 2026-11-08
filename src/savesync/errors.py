"""core 层类型化异常体系（DESIGN.md §4.4/§9）。

交互层（cli / 未来 webui）按类型映射为打印、表单或退出码；
core 内部禁止 print/input，一切异常经此体系上浮。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sync import Decision


class SaveSyncError(Exception):
    """所有可预期错误的基类。"""


class ConfigError(SaveSyncError):
    """配置缺失/非法。"""


class NeedsInputError(SaveSyncError):
    """非交互模式存在未作答决策。携带原决策供渲染问题与建议参数。"""

    def __init__(self, decisions: list["Decision"]):
        self.decisions = decisions
        super().__init__("; ".join(d.question for d in decisions))


class NoSourcePathError(SaveSyncError):
    """备份时显式点名的游戏在当前平台 0 条匹配路径。"""


class NoBackupError(SaveSyncError):
    """恢复时远端无清单，无备份可恢复。"""


class BackendError(SaveSyncError):
    """后端 IO 失败（网络/权限/损坏），message 含后端 id。"""


class ConcurrentWriteError(SaveSyncError):
    """备份提交后重读远端清单，version 与预期不符：存在并发写入者。"""
