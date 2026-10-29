import json
import pathlib
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gama_registry as R
import gama_registry_server as S

VALID = {
    "version": 1,
    "packages": {
        "math": {
            "version": "0.1.0",
            "git": "../mathrepo",
            "rev": "0123456789abcdef0123456789abcdef01234567",
        },
        "Alpha": {
            "version": "2.0.0",
            "git": "https://example.com/alpha.git",
        },
    },
}


class _StaticHandler(BaseHTTPRequestHandler):
    body = b"{}"
    content_type = "application/json"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


def _start(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _url(httpd, path):
    return "http://127.0.0.1:%d%s" % (httpd.server_address[1], path)


class HttpRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self.registry = base / "registry.json"
        self.registry.write_text(json.dumps(VALID), encoding="utf-8")
        self.httpd, self.thread = _start(S.make_handler(self.registry))
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        _stop(self.httpd, self.thread)
        self._tmp.cleanup()

    def _get(self, path, timeout=5):
        return urllib.request.urlopen(self.base + path, timeout=timeout)

    def _expect_http_error(self, path, code):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get(path)
        err = cm.exception
        self.assertEqual(err.code, code, path)
        err.close()

    def test_load_registry_and_search_over_http(self):
        index = R.load_registry(self.base + "/index.json")
        self.assertEqual([r["name"] for r in R.search(index)], ["Alpha", "math"])
        self.assertEqual([r["name"] for r in R.search(index, "MA")], ["math"])
        self.assertEqual(R.resolve(index, "math", "0.1.0")["git"], "../mathrepo")

    def test_health(self):
        with self._get("/health") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "application/json")
            self.assertEqual(json.loads(resp.read()), {"ok": True})

    def test_package_endpoint(self):
        with self._get("/packages/math") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "application/json")
            payload = json.loads(resp.read())
        self.assertEqual(
            payload,
            {
                "name": "math",
                "version": "0.1.0",
                "git": "../mathrepo",
                "rev": "0123456789abcdef0123456789abcdef01234567",
            },
        )

    def test_unknown_routes_are_404(self):
        points = (
            "/",
            "/packages/nope",
            "/packages/",
            "/missing.json",
            "/packages/../x",
            "/packages/%2e%2e/x",
        )
        for path in points:
            with self.subTest(path=path):
                self._expect_http_error(path, 404)

    def test_index_with_query_string(self):
        with self._get("/index.json?cache=0") as resp:
            self.assertEqual(json.loads(resp.read())["version"], 1)

    def test_post_is_rejected(self):
        req = urllib.request.Request(
            self.base + "/index.json",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        err = cm.exception
        try:
            self.assertIn(err.code, (405, 404))
            self.assertEqual(err.headers.get("Allow"), "GET")
        finally:
            err.close()

    def test_unreachable_server_raises_registry_error(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        finally:
            sock.close()
        with self.assertRaises(R.RegistryError):
            R.load_registry("http://127.0.0.1:%d/index.json" % port)

    def test_unsupported_scheme(self):
        for src in ("ftp://127.0.0.1/registry.json", "gopher://example.com/r.json"):
            with self.subTest(src=src):
                with self.assertRaises(R.RegistryError):
                    R.load_registry(src)


class HttpRegistryContentTests(unittest.TestCase):
    def _with_server(self, handler_cls, fn):
        httpd, thread = _start(handler_cls)
        try:
            return fn(_url(httpd, "/index.json"))
        finally:
            _stop(httpd, thread)

    def test_text_plain_is_accepted(self):
        class Plain(_StaticHandler):
            content_type = "text/plain; charset=utf-8"
            body = json.dumps(VALID).encode("utf-8")

        index = self._with_server(Plain, R.load_registry)
        self.assertEqual([r["name"] for r in R.search(index, "math")], ["math"])

    def test_unsupported_content_type_is_rejected(self):
        class Html(_StaticHandler):
            content_type = "text/html"
            body = b"<html></html>"

        with self.assertRaises(R.RegistryError):
            self._with_server(Html, R.load_registry)

    def test_body_size_limit(self):
        class Big(_StaticHandler):
            content_type = "text/plain"
            body = b"x" * (1024 * 1024 + 1)

        with self.assertRaises(R.RegistryError):
            self._with_server(Big, R.load_registry)

    def test_invalid_json_over_http(self):
        class Bad(_StaticHandler):
            content_type = "application/json"
            body = b"{ not json"

        with self.assertRaises(R.RegistryError):
            self._with_server(Bad, R.load_registry)


if __name__ == "__main__":
    unittest.main()
