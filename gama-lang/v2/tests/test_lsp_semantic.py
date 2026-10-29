import json
import pathlib
import subprocess
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
sys.path.insert(0, str(V2))
URI = "file:///tmp/lsp_semantic_demo.gm"

DOC = (
    "let demo = 1\n"
    "fn twice(a: Int) -> Int effects {} { return a * 2 }\n"
    "print twice(demo)\n"
)


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
        body = self.p.stdout.read(n)
        if not body:
            raise EOFError("server closed stdout")
        return json.loads(body.decode("utf-8"))

    def wait_for(self, method=None, mid=None, limit=100):
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


class LspSemanticTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {"processId": None, "rootUri": None, "capabilities": {}})
        resp, _ = self.c.wait_for(mid=mid)
        self.caps = resp["result"]["capabilities"]
        self.c.notify("initialized", {})
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": DOC}})
        self.c.wait_for(method="textDocument/publishDiagnostics")

    def tearDown(self):
        self.c.close()

    def test_capabilities(self):
        self.assertTrue(self.caps.get("documentSymbolProvider"))
        stp = self.caps.get("semanticTokensProvider")
        self.assertIsNotNone(stp)
        self.assertTrue(stp.get("full"))
        legend = stp.get("legend") or {}
        self.assertEqual(
            legend.get("tokenTypes"),
            ["keyword", "type", "function", "variable", "string", "number", "operator"],
        )
        self.assertEqual(legend.get("tokenModifiers"), [])
        # existing capabilities preserved
        self.assertIn("textDocumentSync", self.caps)
        self.assertIsNotNone(self.caps.get("completionProvider"))
        self.assertTrue(self.caps.get("hoverProvider"))
        self.assertTrue(self.caps.get("definitionProvider"))
        self.assertTrue(self.caps.get("referencesProvider"))

    def test_document_symbols(self):
        mid = self.c.request("textDocument/documentSymbol", {"textDocument": {"uri": URI}})
        resp, _ = self.c.wait_for(mid=mid)
        syms = resp["result"]
        self.assertIsInstance(syms, list)
        names = {s["name"]: s for s in syms}
        self.assertIn("demo", names)
        self.assertIn("twice", names)
        self.assertEqual(names["demo"]["kind"], 13)
        self.assertEqual(names["twice"]["kind"], 12)
        self.assertEqual(names["twice"]["detail"], "fn(twice)")
        self.assertEqual(names["demo"]["detail"], "let(demo)")
        self.assertEqual(names["demo"]["range"]["start"]["line"], 0)
        self.assertEqual(names["twice"]["range"]["start"]["line"], 1)
        # selectionRange sits on the name, after `let `/`fn `
        self.assertEqual(names["demo"]["selectionRange"]["start"]["character"], 4)
        self.assertEqual(names["twice"]["selectionRange"]["start"]["character"], 3)
        self.assertEqual(names["twice"]["children"], [])

    def test_semantic_tokens(self):
        mid = self.c.request("textDocument/semanticTokens/full", {"textDocument": {"uri": URI}})
        resp, _ = self.c.wait_for(mid=mid)
        data = resp["result"]["data"]
        self.assertIsInstance(data, list)
        self.assertTrue(data)
        self.assertEqual(len(data) % 5, 0)
        types = [data[i + 3] for i in range(0, len(data), 5)]
        self.assertIn(0, types)  # keyword
        self.assertIn(5, types)  # number
        # decoded spans stay non-negative and on real lines
        line = 0
        start = 0
        for i in range(0, len(data), 5):
            dl, ds, length, ttype, _mods = data[i:i + 5]
            self.assertGreaterEqual(length, 1)
            if dl == 0:
                start = start + ds if i else ds
            else:
                line += dl
                start = ds
            self.assertGreaterEqual(start, 0)
            self.assertLess(line, len(DOC.split("\n")))

    def test_string_token_span(self):
        import gama_lsp
        res = gama_lsp.semantic_tokens(URI, 'let y = "abc" + 1\n')
        data = res["data"]
        # find tokenType 4 (string)
        line = 0; start = 0; strings = []
        for i in range(0, len(data), 5):
            dl, ds, length, ttype, _mods = data[i:i+5]
            line += dl
            start = start + ds if dl == 0 else ds
            if ttype == 4:
                strings.append((line, start, length))
        self.assertEqual(strings, [(0, 8, 5)])  # "abc" including quotes
    def test_shutdown(self):
        sid = self.c.request("shutdown", {})
        sresp, _ = self.c.wait_for(mid=sid)
        self.assertIsNone(sresp["result"])


if __name__ == "__main__":
    unittest.main()
