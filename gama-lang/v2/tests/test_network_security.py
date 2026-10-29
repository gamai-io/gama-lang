"""Network hardening tests: redirect/SSRF guards in gama_ai and gama_registry."""

import json
import os
import pathlib
import sys
import threading
import unittest
from unittest import mock
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gama_ai
import gama_registry as R

AI_ENV = ("GAMA_L_AI_URL", "GAMA_L_AI_KEY", "GAMA_L_AI_MODEL", "GAMA_L_AI_ALLOW_HTTP")
REG_ENV = "GAMA_L_REGISTRY_ALLOW_PRIVATE"

OK_BODY = json.dumps(
    {"choices": [{"message": {"role": "assistant", "content": "zdravo"}}]}
).encode("utf-8")

VALID = {
    "version": 1,
    "packages": {"math": {"version": "0.1.0", "git": "../mathrepo"}},
}


def _start(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _url(httpd, path="/"):
    return "http://127.0.0.1:%d%s" % (httpd.server_address[1], path)


class _ChatHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(OK_BODY)))
        self.end_headers()
        self.wfile.write(OK_BODY)


class _RedirectHandler(BaseHTTPRequestHandler):
    target = ""
    seen = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        type(self).seen += 1
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(302)
        self.send_header("Location", type(self).target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST


class _StaticHandler(BaseHTTPRequestHandler):
    body = b"{}"
    content_type = "application/json"
    seen = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen += 1
        self.send_response(200)
        if self.content_type is not None:
            self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


class GamaAiRedirectTests(unittest.TestCase):
    def setUp(self):
        self._cleaned = {name: os.environ.get(name) for name in AI_ENV}
        for name in AI_ENV:
            os.environ.pop(name, None)
        self._backoff = gama_ai.RETRY_BACKOFF
        gama_ai.RETRY_BACKOFF = (0.0, 0.0)
        self.addCleanup(self._restore)
        _ChatHandler.requests = []
        _RedirectHandler.seen = 0
        self.sink_httpd, self.sink_thread = _start(_ChatHandler)
        self.addCleanup(_stop, self.sink_httpd, self.sink_thread)
        _RedirectHandler.target = _url(self.sink_httpd, "/steal")
        self.src_httpd, self.src_thread = _start(_RedirectHandler)
        self.addCleanup(_stop, self.src_httpd, self.src_thread)
        os.environ["GAMA_L_AI_URL"] = _url(self.src_httpd, "/v1")
        os.environ["GAMA_L_AI_ALLOW_HTTP"] = "1"
        os.environ["GAMA_L_AI_KEY"] = "secret"

    def _restore(self):
        gama_ai.RETRY_BACKOFF = self._backoff
        for name, value in self._cleaned.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_redirect_is_refused_and_key_is_not_replayed(self):
        with self.assertRaises(gama_ai.AIError) as cm:
            gama_ai.ask("Cao")
        self.assertIn("redirect", str(cm.exception).lower())
        self.assertEqual(_RedirectHandler.seen, 1)
        self.assertEqual(_ChatHandler.requests, [])

    def test_https_to_http_redirect_is_refused(self):
        handler = gama_ai._NoRedirectHandler()
        req = urllib.request.Request("https://example.test/v1")
        out = handler.redirect_request(
            req, None, 302, "Found", {}, "http://example.test/v1"
        )
        self.assertIsNone(out)

    def test_request_guard_rejects_a_foreign_final_url(self):
        class FakeResp:
            def __init__(self, url):
                self._url = url

            def geturl(self):
                return self._url

            def read(self, _n):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def __init__(self, final):
                self.final = final

            def open(self, req, timeout=None):
                return FakeResp(self.final)

        saved = gama_ai._OPENER
        self.addCleanup(setattr, gama_ai, "_OPENER", saved)
        gama_ai._OPENER = FakeOpener("http://evil.test/v1/chat/completions")
        with self.assertRaises(gama_ai.AIError) as cm:
            gama_ai._request("https://api.test/v1/chat/completions", b"{}", {}, 5)
        self.assertIn("redirect", str(cm.exception).lower())

    def test_cross_origin_origin_comparison(self):
        self.assertNotEqual(
            gama_ai._origin("https://a.test/x"), gama_ai._origin("https://b.test/x")
        )
        self.assertNotEqual(
            gama_ai._origin("https://a.test/x"), gama_ai._origin("http://a.test/x")
        )
        self.assertEqual(
            gama_ai._origin("https://a.test/x"), gama_ai._origin("https://A.TEST:443/y")
        )


class GamaRegistryHostTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get(REG_ENV)
        os.environ.pop(REG_ENV, None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop(REG_ENV, None)
        else:
            os.environ[REG_ENV] = self._old

    def test_loopback_is_private(self):
        self.assertTrue(R._host_is_private("127.0.0.1"))

    def test_cgnat_is_blocked(self):
        self.assertTrue(R._host_is_private("100.64.0.1"))
        with self.assertRaises(R.RegistryError):
            R.load_registry("http://100.64.0.1/index.json")

    def test_multicast_and_unspecified_are_blocked(self):
        self.assertTrue(R._host_is_private("224.0.0.1"))
        self.assertTrue(R._host_is_private("0.0.0.0"))

    def test_public_ip_is_not_private(self):
        self.assertFalse(R._host_is_private("8.8.8.8"))

    def test_dns_failure_fails_closed(self):
        # No real DNS traffic: force the resolver failure deterministically.
        with mock.patch.object(R.socket, "getaddrinfo", side_effect=OSError("nx")):
            self.assertTrue(R._host_is_private("unresolvable.example"))
            os.environ[REG_ENV] = "1"
            self.assertFalse(R._host_is_private("unresolvable.example"))


class GamaRegistryRedirectTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get(REG_ENV)
        self.addCleanup(self._restore)
        _StaticHandler.seen = 0

    def tearDown(self):
        if self._old is None:
            os.environ.pop(REG_ENV, None)
        else:
            os.environ[REG_ENV] = self._old

    def _restore(self):
        if self._old is None:
            os.environ.pop(REG_ENV, None)
        else:
            os.environ[REG_ENV] = self._old

    def test_allow_private_lets_loopback_through(self):
        os.environ[REG_ENV] = "1"
        class Local(_StaticHandler):
            body = json.dumps(VALID).encode("utf-8")
        httpd, thread = _start(Local)
        try:
            index = R.load_registry(_url(httpd, "/index.json"))
        finally:
            _stop(httpd, thread)
        self.assertEqual(len(index["packages"]), 1)

    def test_public_to_loopback_redirect_is_blocked(self):
        handler = R._GuardRedirectHandler()
        req = urllib.request.Request("http://93.184.216.34/index.json")
        with self.assertRaises(R.RegistryError):
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://127.0.0.1/index.json"
            )

    def test_https_to_http_redirect_is_blocked(self):
        handler = R._GuardRedirectHandler()
        req = urllib.request.Request("https://example.test/index.json")
        with self.assertRaises(R.RegistryError):
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://example.test/index.json"
            )

    def test_allowed_redirect_is_followed(self):
        os.environ[REG_ENV] = "1"
        class Target(_StaticHandler):
            body = json.dumps(VALID).encode("utf-8")
            seen = 0
        target_httpd, target_thread = _start(Target)
        class Hop(_RedirectHandler):
            target = _url(target_httpd, "/index.json")
        hop_httpd, hop_thread = _start(Hop)
        try:
            index = R.load_registry(_url(hop_httpd, "/index.json"))
        finally:
            _stop(hop_httpd, hop_thread)
            _stop(target_httpd, target_thread)
        self.assertEqual(Target.seen, 1)
        self.assertEqual(list(index["packages"]), ["math"])

    def test_missing_content_type_is_rejected(self):
        os.environ[REG_ENV] = "1"
        class NoType(_StaticHandler):
            content_type = None
            body = json.dumps(VALID).encode("utf-8")
        httpd, thread = _start(NoType)
        try:
            with self.assertRaises(R.RegistryError):
                R.load_registry(_url(httpd, "/index.json"))
        finally:
            _stop(httpd, thread)


if __name__ == "__main__":
    unittest.main()
