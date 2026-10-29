"""Robustness tests for the GAMA-L 2 language server.

Covers malformed JSON-RPC framing, non-object params, invalid Content-Length
headers, multiline string line/column tracking, and shutdown/exit.
"""
import json
import os
import pathlib
import select
import subprocess
import sys
import time
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
URI = "file:///tmp/lsp_robust.gm"


class LspClient:
    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, str(GAMAL2), "lsp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(V2),
        )
        self._id = 0
        self.exited = False
        # Read through the raw fd so our own buffer stays the single source of
        # truth; mixing select() with BufferedReader.readline() can hide data
        # that the buffered reader already prefetched.
        self._fd = self.p.stdout.fileno()
        self._buf = bytearray()

    # ------------------------------------------------------------ writing
    def send_raw(self, raw: bytes):
        self.p.stdin.write(raw)
        self.p.stdin.flush()

    def send(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_raw(b"Content-Length: %d\r\n\r\n" % len(data) + data)

    def request(self, method, params):
        self._id += 1
        self.send({"jsonrpc": "2.0", "id": self._id,
                   "method": method, "params": params})
        return self._id

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------ reading
    def _fill(self, deadline, what):
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for %s" % what)
        ready, _, _ = select.select([self._fd], [], [], remaining)
        if not ready:
            raise TimeoutError("timed out waiting for %s" % what)
        chunk = os.read(self._fd, 65536)
        if not chunk:
            raise EOFError("server closed stdout")
        self._buf.extend(chunk)

    def _line(self, deadline):
        while b"\n" not in self._buf:
            self._fill(deadline, "server output")
        idx = self._buf.index(b"\n") + 1
        line = bytes(self._buf[:idx])
        del self._buf[:idx]
        return line

    def _read_exact(self, n, deadline):
        while len(self._buf) < n:
            self._fill(deadline, "body")
        body = bytes(self._buf[:n])
        del self._buf[:n]
        return body

    def read(self, timeout=5.0):
        deadline = time.time() + timeout
        headers = {}
        while True:
            line = self._line(deadline)
            if line in (b"\r\n", b"\n"):
                break
            k, _, v = line.decode("ascii", "replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        body = self._read_exact(n, deadline)
        return json.loads(body.decode("utf-8"))

    def wait_for(self, mid=None, method=None, limit=80, timeout=5.0):
        others = []
        for _ in range(limit):
            msg = self.read(timeout)
            if mid is not None and msg.get("id") == mid and (
                    "result" in msg or "error" in msg):
                return msg, others
            if method is not None and msg.get("method") == method:
                return msg, others
            others.append(msg)
        raise AssertionError(
            "message not received: mid=%r method=%r" % (mid, method))

    def initialize(self):
        mid = self.request("initialize", {})
        return self.wait_for(mid=mid)

    def open_doc(self, text):
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": text}})
        msg, _ = self.wait_for(method="textDocument/publishDiagnostics")
        return msg["params"]["diagnostics"]

    def hover(self, line, character):
        mid = self.request("textDocument/hover", {
            "textDocument": {"uri": URI},
            "position": {"line": line, "character": character}})
        msg, _ = self.wait_for(mid=mid)
        return msg

    def close(self):
        if self.p.poll() is None:
            try:
                self.notify("exit", {})
            except (BrokenPipeError, OSError):
                pass
        try:
            self.p.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=5)
        for stream in (self.p.stdout, self.p.stderr):
            try:
                stream.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        self.exited = True


class LspRobustTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()

    def tearDown(self):
        self.c.close()

    # ---------------------------------------------------------------- tests
    def test_malformed_json_then_valid_request(self):
        # Invalid JSON body: must produce a parse error, not EOF.
        self.c.send_raw(b"Content-Length: 5\r\n\r\n{bad}")
        msg, others = self.c.initialize()
        self.assertIn("result", msg)
        codes = [m.get("error", {}).get("code") for m in others]
        self.assertIn(-32700, codes)

        # The server is still alive and answers a follow-up request.
        self.c.open_doc("let x = 1\nprint x\n")
        reply = self.c.hover(1, 6)
        self.assertIn("result", reply)
        self.assertIsNotNone(reply["result"])

    def test_non_object_params_does_not_crash(self):
        mid = self.c.request("initialize", "oops")
        msg, _ = self.c.wait_for(mid=mid)
        self.assertIn("error", msg)
        self.assertEqual(msg["error"]["code"], -32602)

        # A valid request after the bad one still works.
        msg, _ = self.c.initialize()
        self.assertIn("result", msg)
        self.c.open_doc("let x = 1\nprint x\n")
        self.assertIsNotNone(self.c.hover(1, 6)["result"])

    def test_invalid_content_length_headers(self):
        self.c.send_raw(b"Content-Length: -1\r\n\r\n")
        self.c.send_raw(b"Content-Length: abc\r\n\r\n")
        msg, others = self.c.initialize()
        self.assertIn("result", msg)
        codes = [m.get("error", {}).get("code") for m in others]
        self.assertGreaterEqual(codes.count(-32700), 2)

    def test_multiline_string_advances_line_and_col(self):
        self.c.initialize()
        text = 'let s = "a\nb"\nprint y\n'
        diags = self.c.open_doc(text)
        self.assertEqual(len(diags), 1, diags)
        start = diags[0]["range"]["start"]
        self.assertEqual(start["line"], 2)
        self.assertEqual(start["character"], 6)
        self.assertIn("y", diags[0]["message"])

    def test_shutdown_and_exit(self):
        self.c.initialize()
        mid = self.c.request("shutdown", {})
        msg, _ = self.c.wait_for(mid=mid)
        self.assertIn("result", msg)
        self.assertIsNone(msg["result"])
        self.c.notify("exit", {})
        self.c.p.wait(timeout=5)
        self.c.exited = True
        self.assertEqual(self.c.p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
