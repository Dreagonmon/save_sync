"""backup / restore 状态机（DESIGN.md §4），两阶段执行模型（§4.4）。

纯逻辑层：不 print、不读 stdin、无全局状态。人工确认以 Decision 数据
返回给调用方（CLI / 未来 WebUI 收集 answers），再 execute。

版本号推进：新版本 = max(本地 L, 远端 R) + 1，单调不回退。
提交点：备份先传/删数据、最后写远端清单；恢复先写本地文件、最后写状态清单。
备份写完远端清单后重读校验 version，防并发写入者（ConcurrentWriteError）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from .backends.base import Backend
from .config import GameConfig
from .manifest import LocalState
from .scanner import ScannedFile

# Decision.id 约定（core 产生，交互层按 id 渲染控件/映射参数）
DECISION_PATH = "path"                        # 多条候选路径，选择编号
DECISION_REMOTE_AHEAD = "remote_ahead"        # 备份：R>L 他处上传过，是否覆盖远端
DECISION_REMOTE_BEHIND = "remote_behind"      # 备份：R<L 远端被带外回退，是否仍覆盖
DECISION_RESTORE_OVERWRITE = "restore_overwrite"  # 恢复：R==L 本地有改动 / R<L，是否覆盖本地
DECISION_RESTORE_LOCAL_DIRTY = "restore_local_dirty"  # 恢复：R>L 但本地有未备份修改

Answers = dict[str, int]          # Decision.id -> 选项下标（0 基）
Progress = Callable[[str], None]  # 可选进度回调（WebUI 用），参数为人读文本


@dataclass
class Decision:
    id: str
    question: str
    options: list[str]
    default: int | None = None
    hint: str | None = None       # 非交互建议参数，如 "--path 2" / "先执行 backup"


@dataclass
class Plan:
    game: str
    action: Literal["backup", "restore"]
    save_root: Path | None = None
    upload: list[str] = field(default_factory=list)        # 新增或 hash 变化
    delete: list[str] = field(default_factory=list)        # 远端目录中本地不存在
    overwrite_local: list[str] = field(default_factory=list)
    keep_local: list[str] = field(default_factory=list)    # 恢复：本地多余，保留不删
    version_before: tuple[int, int | None] = (0, None)     # (L, R)
    new_version: int | None = None
    decisions: list[Decision] = field(default_factory=list)
    skipped: bool = False         # all 隐式包含且 0 匹配 → 跳过，不算失败
    skip_reason: str | None = None
    # plan 内部缓存扫描结果供 execute 复用；重 plan 时刷新，不参与序列化
    _scan: list[ScannedFile] | None = field(default=None, repr=False)


@dataclass
class SyncReport:
    game: str
    action: Literal["backup", "restore"]
    ok: bool                      # 用户中止 → ok=False, reason="aborted"（非异常）
    skipped: bool = False
    reason: str | None = None
    uploaded: int = 0
    deleted: int = 0
    overwritten: int = 0
    kept_local: int = 0
    version_before: int | None = None
    version_after: int | None = None


def plan_backup(
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    platform: str,
    save_root: Path | None = None,
) -> Plan:
    """§4.1 状态机的只读部分。

    - save_root 未显式给出且多条候选 → decisions 含 DECISION_PATH，
      差集为空，调用方作答后带 save_root 重新 plan。
    - 0 条匹配：显式点名 → NoSourcePathError；隐式(all) → skipped=True。
    """
    raise NotImplementedError


def plan_restore(
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    platform: str,
    save_root: Path | None = None,
) -> Plan:
    """§4.2 状态机的只读部分。远端无清单 → NoBackupError。"""
    raise NotImplementedError


def execute_backup(
    plan: Plan,
    answers: Answers,
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> tuple[SyncReport, LocalState | None]:
    """按 §4.1 执行。answers 未覆盖的决策选项为「中止」时返回 aborted 报告。

    返回 (报告, 新本地状态清单)；调用方在提交点后落盘 state。
    未答决策 → NeedsInputError。
    """
    raise NotImplementedError


def execute_restore(
    plan: Plan,
    answers: Answers,
    game: GameConfig,
    backend: Backend,
    state: LocalState | None,
    *,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> tuple[SyncReport, LocalState | None]:
    """按 §4.2 执行。恢复不删除本地多余文件。"""
    raise NotImplementedError
