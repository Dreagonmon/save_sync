"""清单读写（DESIGN.md §3）。

远端清单：version / upload_time / time_source / source_platform / files[]。
本地状态清单：唯一同步点 version + 文件表（hash/size/mtime，供两级过滤），无历史
+ chosen_path。本地写入一律 tmp + os.replace 原子落盘。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def iso_now(dt: datetime | None = None) -> str:
    return (dt or datetime.now().astimezone()).isoformat(timespec="seconds")


def ts_to_iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def iso_to_ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


@dataclass
class RemoteFile:
    path: str
    hash: str
    size: int
    mtime: str = ""  # ISO8601 带时区；"" = 未知（无 mtime 的环境），仅尽力恢复用

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "hash": self.hash,
                "size": self.size, "mtime": self.mtime}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "RemoteFile":
        return cls(str(d["path"]), str(d["hash"]), int(d["size"]),
                   str(d.get("mtime", "")))


@dataclass
class RemoteManifest:
    game: str
    version: int
    upload_time: str
    time_source: str            # "ntp" | "local"
    source_platform: str
    files: list[RemoteFile] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"game": self.game, "version": self.version,
                "upload_time": self.upload_time, "time_source": self.time_source,
                "source_platform": self.source_platform,
                "files": [f.to_json() for f in self.files]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "RemoteManifest":
        try:
            return cls(
                game=str(d["game"]), version=int(d["version"]),
                upload_time=str(d["upload_time"]), time_source=str(d["time_source"]),
                source_platform=str(d["source_platform"]),
                files=[RemoteFile.from_json(f) for f in d["files"]],
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"远端清单格式非法: {e!r}") from e


@dataclass
class FileEntry:
    """本地状态清单条目：hash + (size, mtime) 两级过滤指纹。"""
    hash: str
    size: int
    mtime: float

    def to_json(self) -> dict[str, Any]:
        return {"hash": self.hash, "size": self.size, "mtime": self.mtime}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "FileEntry":
        return cls(str(d["hash"]), int(d["size"]), float(d["mtime"]))


@dataclass
class LocalState:
    """每游戏一份本地状态清单（DESIGN.md §3.2）：唯一同步点，不保留历史。

    远端只存最新镜像，旧版本内容无处可寻，历史清单无回滚价值，故刻意去除。
    版本线按游戏全局唯一、所有后端共享（同一存储的不同访问路径不产生伪冲突）；
    files 是两级 hash 过滤与恢复脏检测的依据；恢复保留的本地多余文件不写入，
    下次备份自然上传。
    """
    game: str
    version: int = 0
    files: dict[str, FileEntry] = field(default_factory=dict)  # path -> 指纹
    chosen_path: int | None = None
    last_backend: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"game": self.game, "version": self.version,
                "files": {p: e.to_json() for p, e in self.files.items()},
                "chosen_path": self.chosen_path,
                "last_backend": self.last_backend}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "LocalState":
        try:
            cp = d.get("chosen_path")
            lb = d.get("last_backend")
            return cls(
                game=str(d["game"]), version=int(d["version"]),
                files={str(p): FileEntry.from_json(e)
                       for p, e in d.get("files", {}).items()},
                chosen_path=None if cp is None else int(cp),
                last_backend=None if lb is None else str(lb),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"本地状态清单格式非法: {e!r}") from e


def load_local_state(data_dir: Path, game_id: str) -> LocalState | None:
    """不存在或损坏返回 None（视为版本 0，触发全量/对齐流程）。"""
    p = Path(data_dir) / f"{game_id}.json"
    try:
        with open(p, encoding="utf-8") as fh:
            return LocalState.from_json(json.load(fh))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def save_local_state(data_dir: Path, state: LocalState) -> None:
    """tmp + os.replace 原子写。"""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / f"{state.game}.json"
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state.to_json(), fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
