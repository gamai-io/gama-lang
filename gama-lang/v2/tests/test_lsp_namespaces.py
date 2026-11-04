import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client


DOC = (
    'import "lib.gm" as math\n'
    'let inc = fn(x: Int) -> Int { return x + 1 }\n'
    'print math.add(2, 3)\n'
)


class LspNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "lib.gm").write_text(
            'let hidden = 7\n'
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n'
            'fn quad(x: Int) -> Int effects {} { return x * 4 }\n',
            encoding="utf-8",
        )
        self.uri = (self.root / "main.gm").as_uri()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        self.caps = self.c.wait_for(mid=mid)[0]["result"]["capabilities"]
        self.c.notify("initialized", {})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def open(self, text):
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        return self.c.wait_for(method="textDocument/publishDiagnostics")[0]

    def completion(self, line, character):
        mid = self.c.request("textDocument/completion", {
            "textDocument": {"uri": self.uri},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0]["result"]

    def hover(self, line, character):
        mid = self.c.request("textDocument/hover", {
            "textDocument": {"uri": self.uri},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0]["result"]

    def signature(self, line, character):
        mid = self.c.request("textDocument/signatureHelp", {
            "textDocument": {"uri": self.uri},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0]["result"]

    def test_capabilities_advertise_signature_help(self):
        self.assertIsNotNone(self.caps.get("signatureHelpProvider"))
        self.assertEqual(
            self.caps["signatureHelpProvider"]["triggerCharacters"], ["(", ","])
        # Existing completion trigger characters are unchanged.
        self.assertEqual(
            self.caps["completionProvider"]["triggerCharacters"],
            [" ", "|", "."])

    def test_completion_has_current_builtins_and_function_type(self):
        self.open(DOC)
        res = self.completion(1, len("let inc = fn"))
        labels = {it["label"] for it in res["items"]}
        for want in ("Function", "Result", "round", "split", "unwrap_or",
                     "as", "inc", "math"):
            self.assertIn(want, labels)
        kinds = {it["label"]: it["kind"] for it in res["items"]}
        self.assertEqual(kinds["Function"], 25)
        self.assertEqual(kinds["round"], 3)
        self.assertEqual(kinds["math"], 6)
        round_item = next(i for i in res["items"] if i["label"] == "round")
        self.assertIn("digits: Int", round_item["detail"])
        # No duplicates, preserving the existing invariant.
        self.assertEqual(len(labels), len(res["items"]))

    def test_alias_completion_from_module_filename(self):
        self.open('import "lib.gm" as \n')
        res = self.completion(0, len('import "lib.gm" as '))
        self.assertEqual([i["label"] for i in res["items"]], ["lib"])
        self.assertIn("namespace alias", res["items"][0]["detail"])

    def test_alias_completion_is_empty_when_name_collides(self):
        self.open('let lib = 1\nimport "lib.gm" as \n')
        res = self.completion(1, len('import "lib.gm" as '))
        self.assertEqual(res["items"], [])

    def test_member_completion_and_hover_for_namespaced_import(self):
        self.open(DOC)
        res = self.completion(2, len("print math."))
        labels = {it["label"] for it in res["items"]}
        self.assertEqual(labels, {"add", "quad"})  # top-level let is not exposed
        add_item = next(i for i in res["items"] if i["label"] == "add")
        self.assertIn("add(a: Int, b: Int) -> Int", add_item["detail"])

        alias = self.hover(2, 6)  # inside `math`
        self.assertIn("namespace alias `math`", alias["contents"]["value"])
        member = self.hover(2, 11)  # inside `add`
        self.assertIn("math.add", member["contents"]["value"])
        self.assertIn("a: Int, b: Int", member["contents"]["value"])

    def test_hover_function_type_builtin_and_lambda(self):
        self.open(
            'fn apply(f: Function, x: Int) -> Int { return f(x) }\n'
            'let inc = fn(x: Int) -> Int { return x + 1 }\n'
            'print round(1.234, 2)\n')
        function_type = self.hover(0, 13)  # inside `Function`
        self.assertIn("callable value", function_type["contents"]["value"])
        fn_decl = self.hover(0, 4)  # inside `apply`
        self.assertIn("fn apply(f: Function, x: Int) -> Int",
                      fn_decl["contents"]["value"])
        lambda_let = self.hover(1, 4)  # inside `inc`
        self.assertIn("function value `inc`",
                      lambda_let["contents"]["value"])
        builtin = self.hover(2, 7)  # inside `round`
        self.assertIn("round(value: Decimal, digits: Int) -> Decimal",
                      builtin["contents"]["value"])
        param = self.hover(0, 10)  # inside parameter `f`
        self.assertIn("parameter `f`: `Function`", param["contents"]["value"])

    def test_signature_help_builtin_and_namespaced_call(self):
        self.open(DOC)
        namespaced = self.signature(2, len("print math.add(2, "))
        self.assertIn("add(a: Int, b: Int) -> Int",
                      namespaced["signatures"][0]["label"])
        self.assertEqual(namespaced["activeParameter"], 1)

        self.c.notify("textDocument/didChange", {
            "textDocument": {"uri": self.uri, "version": 2},
            "contentChanges": [{"text": "print round(1.234, 2)\n"}],
        })
        self.c.wait_for(method="textDocument/publishDiagnostics")
        builtin = self.signature(0, len("print round(1.234, "))
        self.assertIn("round(value: Decimal, digits: Int) -> Decimal",
                      builtin["signatures"][0]["label"])
        self.assertEqual(builtin["activeParameter"], 1)


if __name__ == "__main__":
    unittest.main()
