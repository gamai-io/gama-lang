import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client


LIB = (
    "fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n"
)

MAIN = (
    'import "lib.gm"\n'
    "let total = add(1, 2)\n"
    "print total\n"
)


class LspDiagnosticsImportTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "lib.gm").write_text(LIB, encoding="utf-8")
        self.main = self.root / "main.gm"
        self.uri = self.main.as_uri()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        self.c.wait_for(mid=mid)[0]
        self.c.notify("initialized", {})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def open(self, text, uri=None):
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri or self.uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        return self.c.wait_for(method="textDocument/publishDiagnostics")[0]

    def change(self, text):
        self.c.notify("textDocument/didChange", {
            "textDocument": {"uri": self.uri, "version": 2},
            "contentChanges": [{"text": text}],
        })
        return self.c.wait_for(method="textDocument/publishDiagnostics")[0]

    def test_saved_import_resolves_and_has_no_diagnostics(self):
        self.main.write_text(MAIN, encoding="utf-8")
        diag = self.open(MAIN)
        self.assertEqual(diag["params"]["diagnostics"], [])

    def test_broken_import_still_reports_an_error(self):
        broken = 'import "missing.gm"\nprint 1\n'
        self.main.write_text(broken, encoding="utf-8")
        diag = self.open(broken)
        items = diag["params"]["diagnostics"]
        self.assertEqual(len(items), 1)
        self.assertIn("missing.gm", items[0]["message"])

    def test_untitled_import_is_not_reported_as_error(self):
        diag = self.open('import "lib.gm"\nprint 1\n', uri="untitled:Untitled-1")
        self.assertEqual(diag["params"]["diagnostics"], [])

    def test_untitled_type_error_is_still_reported(self):
        diag = self.open('let x: Int = "s"\n', uri="untitled:Untitled-2")
        items = diag["params"]["diagnostics"]
        self.assertEqual(len(items), 1)

    def test_unsaved_buffer_import_resolves_and_type_errors_survive(self):
        self.main.write_text(MAIN, encoding="utf-8")
        self.open(MAIN)
        edited = 'import "lib.gm"\nlet bad: Int = "s"\n'
        diag = self.change(edited)
        items = diag["params"]["diagnostics"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["range"]["start"]["line"], 1)


if __name__ == "__main__":
    unittest.main()
