import json
import pathlib
import subprocess
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
URI = "file:///tmp/lsp_nav_demo.gm"

DOC = (
    "let demo = 1\n"
    "fn twice(a: Int) -> Int effects {} { return a * 2 }\n"
    "print twice(demo)\n"
)


class LspNavClient:
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


class LspNavigationTests(unittest.TestCase):
    def setUp(self):
        self.c = LspNavClient()
        mid = self.c.request("initialize", {"processId": None, "rootUri": None, "capabilities": {}})
        resp, _ = self.c.wait_for(mid=mid)
        self.caps = resp["result"]["capabilities"]
        self.c.notify("initialized", {})
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": DOC}})
        self.c.wait_for(method="textDocument/publishDiagnostics")

    def tearDown(self):
        self.c.close()

    def test_capabilities_definition_references(self):
        self.assertTrue(self.caps.get("definitionProvider"))
        self.assertTrue(self.caps.get("referencesProvider"))
        # existing capabilities are preserved
        self.assertIn("textDocumentSync", self.caps)
        self.assertIsNotNone(self.caps.get("completionProvider"))
        self.assertTrue(self.caps.get("hoverProvider"))

    def test_definition_points_to_let(self):
        # "print twice(demo)" -> demo starts at character 12
        did = self.c.request("textDocument/definition", {
            "textDocument": {"uri": URI},
            "position": {"line": 2, "character": 13},
        })
        resp, _ = self.c.wait_for(mid=did)
        loc = resp["result"]
        self.assertIsNotNone(loc)
        self.assertEqual(loc["uri"], URI)
        self.assertEqual(loc["range"]["start"]["line"], 0)
        self.assertGreaterEqual(loc["range"]["end"]["character"], loc["range"]["start"]["character"])

    def test_definition_points_to_fn(self):
        # "print twice(demo)" -> twice starts at character 6
        did = self.c.request("textDocument/definition", {
            "textDocument": {"uri": URI},
            "position": {"line": 2, "character": 7},
        })
        resp, _ = self.c.wait_for(mid=did)
        loc = resp["result"]
        self.assertIsNotNone(loc)
        self.assertEqual(loc["range"]["start"]["line"], 1)

    def test_references_include_declaration_and_use(self):
        rid = self.c.request("textDocument/references", {
            "textDocument": {"uri": URI},
            "position": {"line": 2, "character": 13},
            "context": {"includeDeclaration": True},
        })
        resp, _ = self.c.wait_for(mid=rid)
        locs = resp["result"]
        self.assertIsInstance(locs, list)
        self.assertGreaterEqual(len(locs), 2)
        self.assertIn(0, [loc["range"]["start"]["line"] for loc in locs])
        self.assertIn(2, [loc["range"]["start"]["line"] for loc in locs])
        for loc in locs:
            self.assertEqual(loc["uri"], URI)
            start = loc["range"]["start"]
            end = loc["range"]["end"]
            self.assertEqual(end["line"], start["line"])
            self.assertEqual(end["character"], start["character"] + 1)

    def test_references_declaration_name_not_keyword(self):
        rid = self.c.request("textDocument/references", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 5},
            "context": {"includeDeclaration": True},
        })
        resp, _ = self.c.wait_for(mid=rid)
        locs = resp["result"]
        decl = [l for l in locs if l["range"]["start"]["line"] == 0]
        self.assertEqual(len(decl), 1)
        # `let ` is 4 characters, so the declaration name starts at UTF-16 4
        self.assertEqual(decl[0]["range"]["start"]["character"], 4)

    def test_definition_unknown_word_is_null(self):
        # "Int" in the signature is not a let/fn declaration
        did = self.c.request("textDocument/definition", {
            "textDocument": {"uri": URI},
            "position": {"line": 1, "character": 13},
        })
        resp, _ = self.c.wait_for(mid=did)
        self.assertIsNone(resp["result"])

        sid = self.c.request("shutdown", {})
        sresp, _ = self.c.wait_for(mid=sid)
        self.assertIsNone(sresp["result"])


if __name__ == "__main__":
    unittest.main()
