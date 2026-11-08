"""存档树扫描：ignore 规则 + 两级 hash（DESIGN.md §5/§6）。

相对路径统一正斜杠；fnmatchcase 匹配（* 可跨 /，大小写敏感保证跨平台一致）；
目录前缀命中即整树剪枝。符号链接跟随（Proton/Lutris 的 prefix 内常用链接目录
放存档），以 (st_dev, st_ino) 访问集防环防重，真实目录优先——同一目标按真实
路径记录，恢复不物化链接结构；断链跳过。空目录不保留（清单只记录文件）。
(size, mtime) 与状态清单一致时沿用旧 hash，否则重算 SHA-256。
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

# known_hashes: path -> (size, mtime, hash)，来自本地状态清单
KnownHashes = dict[str, tuple[int, float, str]]


@dataclass(frozen=True)
class ScannedFile:
    path: str          # 相对存档根，正斜杠
    hash: str          # sha256 hex
    size: int
    mtime: float


def is_ignored(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relpath, p) for p in patterns)


def _dir_ignored(rel: str, patterns: list[str]) -> bool:
    """目录本身命中，或作为前缀命中（如模式 logs/* 命中目录 logs）。"""
    return is_ignored(rel, patterns) or is_ignored(rel + "/.", patterns)


def iter_dir_prefixes(relpath: str) -> Iterator[str]:
    """yield 'a', 'a/b', ... 供目录级剪枝判断。"""
    parts = relpath.split("/")
    for i in range(1, len(parts)):
        yield "/".join(parts[:i])


def scan(
    root: Path,
    ignore: list[str],
    known_hashes: KnownHashes | None = None,
    *,
    warn_large_mb: int = 512,
    warn: Callable[[str], None] | None = None,
) -> list[ScannedFile]:
    """扫描存档树。known_hashes 命中 (size, mtime) 时跳过重算。

    符号链接目录跟随（os.walk followlinks + (dev,ino) 访问集防环）；同一目录
    可经真实路径与链接路径重复到达时只按真实路径扫描一次，避免重复备份与
    恢复后把链接物化成真实目录（如 Proton 的 dosdevices/c:）。断链跳过。
    超大文件警告一次（经 warn 回调，core 不 print）。
    """
    known_hashes = known_hashes or {}
    root = Path(root)
    out: list[ScannedFile] = []
    visited: set[tuple[int, int]] = set()
    try:
        rst = root.stat()
        visited.add((rst.st_dev, rst.st_ino))
    except OSError:
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        base = Path(dirpath)
        reals: list[str] = []
        links: list[tuple[str, tuple[int, int]]] = []
        for d in sorted(dirnames):
            dp = base / d
            rel = dp.relative_to(root).as_posix()
            if _dir_ignored(rel, ignore):
                continue
            try:
                dst = dp.stat()
            except OSError:
                continue  # 断链 / 扫描期间消失
            key = (dst.st_dev, dst.st_ino)
            if dp.is_symlink():
                links.append((d, key))
            elif key not in visited:
                visited.add(key)
                reals.append(d)
        for d, key in links:  # 真实目录先行，链接只补真实路径未覆盖的目标
            rel = (base / d).relative_to(root).as_posix()
            if key in visited:
                if warn is not None:
                    warn(f"跳过符号链接目录 {rel}（目标已扫描或成环）")
                continue
            visited.add(key)
            reals.append(d)
        dirnames[:] = reals
        for f in sorted(filenames):
            fp = base / f
            rel = fp.relative_to(root).as_posix()
            if is_ignored(rel, ignore):
                continue
            try:
                st = fp.stat()  # 跟随符号链接文件；断链 OSError 跳过
            except OSError:
                continue  # 扫描期间消失/不可读
            known = known_hashes.get(rel)
            if known is not None and known[0] == st.st_size and known[1] == st.st_mtime:
                h = known[2]
            else:
                h = hash_file(fp)
            if st.st_size > warn_large_mb * 1024 * 1024 and warn is not None:
                warn(f"大文件 {rel}: {st.st_size / 2**20:.1f} MB")
            out.append(ScannedFile(rel, h, st.st_size, st.st_mtime))
    return out


def hash_file(p: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
