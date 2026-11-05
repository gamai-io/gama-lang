"""LSP completion, hover, signature help and symbols for record methods.

Semantics under test:

* ``p.`` completes record methods (kind Method) alongside fields, with a
  declared signature in ``detail``.
* Hover on ``p.method`` / ``p.method(...)`` shows the method signature and the
  ``self`` binding, and hover on the declaration name works too.
* Signature help for ``p.method(`` lists arguments without ``self`` and tracks
  the active argument.
* Record declaration hover and document symbols include the methods.
* Receiver inference is the same best-effort kind used for fields (annotation,
  constructor call, record literal, parameter, ``self`` inside a method).
* Namespaced records (``geo.Point``) expose methods when the import resolves.
* Malformed declarations stay quiet instead of crashing.
"""
import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client

URI = "file:///tmp/lsp_record_methods.gm"

RECORD = (
    "record Point {\n"
    "  x: Int\n"
    "  y: Int\n"
    "  fn sum(self: Point) -> Int { return self.x + self.y }\n"
    "  fn scaled(self: Point, k: Int) -> Int { return self.x * k }\n"
    "  fn move(self: Point, dx: Int, dy: Int) -> Int {\n"
    "    return self.x + dx + dy\n"
    "  }\n"
    "}\n"
)
LET = "let p: Point = Point(1, 2)\n"
PRINT = "print p.\n"


class LspRecordMethodTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        self.c.wait_for(mid=mid)
        self.c.notify("initialized", {})
        self.uri = URI

    def tearDown(self):
        self.c.close()

    def open(self, text, uri=None):
        self.uri = uri or URI
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        self.c.wait_for(method="textDocument/publishDiagnostics")

    def change(self, text, version=2):
        self.c.notify("textDocument/didChange", {
            "textDocument": {"uri": self.uri, "version": version},
            "contentChanges": [{"text": text}],
        })
        self.c.wait_for(method="textDocument/publishDiagnostics")

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

    def symbols(self):
        mid = self.c.request("textDocument/documentSymbol", {
            "textDocument": {"uri": self.uri}})
        return self.c.wait_for(mid=mid)[0]["result"]

    def labels(self, result):
        return [item["label"] for item in result["items"]]

    def item(self, result, label):
        for entry in result["items"]:
            if entry["label"] == label:
                return entry
        self.fail("missing completion item %r" % label)

    # ----------------------------------------------------------- completion
    def test_completion_after_receiver_includes_methods(self):
        doc = RECORD + LET + PRINT
        self.open(doc)
        res = self.completion(10, len("print p."))
        self.assertEqual(set(self.labels(res)),
                         {"x", "y", "sum", "scaled", "move"})
        self.assertEqual(self.item(res, "x")["kind"], 5)
        for name in ("sum", "scaled", "move"):
            item = self.item(res, name)
            self.assertEqual(item["kind"], 2)  # Method
            self.assertIn("self: Point", item["detail"])
        self.assertEqual(
            self.item(res, "scaled")["detail"],
            "method scaled(self: Point, k: Int) -> Int")
        self.assertIn("record `Point`",
                      self.item(res, "sum")["documentation"])

    def test_completion_from_constructor_literal_and_parameter(self):
        doc = (RECORD +
               "let a = Point(1, 2)\n"
               "let b = Point { x: 3, y: 4 }\n"
               "fn show(v: Point) -> Int {\n"
               "  print v.\n"
               "  return 0\n"
               "}\n"
               "print a.\n"
               "print b.\n")
        self.open(doc)
        self.assertEqual(
            set(self.labels(self.completion(12, len("  print v.")))),
            {"x", "y", "sum", "scaled", "move"})
        self.assertEqual(
            set(self.labels(self.completion(15, len("print a.")))),
            {"x", "y", "sum", "scaled", "move"})
        self.assertEqual(
            set(self.labels(self.completion(16, len("print b.")))),
            {"x", "y", "sum", "scaled", "move"})

    def test_completion_after_self_inside_method(self):
        doc = (RECORD +
               "let p = Point(1, 2)\n")
        doc = doc.replace(
            "}\nlet p",
            "  fn use(self: Point) -> Int {\n"
            "    print self.\n"
            "    return 0\n"
            "  }\n}\nlet p")
        self.open(doc)
        line = doc.split("\n").index("    print self.")
        res = self.completion(line, len("    print self."))
        self.assertEqual(set(self.labels(res)),
                         {"x", "y", "sum", "scaled", "move", "use"})
        self.assertEqual(self.item(res, "sum")["kind"], 2)

    def test_generic_record_method_completion(self):
        doc = ("record Box<T> {\n"
               "  v: T\n"
               "  fn get(self: Box<T>) -> T { return self.v }\n"
               "}\n"
               "let b: Box<Int> = Box(1)\n"
               "print b.\n")
        self.open(doc)
        res = self.completion(5, len("print b."))
        self.assertEqual(set(self.labels(res)), {"v", "get"})
        self.assertEqual(self.item(res, "get")["kind"], 2)
        self.assertIn("self: Box<T>", self.item(res, "get")["detail"])

    # ---------------------------------------------------------------- hover
    def test_hover_on_method_and_call(self):
        doc = RECORD + LET + "print p.scaled\nprint p.scaled(2)\n"
        self.open(doc)
        plain = self.hover(10, len("print p.scaled"))["contents"]["value"]
        self.assertIn("Point.scaled(self: Point, k: Int) -> Int", plain)
        self.assertIn("method `scaled`", plain)
        self.assertIn("record `Point`", plain)
        self.assertIn("is bound to `p`", plain)
        self.assertIn("`self: Point`", plain)

        call = self.hover(11, len("print p.scaled"))["contents"]["value"]
        self.assertIn("Point.scaled(self: Point, k: Int) -> Int", call)

    def test_hover_method_declaration_and_record_declaration(self):
        doc = RECORD + LET
        self.open(doc)
        record = self.hover(0, len("record Po"))["contents"]["value"]
        self.assertIn("fn sum(self: Point) -> Int", record)
        self.assertIn("fn scaled(self: Point, k: Int) -> Int", record)
        self.assertIn("methods", record)
        self.assertIn("fields `x: Int`", record)

        decl = self.hover(4, len("  fn scaled"))["contents"]["value"]
        self.assertIn("Point.scaled(self: Point, k: Int) -> Int", decl)
        self.assertIn("method `scaled`", decl)
        self.assertNotIn("value `", decl)

    # ------------------------------------------------------- signature help
    def test_signature_help_excludes_self(self):
        doc = RECORD + LET + "print p.move(\n"
        self.open(doc)
        sig = self.signature(10, len("print p.move("))
        self.assertIsNotNone(sig)
        info = sig["signatures"][0]
        self.assertEqual(info["label"], "Point.move(dx: Int, dy: Int) -> Int")
        self.assertEqual([p["label"] for p in info["parameters"]],
                         ["dx: Int", "dy: Int"])
        self.assertIn("self` is the receiver",
                      info["documentation"]["value"])
        self.assertEqual(sig["activeParameter"], 0)

        self.change(RECORD + LET + "print p.move(1, \n")
        sig = self.signature(10, len("print p.move(1, "))
        self.assertEqual(sig["activeParameter"], 1)

    def test_signature_help_self_receiver(self):
        doc = (RECORD +
               "let p = Point(1, 2)\n")
        doc = doc.replace(
            "}\nlet p",
            "  fn use(self: Point) -> Int {\n"
            "    return self.move(\n"
            "  }\n}\nlet p")
        self.open(doc)
        line = doc.split("\n").index("    return self.move(")
        sig = self.signature(line, len("    return self.move("))
        self.assertIsNotNone(sig)
        self.assertEqual(sig["signatures"][0]["label"],
                         "Point.move(dx: Int, dy: Int) -> Int")
        self.assertEqual([p["label"] for p in
                          sig["signatures"][0]["parameters"]],
                         ["dx: Int", "dy: Int"])

    # ---------------------------------------------------- document symbols
    def test_document_symbols_include_record_methods(self):
        doc = RECORD + LET
        self.open(doc)
        syms = {sym["name"]: sym for sym in self.symbols()}
        self.assertIn("Point", syms)
        record = syms["Point"]
        self.assertEqual(record["kind"], 5)  # Class
        self.assertEqual(record["detail"], "record Point")
        names = [child["name"] for child in record["children"]]
        self.assertEqual(names, ["sum", "scaled", "move"])
        self.assertTrue(all(child["kind"] == 6 for child in record["children"]))
        self.assertEqual(record["children"][0]["detail"], "fn(sum)")

    # ----------------------------------------------------- namespaced record
    def test_namespaced_record_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "geo.gm").write_text(
                "record Point {\n"
                "  x: Int\n"
                "  fn sum(self: Point) -> Int { return self.x }\n"
                "}\n",
                encoding="utf-8",
            )
            uri = (root / "main.gm").as_uri()
            doc = ('import "geo.gm" as geo\n'
                   "let p: geo.Point = geo.Point(1)\n"
                   "print p.\n")
            self.open(doc, uri=uri)
            res = self.completion(2, len("print p."))
            self.assertEqual(set(self.labels(res)), {"x", "sum"})
            self.assertEqual(self.item(res, "sum")["kind"], 2)
            self.assertIn("self: Point", self.item(res, "sum")["detail"])

            self.change(doc.replace("print p.\n", "print p.sum\n"))
            hover = self.hover(2, len("print p.sum"))["contents"]["value"]
            self.assertIn("Point.sum(self: Point) -> Int", hover)

            self.change(doc.replace("print p.\n", "print p.sum(\n"))
            sig = self.signature(2, len("print p.sum("))
            self.assertEqual(sig["signatures"][0]["label"],
                             "Point.sum() -> Int")

    # --------------------------------------------------------- malformed
    def test_malformed_methods_stay_quiet(self):
        doc = ("record Point {\n"
               "  x: Int\n"
               "  fn broken(self: Point\n"
               "}\n"
               "let p: Point = Point(1)\n"
               "print p.\n")
        self.open(doc)
        res = self.completion(5, len("print p."))
        self.assertEqual(set(self.labels(res)), {"x"})
        self.change(doc.replace("print p.\n", "print p.broken\n"))
        self.assertIsNone(self.hover(5, len("print p.broken")))

        self.change("record Point {\nprint p.\n")
        res = self.completion(1, len("print p."))
        self.assertEqual(res["items"], [])
        self.assertIsNone(self.hover(1, len("print p")))


if __name__ == "__main__":
    unittest.main()
