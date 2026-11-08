"""zip 后端（DESIGN.md §8.2）。单包多游戏，不做追加式增量。

zip 不可原地改，但无需 spool 缓冲：put 直接把流写进新包（tmp，append 模式，
每次写完即关，盘上始终是合法 zip，可被 get/list_files 读取）；delete 记删除集。
write_manifest 是提交点——把旧包中其余游戏条目、本游戏未动条目搬运追加进新包
（跳过差集删除与已被 put 覆盖的条目），写入新清单，os.replace 原子替换。
全程仅新包一个临时文件，峰值磁盘 = 旧包 + 新包。

成员名 UTF-8（zipfile 对非 ASCII 自动置 UTF-8 标志），ZIP_DEFLATED。
同名重复 put 以最后一次为准（zip 中央目录后写覆盖先写，读取端一致取最后）。
提交前 get/list_files 走 overlay（tmp 优先于旧包），与 dir 后端读己之写一致。
搬运采用解压→重压（保留 ZipInfo 元数据），存档体量下 CPU 代价可接受。
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterator

from ..config import BackendConfig
from ..errors import BackendError
from ..manifest import RemoteManifest
from .base import FileMeta

_CHUNK = 1 << 20


class ZipBackend:
    def __init__(self, cfg: BackendConfig):
        self.zip_path = Path(cfg.path)  # type: ignore[arg-type]
        self.tmp_path = self.zip_path.with_name(
            self.zip_path.name + ".savesync-tmp")
        # 已直接写入新包的条目：game -> {relpath}；删除集 (game, relpath)
        self._streamed: dict[str, set[str]] = {}
        self._deleted: set[tuple[str, str]] = set()
        self._tmp_fresh = False  # 本次会话是否已清理过崩溃残留的 tmp

    # ---- 内部 ----

    def _member(self, game_id: str, relpath: str) -> str:
        return f"{game_id}/{relpath}"

    def _manifest_member(self, game_id: str) -> str:
        return f"{game_id}.json"

    def _open(self, path: Path) -> zipfile.ZipFile | None:
        try:
            return zipfile.ZipFile(path)
        except FileNotFoundError:
            return None
        except (OSError, zipfile.BadZipFile) as e:
            raise BackendError(f"后端 {self.zip_path}: 打开 {path.name} 失败: {e}") from e

    def _append(self, write_fn) -> None:
        """向新包追加条目（不存在则创建）；每次调用后盘上都是合法 zip。"""
        if not self._tmp_fresh:
            self.tmp_path.unlink(missing_ok=True)  # 清理上次崩溃的残留
            self._tmp_fresh = True
        try:
            self.zip_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(self.tmp_path, "a",
                                 zipfile.ZIP_DEFLATED) as zf:
                write_fn(zf)
        except (OSError, zipfile.BadZipFile) as e:
            raise BackendError(f"后端 {self.zip_path}: 写新包失败: {e}") from e

    # ---- Backend 协议 ----

    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        name = self._manifest_member(game_id)
        zf = self._open(self.zip_path)
        if zf is None:
            return None
        with zf:
            if name not in zf.namelist():
                return None
            try:
                return RemoteManifest.from_json(
                    json.loads(zf.read(name).decode("utf-8")))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
                raise BackendError(
                    f"后端 {self.zip_path}: 读取清单失败: {e}") from e

    def write_manifest(self, manifest: RemoteManifest) -> None:
        """提交点：旧包搬运追加进新包 + 新清单，os.replace 原子替换。"""
        game = manifest.game
        dels = {r for g, r in self._deleted if g == game}
        self._deleted = {(g, r) for g, r in self._deleted if g != game}
        # 新包里已有的条目（含同实例其他游戏已写的，随本次一并提交，
        # 其清单未动属设计允许的孤儿中间态，下次备份按差集自愈）
        streamed = {self._member(g, r)
                    for g, rs in self._streamed.items() for r in rs}
        try:
            self._append(lambda zf: self._carry_and_manifest(
                zf, game, dels, streamed, manifest))
            os.replace(self.tmp_path, self.zip_path)
        except OSError as e:
            self.tmp_path.unlink(missing_ok=True)
            raise BackendError(f"后端 {self.zip_path}: 提交失败: {e}") from e
        except BackendError:
            self.tmp_path.unlink(missing_ok=True)
            raise
        finally:
            self._streamed = {}
            self._tmp_fresh = False

    def _carry_and_manifest(self, zf: zipfile.ZipFile, game: str,
                            dels: set[str], streamed: set[str],
                            manifest: RemoteManifest) -> None:
        old = self._open(self.zip_path)
        if old is not None:
            with old:
                for info in old.infolist():
                    name = info.filename
                    if name.endswith("/"):
                        continue  # 目录条目不保留
                    if name == self._manifest_member(game):
                        continue  # 本游戏旧清单被替换
                    if name in streamed:
                        continue  # 新包已有本次写入的版本
                    if name.startswith(f"{game}/") \
                            and name[len(game) + 1:] in dels:
                        continue  # 差集删除
                    zf.writestr(info, old.read(info))
        zf.writestr(self._manifest_member(game),
                    json.dumps(manifest.to_json(), ensure_ascii=False,
                               indent=1))

    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        prefix = f"{game_id}/"
        out: dict[str, FileMeta] = {}

        def collect(path: Path) -> None:
            zf = self._open(path)
            if zf is None:
                return
            with zf:
                for info in zf.infolist():  # 后写覆盖先写
                    if not info.filename.startswith(prefix) \
                            or info.filename.endswith("/"):
                        continue
                    # zip 时间戳无时区、2 秒精度，仅作展示参考（§8）
                    mtime = time.mktime(info.date_time + (0, 0, -1))
                    out[info.filename[len(prefix):]] = FileMeta(
                        size=info.file_size, mtime=mtime)

        collect(self.zip_path)
        if self.tmp_path.exists():
            collect(self.tmp_path)
        for g, rel in self._deleted:
            if g == game_id:
                out.pop(rel, None)
        return out

    def get(self, game_id: str, relpath: str) -> Iterator[bytes]:
        if (game_id, relpath) in self._deleted:
            raise BackendError(
                f"后端 {self.zip_path}: 文件不存在（待删除）: {relpath}")
        name = self._member(game_id, relpath)
        try:
            if relpath in self._streamed.get(game_id, ()) \
                    and self.tmp_path.exists():
                zf = self._open(self.tmp_path)
            else:
                zf = self._open(self.zip_path)
            if zf is None:
                raise KeyError(name)
            with zf, zf.open(name) as fh:
                while chunk := fh.read(_CHUNK):
                    yield chunk
        except (KeyError, OSError, zipfile.BadZipFile) as e:
            raise BackendError(
                f"后端 {self.zip_path}: 读取 {relpath} 失败: {e}") from e

    def put(self, game_id: str, relpath: str, stream: BinaryIO, size: int) -> None:
        name = self._member(game_id, relpath)
        info = zipfile.ZipInfo(name, time.localtime()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED

        def write(zf: zipfile.ZipFile) -> None:
            with zf.open(info, "w", force_zip64=True) as out:
                while chunk := stream.read(_CHUNK):
                    out.write(chunk)

        self._append(write)
        self._streamed.setdefault(game_id, set()).add(relpath)
        self._deleted.discard((game_id, relpath))

    def delete(self, game_id: str, relpath: str) -> None:
        self._deleted.add((game_id, relpath))
