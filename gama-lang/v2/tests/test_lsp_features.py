import json, pathlib, subprocess, sys, unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
URI = "file:///tmp/lsp_demo.gm"

DOC = "let demo = 1\nprint demo\n"


class LspClient:
    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, str(GAMAL2), "lsp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(V2),
        )
        self._id = 0

    def send(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        self.p.stdin.flush()

    def request(self, method, params):
        self._id += 1
        self.send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        return self._id

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def read(self):
        headers = {}
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise EOFError("server closed stdout")
            if line in (b"\r\n", b"\n"):
                break
            k, _, v = line.decode("ascii", "replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        return json.loads(self.p.stdout.read(n).decode("utf-8"))

    def wait_for(self, method=None, mid=None, limit=50):
        other = []
        for _ in range(limit):
            msg = self.read()
            if mid is not None and msg.get("id") == mid:
                return msg, other
            if method is not None and msg.get("method") == method:
                return msg, other
            other.append(msg)
        raise AssertionError("message not received: method=%r id=%r" % (method, mid))

    def close(self):
        try:
            self.send({"jsonrpc": "2.0", "id": 9_999_999, "method": "exit", "params": {}})
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


class LspFeatureTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()

    def tearDown(self):
        self.c.close()

    def test_did_change_diagnostics(self):
        c = self.c
        mid = c.request("initialize", {})
        c.wait_for(mid=mid)
        c.notify("initialized", {})
        c.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": "print 1\n"}})
        diag, _ = c.wait_for(method="textDocument/publishDiagnostics")
        self.assertEqual(diag["params"]["diagnostics"], [])
        c.notify("textDocument/didChange", {
            "textDocument": {"uri": URI, "version": 2},
            "contentChanges": [{"text": 'let x: Int = "s"\n'}],
        })
        diag, _ = c.wait_for(method="textDocument/publishDiagnostics")
        self.assertEqual(len(diag["params"]["diagnostics"]), 1)

    def test_capabilities_completion_hover(self):
        c = self.c
        mid = c.request("initialize", {"processId": None, "rootUri": None, "capabilities": {}})
        resp, _ = c.wait_for(mid=mid)
        caps = resp["result"]["capabilities"]
        self.assertIn("textDocumentSync", caps)
        self.assertIsNotNone(caps.get("completionProvider"))
        self.assertEqual(caps["completionProvider"].get("triggerCharacters"), [" ", "|", "."])
        self.assertTrue(caps.get("hoverProvider"))

        c.notify("initialized", {})
        c.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": DOC}})

        diag, _ = c.wait_for(method="textDocument/publishDiagnostics")
        self.assertEqual(diag["params"]["uri"], URI)
        self.assertEqual(diag["params"]["diagnostics"], [])

        cid = c.request("textDocument/completion", {
            "textDocument": {"uri": URI},
            "position": {"line": 1, "character": len("print demo")},
        })
        cresp, _ = c.wait_for(mid=cid)
        res = cresp["result"]
        self.assertFalse(res["isIncomplete"])
        labels = {it["label"] for it in res["items"]}
        for want in ("demo", "print", "sql", "let", "fn", "ask"):
            self.assertIn(want, labels)
        kinds = {it["label"]: it["kind"] for it in res["items"]}
        self.assertEqual(kinds["let"], 14)
        self.assertEqual(kinds["sql"], 3)
        self.assertEqual(kinds["demo"], 6)
        self.assertEqual(len(labels), len(res["items"]), "duplicate labels")

        hid = c.request("textDocument/hover", {
            "textDocument": {"uri": URI},
            "position": {"line": 1, "character": 1},
        })
        hresp, _ = c.wait_for(mid=hid)
        self.assertEqual(hresp["result"]["contents"]["kind"], "markdown")
        self.assertIn("print", hresp["result"]["contents"]["value"])

        hid = c.request("textDocument/hover", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 5},
        })
        hresp, _ = c.wait_for(mid=hid)
        self.assertIn("demo", hresp["result"]["contents"]["value"])

        hid = c.request("textDocument/hover", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 0},
        })
        hresp, _ = c.wait_for(mid=hid)
        self.assertIn("keyword", hresp["result"]["contents"]["value"])

        hid = c.request("textDocument/hover", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 99},
        })
        hresp, _ = c.wait_for(mid=hid)
        self.assertIsNone(hresp["result"])

        sid = c.request("shutdown", {})
        sresp, _ = c.wait_for(mid=sid)
        self.assertIsNone(sresp["result"])


if __name__ == "__main__":
    unittest.main()
