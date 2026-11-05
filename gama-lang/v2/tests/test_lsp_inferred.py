"""Inferred signatures in LSP hover and signature help.

The core erases an omitted ``->``/``effects`` clause to ``Any``/empty and
checks bodies against that erasure; it does not expose inferred return types or
effect sets on the AST.  These tests pin the LSP's conservative best-effort
recomputation:

* an omitted return annotation shows ``(inferred: T)`` only when every
  reachable ``return`` expression has the same provable type;
* an omitted ``effects`` clause shows ``(inferred effects { ... })`` when the
  body calls an effectful builtin or a function with effects;
* explicit annotations and explicit effects are never rewritten;
* malformed documents stay quiet and never crash the server.

"""
import json
import pathlib
import sys
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import gama_lsp as L
from test_lsp_features import LspClient  # reuse the framed JSON-RPC client

URI = "file:///tmp/lsp_inferred.gm"


class InferredUnitTests(unittest.TestCase):
    def signature(self, text, name):
        node = L._decl_by_name(text, name)
        self.assertIsNotNone(node, name)
        return L.fn_signature(node, text)

    def test_omitted_return_is_inferred(self):
        text = "fn one() { return 1 }\n"
        self.assertEqual(self.signature(text, "one"),
                         "one() (inferred: Int)")

    def test_explicit_return_is_unchanged(self):
        text = "fn one() -> Int { return 1 }\n"
        self.assertEqual(self.signature(text, "one"), "one() -> Int")

    def test_call_through_declaration_is_inferred(self):
        text = "fn one() { return 1 }\nfn two() { return one() }\n"
        self.assertEqual(self.signature(text, "two"),
                         "two() (inferred: Int)")

    def test_text_and_bool_returns_are_inferred(self):
        text = ('fn label() { return "x" }\n'
                "fn flag() { return 1 < 2 }\n")
        self.assertEqual(self.signature(text, "label"),
                         "label() (inferred: Text)")
        self.assertEqual(self.signature(text, "flag"),
                         "flag() (inferred: Bool)")

    def test_mixed_returns_stay_quiet(self):
        text = ("fn pick(c: Bool) {\n"
                "  if c { return 1 }\n"
                "  return \"x\"\n"
                "}\n")
        self.assertEqual(self.signature(text, "pick"), "pick(c: Bool)")

    def test_no_return_stays_quiet(self):
        text = "fn log() { print \"x\" }\n"
        self.assertEqual(self.signature(text, "log"),
                         "log() (inferred effects { io.print })")

    def test_lambda_omitted_return_is_inferred(self):
        text = "let inc = fn(n: Int) { return n + 1 }\n"
        node = L._decl_by_name(text, "inc")
        self.assertEqual(L.lambda_signature(node.expr, text=text),
                         "fn(n: Int) (inferred: Int)")

    def test_method_return_is_inferred(self):
        text = ("record Point {\n"
                "  x: Int\n"
                "  y: Int\n"
                "  fn sum(self: Point) { return self.x + self.y }\n"
                "  fn label(self: Point) -> Text { return \"p\" }\n"
                "}\n")
        methods = {m.name: m for m in L._record_decls(text)["Point"].methods}
        self.assertEqual(
            L._method_signature_text(methods["sum"], "Point",
                                     qualifier="Point", text=text),
            "Point.sum(self: Point) (inferred: Int)")
        self.assertEqual(
            L._method_signature_text(methods["label"], "Point",
                                     qualifier="Point", text=text),
            "Point.label(self: Point) -> Text")

    def test_method_call_return_is_inferred(self):
        text = ("record Point {\n"
                "  x: Int\n"
                "  fn sum(self: Point) { return self.x }\n"
                "}\n"
                "fn caller(p: Point) { return p.sum() }\n")
        self.assertEqual(self.signature(text, "caller"),
                         "caller(p: Point) (inferred: Int)")

    def test_inferred_builtin_effect_and_return(self):
        text = 'fn fetch() { return sql("select 1") }\n'
        self.assertEqual(
            self.signature(text, "fetch"),
            "fetch() (inferred: Table) (inferred effects { sql.read })")

    def test_effect_propagates_through_call(self):
        text = ('fn log() { print "x" }\n'
                "fn outer() { log() }\n")
        self.assertEqual(self.signature(text, "outer"),
                         "outer() (inferred effects { io.print })")

    def test_explicit_effects_are_unchanged(self):
        text = ("fn emit() -> Int effects { io.print } "
                "{ print \"x\"\n  return 1 }\n")
        self.assertEqual(self.signature(text, "emit"),
                         "emit() -> Int effects { io.print }")

    def test_empty_explicit_effects_are_unchanged(self):
        text = "fn quiet() -> Int effects {} { return 1 }\n"
        self.assertEqual(self.signature(text, "quiet"), "quiet() -> Int")

    def test_generic_explicit_type_argument_infers_substituted_return(self):
        text = "fn id<T>(x: T) { return x }\nprint id<Int>("
        node = L._decl_by_name_at(text, "id", 1)
        sig = L._node_signature(node, ["Int"], text=text)
        self.assertEqual(sig[0], "id<Int>(x: Int) (inferred: Int)")

    def test_malformed_document_stays_quiet(self):
        text = "fn broken( {\n"
        self.assertIsNone(L._decl_by_name(text, "broken"))
        context = L._inference_context(text)
        self.assertEqual(context["funcs"], {})
        self.assertIsNone(L.hover(URI, text, 0, 4))
        self.assertIsNone(L.signature_help(URI, text, 0, len(text) - 1))


class InferredJsonRpcTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        self.c.wait_for(mid=mid)
        self.c.notify("initialized", {})
        self.uri = URI

    def tearDown(self):
        self.c.close()

    def open(self, text):
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        self.c.wait_for(method="textDocument/publishDiagnostics")

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

    def test_hover_and_signature_help_show_inferred_signature(self):
        doc = ("fn one() { return 1 }\n"
               "fn two() { return one() }\n"
               "print two(\n")
        self.open(doc)
        hover = self.hover(0, 4)
        self.assertIn("one() (inferred: Int)",
                      hover["contents"]["value"])
        sig = self.signature(2, len("print two("))
        self.assertEqual(sig["signatures"][0]["label"],
                         "two() (inferred: Int)")
        json.dumps(hover)
        json.dumps(sig)

    def test_method_call_signature_help_hides_self(self):
        doc = ("record Point {\n"
               "  x: Int\n"
               "  fn sum(self: Point) { return self.x }\n"
               "}\n"
               "let p = Point(1)\n"
               "print p.sum(\n")
        self.open(doc)
        sig = self.signature(5, len("print p.sum("))
        self.assertEqual(sig["signatures"][0]["label"],
                         "Point.sum() (inferred: Int)")
        self.assertEqual(sig["signatures"][0]["parameters"], [])

    def test_malformed_document_reports_no_signature(self):
        self.open("fn broken( {\nprint broken(\n")
        sig = self.signature(1, len("print broken("))
        self.assertIsNone(sig)


if __name__ == "__main__":
    unittest.main()
