"""本地目录后端（DESIGN.md §8.1）。参照实现，其余后端对它做契约对照测试。

布局：<root>/<game_id>/…（数据镜像） + <root>/<game_id>.json（清单）。
写入 tmp + os.replace 原子落盘；游戏目录视为本工具独占。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import BinaryIO, Iterator

from ..config import BackendConfig
from ..errors import BackendError
from ..manifest import RemoteManifest
from .base import Backend, FileMeta

_CHUNK = 1 << 20


class DirBackend(Backend):
    def __init__(self, cfg: BackendConfig):
        super().__init__(cfg)
        self.root = Path(cfg.path)  # type: ignore[arg-type]

    # ---- 内部 ----

    def _game_dir(self, game_id: str) -> Path:
        return self.root / game_id

    def _manifest_path(self, game_id: str) -> Path:
        return self.root / f"{game_id}.json"

    # ---- Backend 抽象方法 ----

    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        try:
            with open(self._manifest_path(game_id), encoding="utf-8") as fh:
                return RemoteManifest.from_json(json.load(fh))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise BackendError(f"后端 {self.root}: 读取清单失败: {e}") from e

    def write_manifest(self, manifest: RemoteManifest) -> None:
        p = self._manifest_path(manifest.game)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(manifest.to_json(), fh, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except OSError as e:
            raise BackendError(f"后端 {self.root}: 写清单失败: {e}") from e

    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        import os as _os
        base = self._game_dir(game_id)
        out: dict[str, FileMeta] = {}
        for dirpath, dirnames, filenames in _os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if not (Path(dirpath) / d).is_symlink()]
            for f in filenames:
                fp = Path(dirpath) / f
                if fp.is_symlink():
                    continue
                try:
                    st = fp.stat()
                except OSError:
                    continue
                rel = fp.relative_to(base).as_posix()
                out[rel] = FileMeta(size=st.st_size, mtime=st.st_mtime)
        return out

    def get(self, game_id: str, relpath: str) -> Iterator[bytes]:
        p = self._game_dir(game_id) / relpath
        try:
            with open(p, "rb") as fh:
                while chunk := fh.read(_CHUNK):
                    yield chunk
        except OSError as e:
            raise BackendError(f"后端 {self.root}: 读取 {relpath} 失败: {e}") from e

    def put(self, game_id: str, relpath: str, stream: BinaryIO, size: int) -> None:
        p = self._game_dir(game_id) / relpath
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".savesync-tmp")
            with open(tmp, "wb") as out:
                while chunk := stream.read(_CHUNK):
                    out.write(chunk)
            os.replace(tmp, p)
        except OSError as e:
            raise BackendError(f"后端 {self.root}: 写入 {relpath} 失败: {e}") from e

    def delete(self, game_id: str, relpath: str) -> None:
        p = self._game_dir(game_id) / relpath
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            raise BackendError(f"后端 {self.root}: 删除 {relpath} 失败: {e}") from e
