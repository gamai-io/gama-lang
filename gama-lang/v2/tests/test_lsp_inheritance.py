"""LSP support for record inheritance / structural field access.

Semantics under test:

* ``d.`` for a child record includes inherited fields and methods plus the
  child's own members; hover names the declaring (parent) record.
* Record declaration hover and document symbols show ``extends Parent`` and
  list inherited fields clearly marked.
* Definition on an inherited field/method lands on the parent declaration,
  including when the parent is reached through a namespaced import.
* A value annotated as the parent while holding a child exposes only parent
  members (structural compatibility, no child guessing).
* Malformed, missing and cyclic inheritance stays quiet and never crashes.
"""
import json
import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client

URI = "file:///tmp/lsp_inheritance.gm"

PARENT = (
    "record Parent {\n"
    "  x: Int\n"
    "  y: Int\n"
    "  fn base(self: Parent) -> Int { return self.x }\n"
    "}\n"
)
CHILD = (
    "record Child extends Parent {\n"
    "  z: Int\n"
    "  fn own(self: Child) -> Int { return self.z }\n"
    "}\n"
)
LET_CHILD = "let d: Child = Child(1, 2, 3)\n"
PRINT_D = "print d.\n"
PRINT_D_X = "print d.x\n"
PRINT_D_BASE = "print d.base\n"
BASE_URI = "file:///tmp/lsp_inherit_base.gm"
CHILD_URI = "file:///tmp/lsp_inherit_child.gm"
MAIN_URI = "file:///tmp/lsp_inherit_main.gm"


class LspInheritanceTests(unittest.TestCase):
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

    def at(self, method, line, character, **extra):
        params = {"textDocument": {"uri": self.uri},
                  "position": {"line": line, "character": character}}
        params.update(extra)
        mid = self.c.request(method, params)
        return self.c.wait_for(mid=mid)[0].get("result")

    def completion(self, line, character):
        return self.at("textDocument/completion", line, character)

    def hover(self, line, character):
        result = self.at("textDocument/hover", line, character)
        return result["contents"]["value"] if result else None

    def definition(self, line, character):
        return self.at("textDocument/definition", line, character)

    def symbols(self):
        return self.at("textDocument/documentSymbol", 0, 0)

    def labels(self, result):
        return [item["label"] for item in result["items"]]

    def item(self, result, label):
        for entry in result["items"]:
            if entry["label"] == label:
                return entry
        self.fail("missing completion item %r" % label)

    # ------------------------------------------------------------ completion
    def test_child_completion_includes_inherited_and_own_members(self):
        self.open(PARENT + CHILD + LET_CHILD + PRINT_D)
        res = self.completion(10, len("print d."))
        self.assertEqual(set(self.labels(res)), {"x", "y", "base", "z", "own"})
        for inherited in ("x", "y", "base"):
            self.assertIn("inherited from Parent",
                          self.item(res, inherited)["detail"])
        self.assertEqual(self.item(res, "x")["kind"], 5)
        self.assertEqual(self.item(res, "base")["kind"], 2)
        # Own members are not marked as inherited.
        self.assertNotIn("inherited", self.item(res, "z")["detail"])
        self.assertNotIn("inherited", self.item(res, "own")["detail"])
        self.assertEqual(
            self.item(res, "own")["detail"],
            "method own(self: Child) -> Int")

    def test_parent_typed_value_uses_parent_members_only(self):
        doc = (PARENT + CHILD
               + "let p: Parent = Child(1, 2, 3)\n"
               + "print p.\n")
        self.open(doc)
        res = self.completion(10, len("print p."))
        self.assertEqual(set(self.labels(res)), {"x", "y", "base"})
        self.assertNotIn("z", self.labels(res))
        self.assertNotIn("own", self.labels(res))

    # ---------------------------------------------------------------- hover
    def test_hover_inherited_member_names_declaring_record(self):
        doc = PARENT + CHILD + LET_CHILD + PRINT_D_X
        self.open(doc)
        field = self.hover(10, len("print d.x"))
        self.assertIn("field `x: Int` of record `Parent`", field)
        self.assertIn("inherited by `Child`", field)

        self.change(PARENT + CHILD + LET_CHILD + PRINT_D_BASE, version=2)
        method = self.hover(10, len("print d.base"))
        self.assertIn("method `base` of record `Parent`", method)
        self.assertIn("Inherited by record `Child`", method)

    def test_hover_own_member_stays_on_child(self):
        doc = PARENT + CHILD + LET_CHILD + "print d.z\n"
        self.open(doc)
        value = self.hover(10, len("print d.z"))
        self.assertIn("field `z: Int` of record `Child`", value)
        self.assertNotIn("inherited", value.lower())

    def test_record_declaration_hover_shows_extends_and_inherited_fields(self):
        self.open(PARENT + CHILD + LET_CHILD)
        value = self.hover(5, len("record Chi"))
        self.assertIn("record Child extends Parent", value)
        self.assertIn("inherited fields", value)
        self.assertIn("`x: Int` (from `Parent`)", value)
        self.assertIn("`y: Int` (from `Parent`)", value)

    # -------------------------------------------------------------- symbols
    def test_document_symbols_mark_extends_and_inherited_fields(self):
        self.open(PARENT + CHILD + LET_CHILD)
        syms = {sym["name"]: sym for sym in self.symbols()}
        child = syms["Child"]
        self.assertEqual(child["detail"], "record Child extends Parent")
        by_name = {entry["name"]: entry for entry in child["children"]}
        self.assertEqual(by_name["own"]["kind"], 6)  # Method
        self.assertEqual(by_name["x"]["kind"], 8)    # Field
        self.assertIn("field x: Int (inherited from Parent)",
                      by_name["x"]["detail"])
        self.assertIn("field y: Int (inherited from Parent)",
                      by_name["y"]["detail"])
        # The parent keeps its own fields unmarked.
        parent = syms["Parent"]
        self.assertEqual(parent["detail"], "record Parent")
        self.assertNotIn("x", [entry["name"] for entry in parent["children"]])

    # ----------------------------------------------------------- definition
    def test_definition_on_inherited_field_and_method_goes_to_parent(self):
        doc = PARENT + CHILD + LET_CHILD + PRINT_D_X
        self.open(doc)
        field = self.definition(10, len("print d.x"))
        self.assertEqual(field["uri"], URI)
        self.assertEqual(field["range"]["start"],
                         {"line": 1, "character": 2})

        self.change(PARENT + CHILD + LET_CHILD + PRINT_D_BASE, version=2)
        method = self.definition(10, len("print d.base"))
        self.assertEqual(method["uri"], URI)
        self.assertEqual(method["range"]["start"],
                         {"line": 3, "character": 5})

    def test_cross_file_inherited_definition_reaches_grandparent_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            base = root / "base.gm"
            base.write_text(
                "record Base {\n"
                "  x: Int\n"
                "  fn hi(self: Base) -> Int { return self.x }\n"
                "}\n", encoding="utf-8")
            child = root / "child.gm"
            child.write_text(
                'import "base.gm" as b\n'
                "record Child extends b.Base {\n"
                "  z: Int\n"
                "}\n", encoding="utf-8")
            main = root / "main.gm"
            doc = ('import "child.gm" as c\n'
                   "let d: c.Child = c.Child(1, 2)\n"
                   "print d.x\n")
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())

            res = self.completion(2, len("print d."))
            self.assertEqual(set(self.labels(res)), {"x", "hi", "z"})
            field = self.definition(2, len("print d.x"))
            self.assertEqual(field["uri"], base.as_uri())
            self.assertEqual(field["range"]["start"],
                             {"line": 1, "character": 2})

            self.change(doc.replace("print d.x", "print d.hi"), version=2)
            method = self.definition(2, len("print d.hi"))
            self.assertEqual(method["uri"], base.as_uri())
            self.assertEqual(method["range"]["start"],
                             {"line": 2, "character": 5})

    # ---------------------------------------------------- malformed / cyclic
    def test_cyclic_inheritance_is_quiet_and_does_not_crash(self):
        doc = ("record A extends B { a: Int }\n"
               "record B extends A { b: Int }\n"
               "let d: A = A(1)\n"
               "print d.\n")
        self.open(doc)
        res = self.completion(3, len("print d."))
        self.assertEqual(set(self.labels(res)), {"a", "b"})
        value = self.hover(0, len("record A"))
        self.assertIn("record A extends B", value)
        self.assertIn("inherited fields", value)
        json.dumps(res)
        json.dumps(self.symbols())

    def test_missing_parent_and_malformed_header_stay_quiet(self):
        doc = ("record MissingChild extends DoesNotExist { c: Int }\n"
               "let d: MissingChild = MissingChild(1)\n"
               "print d.\n")
        self.open(doc)
        self.assertEqual(set(self.labels(self.completion(2, len("print d.")))),
                         {"c"})

        # A malformed header is simply absent; nothing should crash and no
        # members are advertised under an unknown receiver.
        self.change("record Broken extends {\nlet d = 1\nprint d.\n",
                    version=2)
        res = self.completion(2, len("print d."))
        self.assertEqual(res["items"], [])
        json.dumps(res)
        json.dumps(self.hover(0, len("record Bro")))

    def test_payloads_are_json_serializable(self):
        self.open(PARENT + CHILD + LET_CHILD + PRINT_D)
        json.dumps(self.completion(10, len("print d.")))
        json.dumps(self.hover(5, len("record Chi")))
        json.dumps(self.symbols())


if __name__ == "__main__":
    unittest.main()
