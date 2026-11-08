"""平台判定与存档路径解析（DESIGN.md §7）。

- 展开 ~ / $VAR / %VAR%；
- 存在性检测：无权限一律视为不存在（Android 不特殊处理）；
- 平台键只有 linux/macos/windows，Android(Termux) 归 linux；
- 0/1/多条匹配的选择规则见 sync.plan_*，本模块只提供候选与过滤。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PLATFORMS = ("linux", "macos", "windows")  # 无独立 android：Android(Termux) 归 linux

_PCT_VAR = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


@dataclass(frozen=True)
class Candidate:
    index: int      # 1 基编号，对应 --path N
    path: Path
    note: str


def current_platform() -> str:
    """返回 PLATFORMS 之一。Android（ANDROID_ROOT / Termux 环境变量）返回 linux。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def expand(raw: str) -> Path:
    """~、$VAR、%VAR% 统一展开，跨平台写法均可。未定义变量原样保留。"""
    s = _PCT_VAR.sub(lambda m: os.environ.get(m.group(1), m.group(0)), raw)
    s = os.path.expandvars(s)
    return Path(os.path.expanduser(s))


def exists_quietly(p: Path) -> bool:
    """存在且可探测；PermissionError / OSError 一律 False。"""
    try:
        return p.exists()
    except OSError:
        return False


def resolve_candidates(platform_paths: dict[str, str]) -> list[Candidate]:
    """平台条目 dict（路径原文 -> 备注）→ [(编号1起, 展开后路径, 备注)]。"""
    return [
        Candidate(i + 1, expand(raw), note)
        for i, (raw, note) in enumerate(platform_paths.items())
    ]


def existing_only(candidates: list[Candidate]) -> list[Candidate]:
    """过滤出存在的路径。"""
    return [c for c in candidates if exists_quietly(c.path)]


def find_candidate(candidates: list[Candidate], index: int) -> Candidate | None:
    """按 1 基编号取条目（--path N）。"""
    return next((c for c in candidates if c.index == index), None)
