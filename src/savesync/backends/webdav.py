"""WebDAV 后端（DESIGN.md §8.3）。urllib 裸实现，零第三方依赖。

PROPFIND Depth:1 逐层递归列举（兼容不支持无限 Depth 的服务器）/ MKCOL 建目录 /
PUT 上传 / DELETE 删除；路径分段 URL 编码；Basic/Digest 认证走 stdlib 处理器链。
不依赖服务器 Last-Modified / ETag / COPY 语义；
清单 PUT 覆盖视为原子提交点，写后由 sync 层重读校验 version。

密码解析链（DESIGN.md §2.1）：cfg.password（明文）> cfg.password_env（环境变量）
> 首次 401 时回调 password_provider（交互 getpass，缓存复用）；
非交互无密码 → NeedsInputError（rc 3）。匿名服务器不 401 则全程不需要密码。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import BinaryIO, Callable, Iterator

from ..config import BackendConfig
from ..errors import BackendError
from ..manifest import RemoteManifest
from .base import FileMeta

import base64

_CHUNK = 1 << 20
TIMEOUT_S = 30.0
_D = "{DAV:}"
_PROPFIND_BODY = (
    '<?xml version="1.0"?><propfind xmlns="DAV:"><prop>'
    "<resourcetype/><getcontentlength/><getlastmodified/>"
    "</prop></propfind>").encode()


class _PreemptiveBasic(urllib.request.BaseHandler):
    """凭据已知时直接带 Basic 头，避免 401 挑战-重试消耗流式 body。"""

    def __init__(self, header: str):
        self._header = header

    def http_request(self, req):
        req.add_unredirected_header("Authorization", self._header)
        return req

    https_request = http_request


class WebDAVBackend:
    def __init__(self, cfg: BackendConfig,
                 password_provider: Callable[[], str] | None = None):
        self.base_url = cfg.url.rstrip("/") if cfg.url else ""
        self.username = cfg.username
        self._password = cfg.password or os.environ.get(
            cfg.password_env or "", "")
        self._password_provider = password_provider
        self._opener: urllib.request.OpenerDirector | None = None
        self._known_dirs: set[str] = set()  # 本会话已确认存在的集合

    # ---- HTTP 层 ----

    def _build_opener(self) -> urllib.request.OpenerDirector:
        if self._opener is None:
            handlers: list = []
            if self.username and self._password:
                token = base64.b64encode(
                    f"{self.username}:{self._password}".encode()).decode()
                handlers = [_PreemptiveBasic("Basic " + token)]
                # Digest-only 服务器兜底（无 body 的请求可安全内部重试）
                mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
                mgr.add_password(None, self.base_url, self.username,
                                 self._password)
                handlers.append(urllib.request.HTTPDigestAuthHandler(mgr))
            self._opener = urllib.request.build_opener(*handlers)
        return self._opener

    def _request(self, method: str, url: str, data=None,
                 headers: dict[str, str] | None = None,
                 ok_404: bool = False):
        hdrs = {"User-Agent": "savesync/0.1", **(headers or {})}
        for attempt in (0, 1):
            if hasattr(data, "seek"):
                try:
                    data.seek(0)
                except OSError:
                    pass
            req = urllib.request.Request(url, data=data, method=method,
                                         headers=hdrs)
            try:
                return self._build_opener().open(req, timeout=TIMEOUT_S)
            except urllib.error.HTTPError as e:
                if (e.code == 401 and attempt == 0 and not self._password
                        and self.username and self._password_provider):
                    self._password = self._password_provider()  # 可抛 NeedsInputError
                    self._opener = None
                    continue
                if e.code == 404 and ok_404:
                    return None
                err = BackendError(
                    f"后端 {self.base_url}: {method} {url} → HTTP {e.code}")
                err.http_code = e.code  # type: ignore[attr-defined]
                raise err from e
            except urllib.error.URLError as e:
                raise BackendError(
                    f"后端 {self.base_url}: {method} {url} 失败: {e.reason}") from e
            except OSError as e:
                raise BackendError(
                    f"后端 {self.base_url}: {method} {url} 失败: {e}") from e
        raise AssertionError  # 不可达

    def _url(self, *parts: str) -> str:
        """分段 URL 编码拼接（'/' 不编码）。"""
        segs: list[str] = []
        for p in parts:
            segs.extend(urllib.parse.quote(s, safe="")
                        for s in str(p).split("/") if s not in ("", "."))
        return "/".join([self.base_url] + segs)

    # ---- 集合维护 ----

    def _mkcol(self, url: str) -> None:
        try:
            self._request("MKCOL", url, data=b"")
        except BackendError as e:
            # RFC 4918 §9.3.1：405 = URL 已映射（唯一合规的"存在"信号）；
            # 409 本义是祖先缺失（自顶向下建 + 存在性探测后正常不触发）。
            # 网盘桥接服务器（CloudDrive/Alist 等实测）对已存在或只读的
            # 挂载点回 400/501 而非 405，一并容忍；真缺目录会在后续
            # PUT 409 / 写清单重试路径暴露。301 = 尾斜杠重定向（nginx 实测）。
            if getattr(e, "http_code", None) not in (301, 400, 405, 409, 501):
                raise

    def _exists(self, url: str) -> bool:
        """PROPFIND Depth:0 探测集合存在性（§9.1 允许对任意资源 PROPFIND）。

        非 404 的探测错误宽容当作存在（只读挂载点可能拒 PROPFIND）；
        若实际不存在，后续 PUT 409 会暴露真实问题。
        """
        try:
            resp = self._request(
                "PROPFIND", url, data=_PROPFIND_BODY,
                headers={"Depth": "0",
                         "Content-Type": "application/xml",
                         "Content-Length": str(len(_PROPFIND_BODY))},
                ok_404=True)
        except BackendError:
            return True
        if resp is None:
            return False
        resp.close()
        return True

    def _ensure_collection(self, url: str) -> None:
        """确保 url 的全部祖先集合存在（含 base 自身，全新服务器上可能缺失）。

        MKCOL 只发给 PROPFIND 确认不存在（404）的段——RFC 4918 §9.3 本就
        规定 MKCOL 仅可对未映射 URL 执行；网盘桥接对已存在路径的 MKCOL
        常回 400，先探测可完全避开。结果按会话缓存，每段至多一次往返。
        """
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/")
        origin = url[: len(url) - len(parsed.path)]
        acc = ""
        for seg in path.split("/"):
            if not seg:
                continue
            acc += "/" + seg  # path 来自 URL，保持编码形态
            if acc in self._known_dirs:
                continue
            target = origin + acc + "/"
            if not self._exists(target):
                self._mkcol(target)
            self._known_dirs.add(acc)

    # ---- Backend 协议 ----

    def read_manifest(self, game_id: str) -> RemoteManifest | None:
        resp = self._request("GET", self._url(f"{game_id}.json"),
                             ok_404=True)
        if resp is None:
            return None
        with resp:
            try:
                return RemoteManifest.from_json(
                    json.loads(resp.read().decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as e:
                raise BackendError(
                    f"后端 {self.base_url}: 读取清单失败: {e}") from e

    def write_manifest(self, manifest: RemoteManifest) -> None:
        """提交点：PUT 覆盖清单；base 集合缺失（409）则建链重试一次。"""
        body = json.dumps(manifest.to_json(), ensure_ascii=False,
                          indent=1).encode("utf-8")
        url = self._url(f"{manifest.game}.json")
        hdrs = {"Content-Length": str(len(body)),
                "Content-Type": "application/json"}
        try:
            self._request("PUT", url, data=body, headers=hdrs).close()
        except BackendError as e:
            if getattr(e, "http_code", None) != 409:
                raise
            self._known_dirs.clear()
            self._ensure_collection(self.base_url)
            self._request("PUT", url, data=body, headers=hdrs).close()

    def list_files(self, game_id: str) -> dict[str, FileMeta]:
        out: dict[str, FileMeta] = {}
        stack = [(self._url(game_id) + "/", "")]
        while stack:
            url, prefix = stack.pop()
            base_dec = urllib.parse.unquote(
                urllib.parse.urlparse(url).path).rstrip("/") + "/"
            for href, is_coll, size, mtime in self._propfind(url):
                if not href.startswith(base_dec):
                    continue
                rel = prefix + href[len(base_dec):]
                if not rel:
                    continue  # 集合自身
                if is_coll:
                    stack.append((url + "/".join(
                        urllib.parse.quote(s, safe="")
                        for s in rel[len(prefix):].split("/")) + "/",
                        rel + "/"))
                else:
                    out[rel] = FileMeta(size=size, mtime=mtime)
        return out

    def _propfind(self, url: str):
        """Depth:1 → [(解码 href, 是否集合, size, mtime)]；404 → []。"""
        resp = self._request(
            "PROPFIND", url, data=_PROPFIND_BODY,
            headers={"Depth": "1", "Content-Type": "application/xml",
                     "Content-Length": str(len(_PROPFIND_BODY))},
            ok_404=True)
        if resp is None:
            return []
        with resp:
            body = resp.read()
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise BackendError(
                f"后端 {self.base_url}: PROPFIND 响应非法 XML: {e}") from e
        out = []
        for r in root.iter(_D + "response"):
            href = r.findtext(_D + "href", "")
            if "://" in href:
                href = urllib.parse.urlparse(href).path
            href = urllib.parse.unquote(href)
            # RFC 4918 §9.1.2：不存在的属性出现在 status 404 的 propstat 中
            # 且无值，只采信 2xx propstat
            prop = None
            for ps in r.iter(_D + "propstat"):
                st = (ps.findtext(_D + "status") or "").split()
                if len(st) < 2 or not st[1].startswith("2"):
                    continue
                p_el = ps.find(_D + "prop")
                if p_el is not None \
                        and p_el.find(_D + "resourcetype") is not None:
                    prop = p_el
                    break
            if prop is None:
                continue
            rt = prop.find(_D + "resourcetype")
            is_coll = rt is not None \
                and rt.find(_D + "collection") is not None
            mtime = None
            lm = prop.findtext(_D + "getlastmodified")
            if lm:
                try:
                    mtime = parsedate_to_datetime(lm).timestamp()
                except (TypeError, ValueError):
                    pass  # 无 mtime 的环境：仅作展示，容错
            try:
                size = int(prop.findtext(_D + "getcontentlength") or 0)
            except ValueError:
                size = 0
            out.append((href, is_coll, size, mtime))
        return out

    def get(self, game_id: str, relpath: str) -> Iterator[bytes]:
        resp = self._request("GET", self._url(game_id, relpath))
        try:
            while chunk := resp.read(_CHUNK):
                yield chunk
        finally:
            resp.close()

    def put(self, game_id: str, relpath: str, stream: BinaryIO,
            size: int) -> None:
        parent = self._url(game_id, relpath).rsplit("/", 1)[0]
        self._ensure_collection(parent)
        self._request(
            "PUT", self._url(game_id, relpath), data=stream,
            headers={"Content-Length": str(size),
                     "Content-Type": "application/octet-stream"}).close()

    def delete(self, game_id: str, relpath: str) -> None:
        self._request("DELETE", self._url(game_id, relpath), ok_404=True)
