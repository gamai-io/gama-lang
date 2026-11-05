"""LSP completion and hover for record ``with { ... }`` updates.

Semantics under test:

* Completion in the field slot of ``p with { <cursor>`` offers the receiver
  record's fields (including inherited ones) and never methods.
* A nested receiver such as ``ln.a with {`` resolves through the field type;
  a best-effort constructor receiver (``Point(1, 2) with {``) works too.
* A dotted path inside the update (``ln with { a.``) resolves that field's
  record type like the ordinary member resolver.
* A value position (after ``=``) keeps the ordinary completion list; it is
  not treated as a field slot.
* Hover on a field key shows its type and declaring record, including the
  inherited-by marker; hovering a value identifier is not a field hover.
* Malformed or unresolved receivers stay quiet (empty completion, no hover)
  and never crash.
"""
import json
import pathlib
import sys
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client

URI = "file:///tmp/lsp_record_with.gm"

POINT = (
    "record Point {\n"
    "  x: Int\n"
    "  y: Int\n"
    "  fn sum(self: Point) -> Int { return self.x + self.y }\n"
    "}\n"
)
LINE = (
    "record Line {\n"
    "  a: Point\n"
    "  b: Point\n"
    "}\n"
)
CHILD = (
    "record Child extends Point {\n"
    "  z: Int\n"
    "}\n"
)
VALUES = (
    "let p: Point = Point(1, 2)\n"
    "let c: Child = Child(1, 2, 3)\n"
    "let ln: Line = Line(p, p)\n"
)
BASE = POINT + LINE + CHILD + VALUES
# BASE has 15 lines (0..14); tests append update statements below.
WITH_LINE = 15


class LspRecordWithTests(unittest.TestCase):
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

    def at(self, method, line, character):
        mid = self.c.request(method, {
            "textDocument": {"uri": self.uri},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0].get("result")

    def completion(self, line, character):
        return self.at("textDocument/completion", line, character)

    def hover(self, line, character):
        result = self.at("textDocument/hover", line, character)
        return result["contents"]["value"] if result else None

    def labels(self, result):
        return [item["label"] for item in result["items"]]

    def item(self, result, label):
        for entry in result["items"]:
            if entry["label"] == label:
                return entry
        self.fail("missing completion item %r" % label)

    def doc(self, line_text):
        return BASE + line_text + "\n"

    def assert_quiet(self, text, line, character):
        result = self.completion(line, character)
        self.assertEqual(result["items"], [])

    # ------------------------------------------------------------ completion
    def test_field_slot_offers_fields_only(self):
        text = "let q = p with { "
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y"})
        self.assertNotIn("sum", self.labels(res))
        self.assertEqual(self.item(res, "x")["kind"], 5)   # Field
        self.assertEqual(self.item(res, "x")["detail"], "field x: Int")
        self.assertIn("Field `x` of record `Point`",
                      self.item(res, "x")["documentation"])

    def test_partial_field_name_still_offers_fields(self):
        text = "let q = p with { y"
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y"})

    def test_inherited_fields_are_offered_and_marked(self):
        text = "let q = c with { "
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y", "z"})
        self.assertNotIn("sum", self.labels(res))
        self.assertIn("inherited from Point",
                      self.item(res, "x")["detail"])
        self.assertIn("inherited from Point",
                      self.item(res, "y")["detail"])
        self.assertNotIn("inherited", self.item(res, "z")["detail"])

    def test_nested_receiver_field_slot(self):
        text = "let q = ln.a with { "
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y"})
        self.assertNotIn("sum", self.labels(res))

    def test_constructor_receiver_field_slot(self):
        text = "let q = Point(1, 2) with { "
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y"})

    def test_multiline_with_field_slot(self):
        text = "let q = p with {\n  \n}"
        self.open(self.doc(text))
        res = self.completion(WITH_LINE + 1, 2)
        self.assertEqual(set(self.labels(res)), {"x", "y"})

    def test_nested_field_path_resolves_members(self):
        text = "let q = ln with { a."
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y", "sum"})
        self.assertEqual(self.item(res, "sum")["kind"], 2)  # Method

    def test_nested_field_path_after_comma(self):
        text = "let q = ln with { a.x = 1, b."
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y", "sum"})

    def test_value_position_keeps_ordinary_members(self):
        text = "let q = p with { x = p."
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y", "sum"})

    def test_unresolved_receiver_is_quiet(self):
        self.open(self.doc("let q = nope with { "))
        self.assert_quiet(BASE, WITH_LINE, len("let q = nope with { "))

    def test_unresolved_nested_path_is_quiet(self):
        self.open(self.doc("let q = nope with { a."))
        self.assert_quiet(BASE, WITH_LINE, len("let q = nope with { a."))

    def test_self_receiver_inside_method(self):
        method_doc = (
            "record Point {\n"
            "  x: Int\n"
            "  y: Int\n"
            "  fn bump(self: Point) -> Point {\n"
            "    return self with { \n"
            "    }\n"
            "  }\n"
            "}\n"
        )
        self.open(method_doc)
        res = self.completion(4, len("    return self with { "))
        self.assertEqual(set(self.labels(res)), {"x", "y"})
        hover_doc = method_doc.replace(
            "    return self with { \n    }",
            "    return self with { x = 1 }")
        row = "    return self with { x = 1 }"
        self.change(hover_doc)
        value = self.hover(4, row.index("x = 1") + 1)
        self.assertIn("`self.x`", value)
        self.assertIn("field `x: Int` of record `Point`", value)

    def test_plain_member_completion_is_unchanged(self):
        text = "print p."
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y", "sum"})

    # ---------------------------------------------------------------- hover
    def test_hover_field_key_shows_type_and_record(self):
        text = "let q = p with { x = 3 }"
        self.open(self.doc(text))
        col = text.index("x = 3") + 1
        value = self.hover(WITH_LINE, col)
        self.assertIn("`p.x`", value)
        self.assertIn("field `x: Int` of record `Point`", value)
        self.assertNotIn("inherited", value)

    def test_hover_inherited_field_key_names_declaring_record(self):
        text = "let q = c with { x = 3 }"
        self.open(self.doc(text))
        col = text.index("x = 3") + 1
        value = self.hover(WITH_LINE, col)
        self.assertIn("field `x: Int` of record `Point`", value)
        self.assertIn("inherited by `Child`", value)

    def test_hover_nested_receiver_key(self):
        text = "let q = ln.a with { y = 3 }"
        self.open(self.doc(text))
        col = text.index("y = 3") + 1
        value = self.hover(WITH_LINE, col)
        self.assertIn("`ln.a.y`", value)
        self.assertIn("field `y: Int` of record `Point`", value)

    def test_hover_second_key_after_comma(self):
        text = "let q = p with { x = 1, y = 2 }"
        self.open(self.doc(text))
        col = text.index("y = 2") + 1
        value = self.hover(WITH_LINE, col)
        self.assertIn("field `y: Int` of record `Point`", value)

    def test_hover_value_identifier_is_not_field_key(self):
        text = "let q = p with { x = y }"
        self.open(self.doc(text))
        col = text.index("y }") + 1
        self.assertIsNone(self.hover(WITH_LINE, col))

    def test_hover_unresolved_receiver_is_quiet(self):
        text = "let q = nope with { x = 3 }"
        self.open(self.doc(text))
        col = text.index("x = 3") + 1
        self.assertIsNone(self.hover(WITH_LINE, col))

    # ---------------------------------------------------- JSON / smoke shape
    def test_completion_payload_is_json_serializable(self):
        self.open(self.doc("let q = p with { "))
        res = self.completion(WITH_LINE, len("let q = p with { "))
        json.dumps(res)
        self.assertIn("isIncomplete", res)
        for entry in res["items"]:
            self.assertIn("label", entry)
            self.assertIn("kind", entry)
            self.assertIn("detail", entry)
            self.assertIn("documentation", entry)

    def test_unclosed_with_does_not_crash(self):
        text = "let q = p with {"
        self.open(self.doc(text))
        res = self.completion(WITH_LINE, len(text))
        self.assertEqual(set(self.labels(res)), {"x", "y"})


if __name__ == "__main__":
    unittest.main()
