"""webdav 后端测试：进程内 MiniDAV 服务器（stdlib http.server），真 HTTP 往返。

MiniDAV 支持 GET/PUT/DELETE/MKCOL/PROPFIND(Depth:1 单层，逼后端自己递归)，
可选 Basic 认证，用于验证密码链。
"""
from __future__ import annotations

import base64
import io
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from savesync.backends.webdav import WebDAVBackend
from savesync.config import BackendConfig, GameConfig
from savesync.errors import BackendError, NeedsInputError
from savesync import sync as S


class MiniDAV(BaseHTTPRequestHandler):
    FILES: dict[str, bytes] = {}
    DIRS: set[str] = {"/dav"}
    REQUIRE: tuple[str, str] | None = None
    LAST_MODIFIED: str | None = None
    ECHO_404 = False  # 模拟非合规服务器：404 propstat 里回显空请求属性
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- 工具 ----

    def _path(self) -> str:
        return urllib.parse.unquote(self.path.split("?")[0])

    def _auth_ok(self) -> bool:
        if not self.REQUIRE:
            return True
        want = base64.b64encode(
            f"{self.REQUIRE[0]}:{self.REQUIRE[1]}".encode()).decode()
        return self.headers.get("Authorization", "") == "Basic " + want

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="m"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _reply(self, code: int, body: bytes = b"", extra=None):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    # ---- 方法 ----

    def do_GET(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        if p in self.FILES:
            extra = ({"Last-Modified": self.LAST_MODIFIED}
                     if self.LAST_MODIFIED else None)
            return self._reply(200, self.FILES[p], extra)
        self._reply(404)

    def do_PUT(self):
        body = self._body()
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        if p.rsplit("/", 1)[0] not in self.DIRS:
            return self._reply(409)
        created = p not in self.FILES
        self.FILES[p] = body
        self._reply(201 if created else 204,
                    extra={"Location": self.path} if created else None)

    def do_DELETE(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        if p in self.FILES:
            del self.FILES[p]
            return self._reply(200)
        self._reply(404)

    def do_MKCOL(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path().rstrip("/")
        if p in self.DIRS:
            return self._reply(405)
        parent = p.rsplit("/", 1)[0] or "/"
        if parent != "/" and parent not in self.DIRS:
            return self._reply(409)  # RFC 4918 §9.3.1：祖先缺失，不自动建
        self.DIRS.add(p)
        self._reply(201, extra={"Location": self.path})

    def do_PROPFIND(self):
        self._body()
        if not self._auth_ok():
            return self._deny()
        p = self._path().rstrip("/")
        if p not in self.DIRS and p not in self.FILES:
            return self._reply(404)  # §9.1：文件也可 PROPFIND（单条 207）
        depth = self.headers.get("Depth")  # 缺省按 infinity（§9.1）
        entries = [(p, p in self.DIRS)]
        if p in self.DIRS and depth != "0":
            kids = [(d, True) for d in self.DIRS
                    if d != p and d.rsplit("/", 1)[0] == p]
            entries += kids
            entries += [(f, False) for f in self.FILES
                        if f.rsplit("/", 1)[0] == p]
            if depth is None or depth == "infinity":
                while kids:
                    c = kids.pop()[0]
                    sub = [(d, True) for d in self.DIRS
                           if d.rsplit("/", 1)[0] == c]
                    entries += sub
                    entries += [(f, False) for f in self.FILES
                                if f.rsplit("/", 1)[0] == c]
                    kids += sub
        xml = ["<?xml version=\"1.0\"?><D:multistatus xmlns:D=\"DAV:\">"]
        for path, coll in entries:
            href = "/" + "/".join(
                urllib.parse.quote(s) for s in path.strip("/").split("/"))
            prop = ("<D:resourcetype>"
                    + ("<D:collection/>" if coll else "")
                    + "</D:resourcetype>")
            if not coll:  # §15.4：getcontentlength 只定义在 GET 有 body 的资源上
                prop += ("<D:getcontentlength>"
                         + str(len(self.FILES[path]))
                         + "</D:getcontentlength>")
            if not coll and self.LAST_MODIFIED:
                prop += f"<D:getlastmodified>{self.LAST_MODIFIED}</D:getlastmodified>"
            echo = ("<D:propstat><D:prop><D:resourcetype/>"
                    "<D:getlastmodified/></D:prop><D:status>"
                    "HTTP/1.1 404 Not Found</D:status></D:propstat>"
                    if self.ECHO_404 else "")
            xml.append(f"<D:response><D:href>{href}</D:href>{echo}<D:propstat>"
                       f"<D:prop>{prop}</D:prop>"
                       f"<D:status>HTTP/1.1 200 OK</D:status>"
                       f"</D:propstat></D:response>")
        xml.append("</D:multistatus>")
        self._reply(207, "".join(xml).encode("utf-8"),
                    {"Content-Type": "application/xml; charset=utf-8"})


class WebDAVCase(unittest.TestCase):
    def setUp(self):
        MiniDAV.FILES, MiniDAV.DIRS = {}, {"/dav"}
        MiniDAV.REQUIRE, MiniDAV.LAST_MODIFIED = None, None
        MiniDAV.ECHO_404 = False
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), MiniDAV)
        self.srv.daemon_threads = True
        self.srv.block_on_close = False
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/dav"

    def backend(self, provider=None, username=None, password=None):
        return WebDAVBackend(
            BackendConfig(id="wd", type="webdav", url=self.base,
                          username=username, password=password), provider)


class TestContract(WebDAVCase):
    def test_put_get_list_delete(self):
        b = self.backend()
        rel = "存档/第1章/options.txt"
        b.put("g1", rel, io.BytesIO(b"hello"), 5)
        self.assertEqual(b"".join(b.get("g1", rel)), b"hello")
        self.assertEqual(b.list_files("g1")[rel].size, 5)
        b.delete("g1", rel)
        self.assertNotIn(rel, b.list_files("g1"))
        b.delete("g1", rel)  # 幂等（404 吞掉）
        with self.assertRaises(BackendError):
            b"".join(b.get("g1", rel))

    def test_manifest_roundtrip_missing(self):
        from savesync.manifest import RemoteFile, RemoteManifest
        b = self.backend()
        self.assertIsNone(b.read_manifest("g1"))
        m = RemoteManifest(game="g1", version=1,
                           upload_time="2026-11-08T13:00:00+08:00",
                           time_source="local", source_platform="linux",
                           files=[RemoteFile("a", "h", 1,
                                             "2026-11-08T12:00:00+08:00")])
        b.write_manifest(m)
        got = b.read_manifest("g1")
        self.assertEqual((got.version, got.files[0].path), (1, "a"))

    def test_nested_put_mkcols_parents(self):
        b = self.backend()
        b.put("g1", "a/b/c.dat", io.BytesIO(b"x"), 1)  # 409 → MKCOL 链
        self.assertIn("/dav/g1/a/b/c.dat", MiniDAV.FILES)

    def test_write_manifest_creates_missing_base(self):
        MiniDAV.DIRS = set()  # 全新服务器：/dav 也不存在
        b = self.backend()
        from savesync.manifest import RemoteManifest
        b.write_manifest(RemoteManifest(
            game="g1", version=1, upload_time="t", time_source="local",
            source_platform="linux", files=[]))
        self.assertEqual(b.read_manifest("g1").version, 1)

    def test_list_files_recursive_and_mtime(self):
        MiniDAV.FILES = {"/dav/g1/a.txt": b"aa",
                         "/dav/g1/sub/b.txt": b"bbb"}
        MiniDAV.DIRS = {"/dav", "/dav/g1", "/dav/g1/sub"}
        MiniDAV.LAST_MODIFIED = "Sun, 08 Nov 2026 12:00:00 GMT"
        b = self.backend()
        files = b.list_files("g1")
        self.assertEqual(sorted(files), ["a.txt", "sub/b.txt"])
        self.assertEqual(files["sub/b.txt"].size, 3)
        self.assertIsNotNone(files["a.txt"].mtime)
        self.assertEqual(b.list_files("nope"), {})  # 404 → 空

    def test_propstat_404_echo_ignored(self):
        # RFC 4918 §9.1.2：404 propstat 中的属性无值，客户端只采信 2xx
        MiniDAV.FILES = {"/dav/g1/a.txt": b"aa", "/dav/g1/sub/b.txt": b"bbb"}
        MiniDAV.DIRS = {"/dav", "/dav/g1", "/dav/g1/sub"}
        MiniDAV.ECHO_404 = True
        files = self.backend().list_files("g1")
        self.assertEqual(sorted(files), ["a.txt", "sub/b.txt"])

    def test_server_fidelity_propfind_file_and_default_depth(self):
        # §9.1：文件可 PROPFIND（单条 207）；缺 Depth 头按 infinity
        MiniDAV.FILES = {"/dav/g1/a.txt": b"aa", "/dav/g1/sub/b.txt": b"bbb"}
        MiniDAV.DIRS = {"/dav", "/dav/g1", "/dav/g1/sub"}
        req = urllib.request.Request(self.base + "/g1/a.txt",
                                     data=b"", method="PROPFIND")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 207)
            self.assertEqual(resp.read().decode().count("<D:response>"), 1)
        req = urllib.request.Request(self.base + "/g1/",
                                     data=b"", method="PROPFIND")
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
        self.assertEqual(body.count("<D:response>"), 4)  # 含全部后代

    def test_multilevel_subdirectory_base(self):
        # 配置到服务器多级子目录：不存在则自顶向下自动建链
        MiniDAV.DIRS = {"/webdav-root"}  # /dav 本身也不存在
        b = WebDAVBackend(BackendConfig(
            id="wd", type="webdav", url=self.base + "/savesync/prod"))
        b.put("g1", "a/b.dat", io.BytesIO(b"x"), 1)
        self.assertIn("/dav/savesync/prod/g1/a/b.dat", MiniDAV.FILES)
        self.assertEqual(sorted(MiniDAV.DIRS),
                         ["/dav", "/dav/savesync", "/dav/savesync/prod",
                          "/dav/savesync/prod/g1", "/dav/savesync/prod/g1/a",
                          "/webdav-root"])
        self.assertEqual(b.list_files("g1")["a/b.dat"].size, 1)
        self.assertIsNone(b.read_manifest("g1"))  # 子目录可达但无清单

    def test_connection_refused(self):
        b = WebDAVBackend(BackendConfig(
            id="wd", type="webdav", url="http://127.0.0.1:1/dav"))
        with self.assertRaises(BackendError):
            b.read_manifest("g1")


class TestPasswordChain(WebDAVCase):
    def test_configured_password(self):
        MiniDAV.REQUIRE = ("me", "s3cret")
        self.backend(username="me", password="s3cret").put(
            "g1", "a", io.BytesIO(b"x"), 1)
        with self.assertRaises(BackendError):
            self.backend(username="me",
                             password="wrong").read_manifest("g1")

    def test_401_triggers_provider_once(self):
        MiniDAV.REQUIRE = ("me", "s3cret")
        calls = []

        def provider():
            calls.append(1)
            return "s3cret"

        b = self.backend(provider=provider, username="me")
        b.put("g1", "a", io.BytesIO(b"x"), 1)
        b.put("g1", "b", io.BytesIO(b"y"), 1)
        self.assertEqual(len(calls), 1)  # 缓存复用

    def test_401_non_interactive_needsinput(self):
        MiniDAV.REQUIRE = ("me", "s3cret")

        def provider():
            raise NeedsInputError([])

        b = self.backend(provider=provider, username="me")
        with self.assertRaises(NeedsInputError):
            b.read_manifest("g1")

    def test_anonymous_never_prompted(self):
        calls = []
        self.backend(provider=lambda: calls.append(1)).put(
            "g1", "a", io.BytesIO(b"x"), 1)
        self.assertEqual(calls, [])


class FakeResp:
    def __init__(self, body=b""):
        self.body, self.closed = body, False

    def read(self, n=-1):
        if n is None or n < 0:
            out, self.body = self.body, b""
        else:
            out, self.body = self.body[:n], self.body[n:]
        return out

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def http_err(code: int) -> BackendError:
    e = BackendError(f"HTTP {code}")
    e.http_code = code  # type: ignore[attr-defined]
    return e


class TestUnit(unittest.TestCase):
    """纯单元：mock _request，覆盖解析/容错分支，不起服务器。"""

    def bk(self, url="http://h/dav"):
        return WebDAVBackend(BackendConfig(id="w", type="webdav", url=url))

    def test_url_segment_encoding(self):
        b = self.bk()
        self.assertEqual(
            b._url("g1", "存档 1#2/?.dat"),
            "http://h/dav/g1/" + urllib.parse.quote("存档 1#2")
            + "/" + urllib.parse.quote("?.dat"))
        self.assertEqual(b._url("g1", "./a//b.dat"),
                         "http://h/dav/g1/a/b.dat")  # 空/单点段剔除

    def test_propfind_parse_edge_cases(self):
        xml = (
            '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">'
            "<D:response><D:href>http://other/dav/g1/x.bin</D:href>"  # 绝对 URI
            "<D:propstat><D:prop><D:resourcetype/>"
            "<D:getcontentlength>-</D:getcontentlength>"  # §15.4 未知长度
            "<D:getlastmodified>garbage</D:getlastmodified>"  # 非法日期
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
            "</D:response>"
            "<D:response><D:href>/dav/g1/y</D:href>"  # 无 resourcetype → 跳过
            "<D:propstat><D:prop><D:getcontentlength>5</D:getcontentlength>"
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
            "</D:response></D:multistatus>").encode()
        b = self.bk()
        with mock.patch.object(b, "_request", return_value=FakeResp(xml)):
            out = b._propfind("http://h/dav/g1/")
        self.assertEqual(out, [("/dav/g1/x.bin", False, 0, None)])

    def test_propfind_bad_xml(self):
        b = self.bk()
        with mock.patch.object(b, "_request", return_value=FakeResp(b"<x")):
            with self.assertRaises(BackendError):
                b._propfind("http://h/dav/g1/")

    def test_read_manifest_bad_json(self):
        b = self.bk()
        with mock.patch.object(b, "_request", return_value=FakeResp(b"{oops")):
            with self.assertRaises(BackendError):
                b.read_manifest("g1")

    def test_list_files_skips_foreign_href(self):
        xml = ('<D:multistatus xmlns:D="DAV:"><D:response>'
               "<D:href>/other/root/a.txt</D:href><D:propstat><D:prop>"
               "<D:resourcetype/><D:getcontentlength>1</D:getcontentlength>"
               "</D:prop><D:status>HTTP/1.1 200 OK</D:status>"
               "</D:propstat></D:response></D:multistatus>").encode()
        b = self.bk()
        with mock.patch.object(b, "_request", return_value=FakeResp(xml)):
            self.assertEqual(b.list_files("g1"), {})

    def test_mkcol_tolerance_table(self):
        b = self.bk()
        for code, ok in ((405, True), (409, True), (301, True), (500, False)):
            with mock.patch.object(b, "_request",
                                   side_effect=http_err(code)):
                if ok:
                    b._mkcol("http://h/dav/g1/")
                else:
                    with self.assertRaises(BackendError):
                        b._mkcol("http://h/dav/g1/")

    def test_ensure_collection_topdown_and_cached(self):
        b = self.bk()
        made = []
        with mock.patch.object(b, "_mkcol", side_effect=made.append):
            b._ensure_collection("http://h/dav/g1/sub/")
            self.assertEqual(made, ["http://h/dav/", "http://h/dav/g1/",
                                    "http://h/dav/g1/sub/"])  # 自顶向下
            b._ensure_collection("http://h/dav/g1/other/")
            self.assertEqual(made[-1], "http://h/dav/g1/other/")
            n = len(made)
            b._ensure_collection("http://h/dav/g1/sub/")  # 会话缓存命中
            self.assertEqual(len(made), n)

    def test_write_manifest_409_rebuilds_and_retries(self):
        from savesync.manifest import RemoteManifest
        b = self.bk()
        calls = []

        def fake_request(method, url, data=None, headers=None,
                         ok_404=False):
            calls.append(method)
            if method == "PUT" and calls.count("PUT") == 1:
                raise http_err(409)
            return FakeResp()

        m = RemoteManifest(game="g1", version=1, upload_time="t",
                           time_source="local", source_platform="linux",
                           files=[])
        with mock.patch.object(b, "_request", side_effect=fake_request), \
                mock.patch.object(b, "_mkcol"):
            b.write_manifest(m)
        self.assertEqual(calls, ["PUT", "PUT"])  # 409 → 建链 → 重试

    def test_get_closes_response(self):
        b = self.bk()
        r = FakeResp(b"payload")
        with mock.patch.object(b, "_request", return_value=r):
            self.assertEqual(list(b.get("g1", "a")), [b"payload"])
        self.assertTrue(r.closed)


class TestSyncCycle(WebDAVCase):
    def test_backup_restore(self):
        from datetime import datetime
        self._td = TemporaryDirectory()
        self.save = Path(self._td.name)
        b = self.backend()
        g = GameConfig(id="g1", name="G", ignore=[],
                       paths={"linux": {str(self.save): "主"}})
        (self.save / "a.dat").write_bytes(b"v1")
        with mock.patch.object(S, "now_with_source",
                               lambda: (datetime.now().astimezone(), "local")):
            _, st = S.execute_backup(
                S.plan_backup(g, b, None, platform="linux"),
                {}, g, b, None, backend_id="wd")
            self.assertEqual(st.version, 1)
            self.assertEqual(b.read_manifest("g1").version, 1)
            # 换机器：新路径恢复
            target = Path(self._td.name) / "other"  # 不存在 → 出路径决策
            g2 = GameConfig(id="g1", name="G", ignore=[],
                            paths={"linux": {str(target): "新"}})
            plan = S.plan_restore(g2, b, None, platform="linux")
            plan2 = S.plan_restore(g2, b, None, platform="linux",
                                   save_root=plan.path_options[0])
            _, st2 = S.execute_restore(plan2, {}, g2, b, None,
                                       backend_id="wd")
            self.assertEqual((target / "a.dat").read_bytes(), b"v1")
            self.assertEqual(st2.version, 1)


if __name__ == "__main__":
    unittest.main()
