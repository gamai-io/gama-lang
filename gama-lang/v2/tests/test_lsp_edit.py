import json
import pathlib
import subprocess
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
URI = "file:///tmp/lsp_edit_demo.gm"

sys.path.insert(0, str(V2))
import gamal2 as G  # noqa: E402

DOC = (
    "let demo = 1\n"
    "print demo\n"
    "fn twice(a: Int) -> Int effects {} { return a * 2 }\n"
    "print twice(demo)\n"
)

UNFORMATTED = (
    "let   demo=1\n"
    "print   demo\n"
    "fn twice(a: Int)->Int effects {} { return a*2 }\n"
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
        self.send({"jsonrpc": "2.0", "id": self._id, "method": method,
                   "params": params})
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
        raise AssertionError("message not received: method=%r id=%r"
                             % (method, mid))

    def open(self, text):
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": text}})
        self.wait_for(method="textDocument/publishDiagnostics")

    def change(self, text, version=2):
        self.notify("textDocument/didChange", {
            "textDocument": {"uri": URI, "version": version},
            "contentChanges": [{"text": text}]})
        self.wait_for(method="textDocument/publishDiagnostics")

    def close(self):
        try:
            mid = self.request("shutdown", {})
            self.wait_for(mid=mid)
        except Exception:
            pass
        try:
            self.notify("exit", {})
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()
        for stream in (self.p.stdin, self.p.stdout, self.p.stderr):
            try:
                stream.close()
            except Exception:
                pass


class LspEditingTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {"processId": None, "rootUri": None,
                                            "capabilities": {}})
        msg, _ = self.c.wait_for(mid=mid)
        self.caps = msg["result"]["capabilities"]

    def tearDown(self):
        self.c.close()

    def test_initialize_capabilities(self):
        self.assertTrue(self.caps.get("renameProvider"))
        self.assertTrue(self.caps.get("documentFormattingProvider"))

    def test_rename_declaration_and_references(self):
        self.c.open(DOC)
        mid = self.c.request("textDocument/rename", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 4},
            "newName": "broj",
        })
        msg, _ = self.c.wait_for(mid=mid)
        edit = msg.get("result")
        self.assertIsInstance(edit, dict)
        changes = edit.get("changes") or {}
        edits = changes.get(URI)
        self.assertIsInstance(edits, list)
        self.assertEqual(len(edits), 3)
        for e in edits:
            self.assertEqual(e["newText"], "broj")
        positions = sorted((e["range"]["start"]["line"],
                            e["range"]["start"]["character"]) for e in edits)
        self.assertEqual(positions, [(0, 4), (1, 6), (3, 12)])

    def test_rename_unknown_word_returns_null(self):
        self.c.open(DOC)
        mid = self.c.request("textDocument/rename", {
            "textDocument": {"uri": URI},
            "position": {"line": 2, "character": 16},  # Int
            "newName": "Broj",
        })
        msg, _ = self.c.wait_for(mid=mid)
        self.assertIsNone(msg.get("result"))

    def test_rename_invalid_name_returns_null(self):
        self.c.open(DOC)
        mid = self.c.request("textDocument/rename", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 4},
            "newName": "1bad name",
        })
        msg, _ = self.c.wait_for(mid=mid)
        self.assertIsNone(msg.get("result"))

    def test_formatting_returns_whole_document_edit(self):
        self.c.open(DOC)
        mid = self.c.request("textDocument/formatting", {
            "textDocument": {"uri": URI},
            "options": {"tabSize": 2, "insertSpaces": True},
        })
        msg, _ = self.c.wait_for(mid=mid)
        self.assertEqual(msg.get("result"), [])

        self.c.change(UNFORMATTED)
        mid = self.c.request("textDocument/formatting", {
            "textDocument": {"uri": URI},
            "options": {"tabSize": 2, "insertSpaces": True},
        })
        msg, _ = self.c.wait_for(mid=mid)
        edits = msg.get("result")
        self.assertIsInstance(edits, list)
        self.assertEqual(len(edits), 1)
        edit = edits[0]
        self.assertTrue(edit["newText"].startswith("let demo"))
        self.assertEqual(edit["newText"], G.fmt_source(UNFORMATTED))
        self.assertEqual(edit["range"]["start"], {"line": 0, "character": 0})
        lines = UNFORMATTED.split("\n")
        self.assertEqual(edit["range"]["end"],
                         {"line": len(lines) - 1,
                          "character": len(lines[-1].encode("utf-16-le")) // 2})

    def test_rename_after_change_still_works(self):
        self.c.open(DOC)
        self.c.change(UNFORMATTED)
        mid = self.c.request("textDocument/rename", {
            "textDocument": {"uri": URI},
            "position": {"line": 0, "character": 6},
            "newName": "broj",
        })
        msg, _ = self.c.wait_for(mid=mid)
        edits = (msg.get("result") or {}).get("changes", {}).get(URI)
        self.assertEqual(len(edits), 3)
        self.assertTrue(all(e["newText"] == "broj" for e in edits))


if __name__ == "__main__":
    unittest.main()
