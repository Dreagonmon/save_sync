"""存档树扫描：ignore 规则 + 两级 hash（DESIGN.md §5/§6）。

相对路径统一正斜杠；fnmatch 匹配（* 可跨 /）；目录前缀命中即整树剪枝。
(size, mtime) 与状态清单一致时沿用旧 hash，否则重算 SHA-256。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ScannedFile:
    path: str          # 相对存档根，正斜杠
    hash: str          # sha256 hex
    size: int
    mtime: float


def is_ignored(relpath: str, patterns: list[str]) -> bool:
    raise NotImplementedError


def iter_dir_prefixes(relpath: str) -> Iterator[str]:
    """yield 'a', 'a/b', ... 供目录级剪枝判断。"""
    raise NotImplementedError


def scan(
    root: Path,
    ignore: list[str],
    known_hashes: dict[str, tuple[int, float, str]] | None = None,
    *,
    warn_large_mb: int = 512,
) -> list[ScannedFile]:
    """扫描存档树。known_hashes: path -> (size, mtime, hash) 用于跳过重算。

    符号链接跳过并记录；空目录由调用方按需保留。
    """
    raise NotImplementedError


def hash_file(p: Path, *, chunk: int = 1 << 20) -> str:
    raise NotImplementedError
