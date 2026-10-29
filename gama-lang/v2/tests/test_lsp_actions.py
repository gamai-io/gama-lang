import json
import pathlib
import subprocess
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
URI = "file:///tmp/lsp_actions.gm"

BAD = 'let x: Int = "s"\n'
BAD_LINE = BAD.split("\n")[0]
COMMENTED = '# let x: Int = "s"\n'


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


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
        raise AssertionError(
            "message not received: method=%r id=%r" % (method, mid))

    def close(self):
        try:
            self.send({"jsonrpc": "2.0", "id": 9_999_999,
                       "method": "exit", "params": {}})
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


class LspCodeActionTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()

    def tearDown(self):
        self.c.close()

    def _initialize(self):
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        resp, _ = self.c.wait_for(mid=mid)
        return resp["result"]

    def _open(self, text):
        self.c.notify("initialized", {})
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1,
            "text": text}})
        diag, _ = self.c.wait_for(method="textDocument/publishDiagnostics")
        return diag["params"]["diagnostics"]

    def _code_actions(self, line, ch_start, ch_end, diagnostics=None):
        params = {
            "textDocument": {"uri": URI},
            "range": {"start": {"line": line, "character": ch_start},
                      "end": {"line": line, "character": ch_end}},
        }
        if diagnostics is not None:
            params["context"] = {"diagnostics": diagnostics}
        mid = self.c.request("textDocument/codeAction", params)
        resp, _ = self.c.wait_for(mid=mid)
        return resp["result"]

    def test_initialize_advertises_code_action_provider(self):
        result = self._initialize()
        caps = result["capabilities"]
        self.assertEqual(caps.get("codeActionProvider"),
                         {"codeActionKinds": ["quickfix"]})

    def test_quickfix_comments_out_error_line(self):
        result = self._initialize()
        self.assertIn("codeActionProvider", result["capabilities"])
        diags = self._open(BAD)
        self.assertEqual(len(diags), 1)

        actions = self._code_actions(0, 0, u16(BAD_LINE), diags)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["title"], "Comment out line 1")
        self.assertEqual(action["kind"], "quickfix")
        self.assertEqual(len(action["diagnostics"]), 1)
        changes = action["edit"]["changes"]
        self.assertIn(URI, changes)
        edits = changes[URI]
        self.assertEqual(len(edits), 1)
        edit = edits[0]
        self.assertEqual(edit["newText"], "# " + BAD_LINE)
        self.assertEqual(edit["range"]["start"],
                         {"line": 0, "character": 0})
        self.assertEqual(edit["range"]["end"],
                         {"line": 0, "character": u16(BAD_LINE)})

    def test_code_action_without_context_computes_diagnostics(self):
        self._initialize()
        diags = self._open(BAD)
        self.assertEqual(len(diags), 1)

        actions = self._code_actions(0, 0, u16(BAD_LINE))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["edit"]["changes"][URI][0]["newText"],
                         "# " + BAD_LINE)

    def test_no_actions_for_clean_line(self):
        self._initialize()
        self._open(BAD)

        # line 1 is empty: no diagnostics there
        self.assertEqual(self._code_actions(1, 0, 0, []), [])
        self.assertEqual(self._code_actions(1, 0, 0), [])

    def test_already_commented_line_is_not_modified(self):
        self._initialize()
        diags = self._open(COMMENTED)
        self.assertEqual(diags, [])

        # fabricate a diagnostic on a line that is already a comment
        fake = {"range": {"start": {"line": 0, "character": 0},
                          "end": {"line": 0, "character": 1}},
                "severity": 1, "source": "gama-l", "message": "fake"}
        self.assertEqual(self._code_actions(0, 0, u16(BAD_LINE), [fake]), [])

    def test_duplicate_diagnostics_on_one_line_yield_single_action(self):
        self._initialize()
        diags = self._open(BAD)
        self.assertEqual(len(diags), 1)
        dup = [diags[0], diags[0]]

        actions = self._code_actions(0, 0, u16(BAD_LINE), dup)
        self.assertEqual(len(actions), 1)

    def test_shutdown(self):
        self._initialize()
        mid = self.c.request("shutdown", {})
        resp, _ = self.c.wait_for(mid=mid)
        self.assertIsNone(resp["result"])


if __name__ == "__main__":
    unittest.main()
