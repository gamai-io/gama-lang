"""LSP behaviour for record/list match patterns and arm guards.

The core parser only accepts enum/Result patterns; record struct patterns,
record constructor patterns, list patterns and ``if`` guards are LSP-only
syntax.  These tests pin the best-effort, parse-free support:

* Completion at an arm pattern head offers records/enums (never ordinary
  expression builtins) and record struct braces offer the record's fields.
* List pattern brackets are ordinary expression positions; they are never
  mistaken for a record field slot.
* Hover on a pattern record name reports fields/arity; hover on a bound
  pattern variable reports the field/list element type when it can be
  resolved from the declaration and the match subject.
* A guard after ``if`` falls back to normal expression completion and never
  crashes.
* Unclosed or malformed matches keep returning JSON-compatible responses.
"""
import json
import pathlib
import sys
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient

URI = "file:///tmp/lsp_patterns.gm"

POINT = (
    "record Point {\n"
    "  x: Int\n"
    "  y: Int\n"
    "  fn sum(self: Point) -> Int { return self.x + self.y }\n"
    "}\n"
)
CHILD = (
    "record Child extends Point {\n"
    "  z: Int\n"
    "}\n"
)
COLOR = "enum Color { Red, Green }\n"
BASE = POINT + CHILD + COLOR + "let p: Point = Point(1, 2)\n"
# BASE has 10 lines (0..9); tests append match statements below.
MATCH_LINE = 10


class LspPatternTests(unittest.TestCase):
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

    def assert_quiet(self, text, line, character):
        result = self.completion(line, character)
        self.assertEqual(result["items"], [])

    def doc(self, *lines):
        return BASE + "\n".join(lines) + "\n"

    # ------------------------------------------------------- completion
    def test_field_completion_after_record_brace(self):
        text = "match p {\n  Point { \n}"
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 1, len("  Point { "))
        self.assertEqual(set(self.labels(res)), {"x", "y"})
        self.assertNotIn("sum", self.labels(res))

    def test_field_completion_after_comma_and_inherited(self):
        text = "match p {\n  Child { \n}"
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 1, len("  Child { "))
        labels = self.labels(res)
        self.assertTrue({"x", "y", "z"} <= set(labels))
        self.assertNotIn("sum", labels)

    def test_field_completion_after_comma(self):
        text = "match p {\n  Point { x, \n}"
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 1, len("  Point { x, "))
        self.assertEqual(set(self.labels(res)), {"x", "y"})

    def test_pattern_head_lists_records_not_expression_builtins(self):
        text = "match p {\n  Po\n}"
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 1, len("  Po"))
        labels = self.labels(res)
        self.assertIn("Point", labels)
        self.assertIn("Child", labels)
        self.assertIn("Color", labels)
        self.assertIn("Red", labels)
        record = self.item(res, "Point")
        self.assertEqual(record["kind"], 7)
        self.assertIn("record Point", record["detail"])
        self.assertNotIn("print", labels)

    def test_pattern_head_in_later_arm(self):
        text = (
            "match p {\n"
            "  Point(x, y) => { print x }\n"
            "  Ch\n"
            "}"
        )
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 2, len("  Ch"))
        labels = self.labels(res)
        self.assertIn("Child", labels)
        self.assertNotIn("print", labels)

    def test_list_pattern_brackets_keep_expression_completion(self):
        text = "match xs {\n  [a, \n}"
        self.open("let xs: List<Int> = [1, 2]\n" + self.doc(text))
        res = self.completion(1 + MATCH_LINE + 1, len("  [a, "))
        labels = self.labels(res)
        self.assertNotIn("x", labels)
        self.assertNotIn("y", labels)
        self.assertIn("print", labels)

    def test_guard_after_if_uses_normal_completion(self):
        text = "match p {\n  Point { x, y } if \n}"
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 1, len("  Point { x, y } if "))
        labels = self.labels(res)
        self.assertIn("print", labels)
        self.assertIn("p", labels)
        self.assertNotEqual(set(labels), {"x", "y"})

    # ------------------------------------------------------------ hover
    def test_hover_record_pattern_name_shows_fields(self):
        text = "match p {\n  Point { x, y } => { print x }\n}"
        self.open(self.doc(text))
        value = self.hover(MATCH_LINE + 1, len("  Point"))
        self.assertIn("record `Point`", value)
        self.assertIn("fields", value)
        self.assertIn("x: Int", value)
        self.assertIn("y: Int", value)

    def test_hover_bound_field_type_struct_pattern(self):
        text = "match p {\n  Point { x: a, y: b } => { print a }\n}"
        self.open(self.doc(text))
        value = self.hover(MATCH_LINE + 1, len("  Point { x: a"))
        self.assertIn("pattern binding `a`", value)
        self.assertIn("`Int`", value)
        self.assertIn("field `x`", value)

    def test_hover_bound_field_type_constructor_pattern(self):
        text = "match p {\n  Point(a, b) => { print a }\n}"
        self.open(self.doc(text))
        a = self.hover(MATCH_LINE + 1, len("  Point(a"))
        b = self.hover(MATCH_LINE + 1, len("  Point(a, b"))
        self.assertIn("`Int`", a)
        self.assertIn("field `x`", a)
        self.assertIn("`Int`", b)
        self.assertIn("field `y`", b)

    def test_hover_list_binding_infers_element_type(self):
        text = (
            "let xs: List<Int> = [1, 2]\n"
            "match xs {\n"
            "  [a, b] => { print a }\n"
            "}"
        )
        self.open(text)
        value = self.hover(2, len("  [a"))
        self.assertIn("pattern binding `a`", value)
        self.assertIn("`Int`", value)
        self.assertIn("list element", value)

    # ------------------------------------------------- malformed/robustness
    def test_unclosed_record_pattern_completes_fields(self):
        text = "match p {\n  Point {"
        self.open(self.doc(text))
        res = self.completion(MATCH_LINE + 1, len("  Point {"))
        self.assertEqual(set(self.labels(res)), {"x", "y"})
        value = self.hover(MATCH_LINE + 1, len("  Point"))
        self.assertIn("record `Point`", value)

    def test_malformed_matches_return_json_without_crashing(self):
        cases = [
            ("match\n", 1, 0),
            ("match p {\n  [a,\n", 1, 0),
            ("match p {\n  Point {\n", 1, 0),
            ("match p {\n  Some(v) if \n", 1, 0),
        ]
        self.open(self.doc(cases[0][0]))
        for index, (text, line, character) in enumerate(cases):
            if index:
                self.change(self.doc(text), version=index + 2)
            result = self.completion(MATCH_LINE + line, character)
            json.dumps(result)
            self.assertIsInstance(result["items"], list)
            hover = self.at("textDocument/hover", MATCH_LINE + line,
                            character)
            json.dumps(hover)
            sig = self.at("textDocument/signatureHelp", MATCH_LINE + line,
                          character)
            json.dumps(sig)

    def test_guard_signature_help_does_not_crash(self):
        text = "match p {\n  Point { x } if pred(\n}"
        self.open(self.doc(text))
        result = self.at("textDocument/signatureHelp", MATCH_LINE + 1,
                         len("  Point { x } if pred("))
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()
