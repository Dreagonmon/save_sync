"""平台判定与存档路径解析（DESIGN.md §7）。

- 展开 ~ / $VAR / %VAR%；
- 存在性检测：无权限一律视为不存在（Android 不特殊处理）；
- 0/1/多条匹配的选择规则；结果记入本地状态清单 chosen_path。
"""
from __future__ import annotations

from pathlib import Path

PLATFORMS = ("linux", "macos", "windows")  # 无独立 android：Android(Termux) 归 linux


def current_platform() -> str:
    """返回 PLATFORMS 之一。Android（ANDROID_ROOT / Termux 环境变量）返回 linux。"""
    raise NotImplementedError


def expand(raw: str) -> Path:
    """~、$VAR、%VAR% 统一展开，跨平台写法均可。"""
    raise NotImplementedError


def exists_quietly(p: Path) -> bool:
    """存在且可探测；PermissionError 等一律 False。"""
    raise NotImplementedError


def resolve_candidates(game_paths: dict[str, str], platform: str) -> list[tuple[int, Path, str]]:
    """返回该平台全部条目 [(编号1起, 展开后路径, 备注)]。"""
    raise NotImplementedError


def existing_only(candidates: list[tuple[int, Path, str]]) -> list[tuple[int, Path, str]]:
    """过滤出存在的路径。"""
    raise NotImplementedError
