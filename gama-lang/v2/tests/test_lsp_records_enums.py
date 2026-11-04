"""LSP completion and hover for GAMA-L records and enums.

Covers record/enum keywords and type names in general completion, record
fields after ``value.``, enum variants after ``Enum.``, declaration and member
hover (types only, never evaluation), nested records, namespaced records, and
malformed/missing declarations staying quiet instead of crashing.
"""
import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client

URI = "file:///tmp/lsp_records_enums.gm"

POINT = "record Point { x: Int, y: Int }\n"
COLOR = "enum Color { Red, Green, Blue }\n"


class LspRecordEnumTests(unittest.TestCase):
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

    def labels(self, result):
        return [item["label"] for item in result["items"]]

    def item(self, result, label):
        for entry in result["items"]:
            if entry["label"] == label:
                return entry
        self.fail("missing completion item %r" % label)

    # ---------------------------------------------------------------- basics
    def test_general_completion_has_record_and_enum_keywords_and_types(self):
        doc = POINT + COLOR + "let p: Point = Point(1, 2)\n"
        self.open(doc)
        res = self.completion(2, len("let p: Point = Point(1, 2)"))
        labels = self.labels(res)
        self.assertEqual(len(labels), len(set(labels)), "duplicate labels")
        for want in ("record", "enum", "Point", "Color", "let", "sql"):
            self.assertIn(want, labels)
        self.assertEqual(self.item(res, "record")["kind"], 14)
        self.assertEqual(self.item(res, "enum")["kind"], 14)
        self.assertEqual(self.item(res, "Point")["kind"], 7)
        self.assertIn("record Point", self.item(res, "Point")["detail"])
        self.assertEqual(self.item(res, "Color")["kind"], 13)
        self.assertIn("enum Color", self.item(res, "Color")["detail"])

    def test_record_field_completion_after_annotation(self):
        doc = POINT + "let p: Point = Point(1, 2)\nprint p.\n"
        self.open(doc)
        res = self.completion(2, len("print p."))
        self.assertEqual(set(self.labels(res)), {"x", "y"})
        self.assertEqual(self.item(res, "x")["kind"], 5)
        self.assertIn("field x: Int", self.item(res, "x")["detail"])
        self.assertIn("record `Point`",
                      self.item(res, "x")["documentation"])

    def test_record_field_completion_from_literal_and_parameter(self):
        doc = (POINT +
               "let p = Point { y: 4, x: 3 }\n"
               "fn show(v: Point) -> Int effects {} {\n"
               "  print v.\n"
               "  return 0\n"
               "}\n"
               "print p.\n")
        self.open(doc)
        self.assertEqual(
            set(self.labels(self.completion(3, len("  print v.")))),
            {"x", "y"})
        self.assertEqual(
            set(self.labels(self.completion(6, len("print p.")))),
            {"x", "y"})

    def test_nested_record_field_completion(self):
        doc = (POINT +
               "record Line { a: Point, b: Point }\n"
               "let l: Line = Line(Point(1, 2), Point(3, 4))\n"
               "print l.a.\n")
        self.open(doc)
        self.assertEqual(
            set(self.labels(self.completion(3, len("print l.a.")))),
            {"x", "y"})

    def test_enum_variant_completion_after_enum_name(self):
        doc = COLOR + "let c: Color = Color.Red\nprint Color.\n"
        self.open(doc)
        res = self.completion(2, len("print Color."))
        self.assertEqual(set(self.labels(res)), {"Red", "Green", "Blue"})
        self.assertEqual(self.item(res, "Red")["kind"], 20)
        self.assertEqual(self.item(res, "Red")["detail"], "Color.Red")
        self.assertIn("enum `Color`",
                      self.item(res, "Red")["documentation"])

    # ----------------------------------------------------------------- hover
    def test_hover_record_and_enum_declarations_and_members(self):
        doc = ("record Point { x: Int, y: Decimal }\n"
               "enum Color { Red, Green }\n"
               "let p: Point = Point(1, 2.5)\n"
               "print p.x\n"
               "print Color.Green\n")
        self.open(doc)

        record = self.hover(0, len("record Po"))["contents"]["value"]
        self.assertIn("record Point { x: Int, y: Decimal }", record)
        self.assertIn("`x: Int`", record)
        self.assertIn("`y: Decimal`", record)
        self.assertNotIn("value `", record)  # types only, never evaluation

        enum = self.hover(1, len("enum Co"))["contents"]["value"]
        self.assertIn("enum Color { Red, Green }", enum)
        self.assertIn("`Red`", enum)
        self.assertIn("`Green`", enum)

        annotated = self.hover(2, len("let p: Po"))["contents"]["value"]
        self.assertIn("GAMA-L record `Point`", annotated)

        field = self.hover(3, len("print p.x"))["contents"]["value"]
        self.assertIn("field `x: Int`", field)
        self.assertIn("record `Point`", field)
        self.assertNotIn("value `", field)

        variant = self.hover(4, len("print Color.Green"))["contents"]["value"]
        self.assertIn("enum variant `Green`", variant)
        self.assertIn("enum `Color`", variant)

    def test_hover_nested_and_parameter_fields(self):
        doc = (POINT +
               "record Line { a: Point, b: Point }\n"
               "let l: Line = Line(Point(1, 2), Point(3, 4))\n"
               "fn show(v: Point) -> Int effects {} {\n"
               "  print v.x\n"
               "  return 0\n"
               "}\n"
               "print l.a.x\n")
        self.open(doc)
        nested = self.hover(7, len("print l.a.x"))["contents"]["value"]
        self.assertIn("field `x: Int`", nested)
        self.assertIn("record `Point`", nested)
        param = self.hover(4, len("  print v.x"))["contents"]["value"]
        self.assertIn("field `x: Int`", param)

    # ----------------------------------------------------- namespaced record
    def test_namespaced_record_completion_and_hover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "geo.gm").write_text(
                "record Point { x: Int, y: Int }\n"
                "fn add(a: Int, b: Int) -> Int effects {} "
                "{ return a + b }\n",
                encoding="utf-8",
            )
            uri = (root / "main.gm").as_uri()
            doc = ('import "geo.gm" as geo\n'
                   "print geo.\n"
                   "print geo.Point(1, 2)\n")
            self.open(doc, uri=uri)
            res = self.completion(1, len("print geo."))
            self.assertEqual(set(self.labels(res)), {"Point", "add"})
            self.assertEqual(self.item(res, "Point")["kind"], 7)
            self.assertEqual(self.item(res, "add")["kind"], 3)
            hover = self.hover(2, len("print geo.Point"))["contents"]["value"]
            self.assertIn("geo.Point { x: Int, y: Int }", hover)
            self.assertIn("imported as `geo`", hover)

    # ------------------------------------------------- malformed / missing
    def test_malformed_declarations_do_not_crash(self):
        doc = "record Point {\nprint 1\n"
        self.open(doc)
        res = self.completion(1, len("print 1"))
        self.assertIn("record", self.labels(res))  # general items still work
        self.assertIsNone(self.hover(1, len("print 1")))
        self.assertIsNone(self.hover(0, len("record Po")))

        doc = "enum Color {\nprint Color.\n"
        self.change(doc)
        self.assertEqual(
            self.completion(1, len("print Color."))["items"], [])
        self.assertIsNone(self.hover(1, len("print Color")))

    def test_missing_declarations_are_quiet(self):
        doc = "let p = Point(1, 2)\nprint p.x\nprint Color.\n"
        self.open(doc)
        # `p` has no known record type and `Color` no enum declaration.
        self.assertEqual(
            self.completion(1, len("print p."))["items"], [])
        self.assertEqual(
            self.completion(2, len("print Color."))["items"], [])
        self.assertIsNone(self.hover(1, len("print p.x")))
        self.assertIsNone(self.hover(2, len("print Color")))


if __name__ == "__main__":
    unittest.main()
