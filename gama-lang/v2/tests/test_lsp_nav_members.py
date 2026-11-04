"""LSP navigation for fields and variants of imported records and enums.

Covers alias-qualified record fields (``p.x`` where ``p: geo.Point``) and
alias-qualified enum variants (``geo.Color.Red``) across the declaring module
and importing documents, plus missing modules, unresolved receivers, unknown
members, collision refusal and unchanged local-only behaviour.
"""
import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient


class LspImportedMemberNavigationTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        resp, _ = self.c.wait_for(mid=mid)
        self.caps = resp["result"]["capabilities"]
        self.c.notify("initialized", {})
        self.uri = None

    def tearDown(self):
        self.c.close()

    def open(self, text, uri):
        self.uri = uri
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        self.c.wait_for(method="textDocument/publishDiagnostics")

    def at(self, method, line, character, **extra):
        params = {"textDocument": {"uri": self.uri},
                  "position": {"line": line, "character": character}}
        params.update(extra)
        mid = self.c.request(method, params)
        return self.c.wait_for(mid=mid)[0].get("result")

    def definition(self, line, character):
        return self.at("textDocument/definition", line, character)

    def references(self, line, character):
        return self.at("textDocument/references", line, character)

    def prepare(self, line, character):
        return self.at("textDocument/prepareRename", line, character)

    def rename(self, line, character, new_name):
        return self.at("textDocument/rename", line, character,
                       newName=new_name)

    def spans_uri(self, locations):
        return sorted((loc["uri"],
                       loc["range"]["start"]["line"],
                       loc["range"]["start"]["character"],
                       loc["range"]["end"]["character"])
                      for loc in locations)

    def edit_spans(self, result):
        return sorted((uri,
                       edit["range"]["start"]["line"],
                       edit["range"]["start"]["character"],
                       edit["range"]["end"]["character"],
                       edit["newText"])
                      for uri, edits in result["changes"].items()
                      for edit in edits)

    def test_imported_record_field_navigation_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            module = root / "geo.gm"
            module_text = "record Point { x: Int, y: Int }\n"
            module.write_text(module_text, encoding="utf-8")
            main = root / "main.gm"
            doc = ('import "geo.gm" as geo\n'
                   'let p: geo.Point = geo.Point(1, 2)\n'
                   'print p.x\n'
                   'let q: geo.Point = geo.Point(3, 4)\n'
                   'print q.x\n')
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            x = doc.split("\n")[2].index("x")

            decl = self.definition(2, x)
            self.assertEqual(decl["uri"], module.as_uri())
            self.assertEqual(decl["range"]["start"],
                             {"line": 0, "character": 15})

            expected = [
                (module.as_uri(), 0, 15, 16),
                (main.as_uri(), 2, 8, 9),
                (main.as_uri(), 4, 8, 9),
            ]
            self.assertEqual(
                self.spans_uri(self.references(2, x)), sorted(expected))
            prepared = self.prepare(2, x)
            self.assertEqual(prepared["placeholder"], "x")
            self.assertEqual(prepared["range"]["start"],
                             {"line": 2, "character": 8})

            renamed = self.rename(2, x, "z")
            self.assertEqual(self.edit_spans(renamed), sorted(
                (uri, line, start, end, "z")
                for uri, line, start, end in expected))

            # Same edits when starting from the declaration in the module.
            self.open(module_text, module.as_uri())
            xdecl = module_text.split("\n")[0].index("x")
            self.assertEqual(
                self.spans_uri(self.references(0, xdecl)),
                sorted(expected))
            renamed = self.rename(0, xdecl, "z")
            self.assertEqual(self.edit_spans(renamed), sorted(
                (uri, line, start, end, "z")
                for uri, line, start, end in expected))

            # Renaming onto another declared field must be refused.
            self.open(doc, main.as_uri())
            self.assertIsNone(self.rename(2, x, "y"))

    def test_imported_enum_variant_navigation_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            module = root / "geo.gm"
            module_text = "enum Color { Red(Int), Green }\n"
            module.write_text(module_text, encoding="utf-8")
            main = root / "main.gm"
            doc = ('import "geo.gm" as geo\n'
                   'let c: geo.Color = geo.Color.Red(1)\n'
                   'match c { geo.Color.Red(v) { print v } '
                   'geo.Color.Green { print 2 } }\n')
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            lines = doc.split("\n")
            value = lines[1].rindex("Red")
            pattern = lines[2].index("Red")

            decl = self.definition(1, value)
            self.assertEqual(decl["uri"], module.as_uri())
            self.assertEqual(decl["range"]["start"],
                             {"line": 0, "character": 13})

            expected = [
                (module.as_uri(), 0, 13, 16),
                (main.as_uri(), 1, value, value + 3),
                (main.as_uri(), 2, pattern, pattern + 3),
            ]
            self.assertEqual(
                self.spans_uri(self.references(1, value)), sorted(expected))
            prepared = self.prepare(1, value)
            self.assertEqual(prepared["placeholder"], "Red")
            renamed = self.rename(1, value, "Crimson")
            self.assertEqual(self.edit_spans(renamed), sorted(
                (uri, line, start, end, "Crimson")
                for uri, line, start, end in expected))

            # Same edits starting from the enum declaration in the module.
            self.open(module_text, module.as_uri())
            red = module_text.split("\n")[0].index("Red")
            self.assertEqual(
                self.spans_uri(self.references(0, red)), sorted(expected))
            renamed = self.rename(0, red, "Crimson")
            self.assertEqual(self.edit_spans(renamed), sorted(
                (uri, line, start, end, "Crimson")
                for uri, line, start, end in expected))

            self.open(doc, main.as_uri())
            self.assertIsNone(self.rename(1, value, "Green"))

    def test_missing_module_and_unresolved_receivers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            main = root / "main.gm"
            doc = ('import "missing.gm" as geo\n'
                   'let p: geo.Point = geo.Point(1, 2)\n'
                   'print p.x\n'
                   'let c: geo.Color = geo.Color.Red\n')
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            lines = doc.split("\n")
            for line, idx in ((2, lines[2].index("x")),
                              (3, lines[3].rindex("Red"))):
                self.assertIsNone(self.definition(line, idx))
                self.assertEqual(self.references(line, idx), [])
                self.assertIsNone(self.prepare(line, idx))
                self.assertIsNone(self.rename(line, idx, "Renamed"))

    def test_unknown_members_and_receivers_are_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            module = root / "geo.gm"
            module.write_text(
                "record Point { x: Int, y: Int }\n"
                "enum Color { Red, Green }\n", encoding="utf-8")
            doc = ('import "geo.gm" as geo\n'
                   'let p: geo.Point = geo.Point(1, 2)\n'
                   'print p.z\n'
                   'let q = 5\n'
                   'print q.x\n'
                   'print geo.Color.Blue\n')
            main = root / "main.gm"
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            lines = doc.split("\n")
            for text in ("z", "x", "Blue"):
                pass
            positions = [(2, lines[2].index("z")),
                         (4, lines[4].index("x")),
                         (5, lines[5].rindex("Blue"))]
            for line, idx in positions:
                self.assertIsNone(self.definition(line, idx))
                self.assertEqual(self.references(line, idx), [])
                self.assertIsNone(self.prepare(line, idx))
                self.assertIsNone(self.rename(line, idx, "Renamed"))

    def test_local_member_navigation_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            main = root / "local.gm"
            doc = ("record Point { x: Int, y: Int }\n"
                   "let p: Point = Point(1, 2)\n"
                   "print p.x\n"
                   "enum Color { Red, Green }\n"
                   "let c: Color = Color.Red\n")
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            lines = doc.split("\n")
            x = lines[2].index("x")
            self.assertEqual(
                self.spans_uri(self.references(2, x)),
                [(main.as_uri(), 0, 15, 16),
                 (main.as_uri(), 2, 8, 9)])
            renamed = self.rename(2, x, "z")
            self.assertEqual(
                sorted(renamed["changes"]), [main.as_uri()])
            self.assertEqual(
                self.spans_uri([{"uri": uri, "range": edit["range"]}
                                for uri, edits in renamed["changes"].items()
                                for edit in edits]),
                [(main.as_uri(), 0, 15, 16),
                 (main.as_uri(), 2, 8, 9)])

            red = lines[4].rindex("Red")
            self.assertEqual(
                self.spans_uri(self.references(4, red)),
                [(main.as_uri(), 3, 13, 16),
                 (main.as_uri(), 4, 21, 24)])
            renamed = self.rename(3, lines[3].index("Red"), "Crimson")
            self.assertEqual(sorted(renamed["changes"]), [main.as_uri()])
            self.assertEqual(
                self.spans_uri([{"uri": uri, "range": edit["range"]}
                                for uri, edits in renamed["changes"].items()
                                for edit in edits]),
                [(main.as_uri(), 3, 13, 16),
                 (main.as_uri(), 4, 21, 24)])


    def test_nested_imported_field_chain_and_module_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            geo = root / "geo.gm"
            geo.write_text("record Point { x: Int }\n"
                           "record Segment { a: Point, b: Point }\n",
                           encoding="utf-8")
            other = root / "other.gm"
            other.write_text("record Point { x: Int }\n", encoding="utf-8")
            main = root / "main.gm"
            doc = ('import "geo.gm" as geo\n'
                   'import "other.gm" as oth\n'
                   'let s: geo.Segment = '
                   'geo.Segment(geo.Point(1), geo.Point(2))\n'
                   'print s.a.x\n'
                   'let o: oth.Point = oth.Point(9)\n'
                   'print o.x\n')
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            lines = doc.split("\n")
            nested = lines[3].rindex("x")
            foreign = lines[5].rindex("x")

            # Nested chain ``s.a.x`` resolves to geo.Point.x (declared bare
            # inside the imported module).
            decl = self.definition(3, nested)
            self.assertEqual(decl["uri"], geo.as_uri())
            self.assertEqual(decl["range"]["start"],
                             {"line": 0, "character": 15})
            expected = [
                (geo.as_uri(), 0, 15, 16),
                (main.as_uri(), 3, nested, nested + 1),
            ]
            self.assertEqual(
                self.spans_uri(self.references(3, nested)), sorted(expected))
            renamed = self.rename(3, nested, "w")
            self.assertEqual(self.edit_spans(renamed), sorted(
                (uri, line, start, end, "w")
                for uri, line, start, end in expected))

            # The same-named field of a different imported module is isolated.
            foreign_decl = self.definition(5, foreign)
            self.assertEqual(foreign_decl["uri"], other.as_uri())
            foreign_expected = [
                (other.as_uri(), 0, 15, 16),
                (main.as_uri(), 5, foreign, foreign + 1),
            ]
            self.assertEqual(
                self.spans_uri(self.references(5, foreign)),
                sorted(foreign_expected))

    def test_field_navigation_when_document_does_not_fully_parse(self):
        # ``alias.Enum.Variant`` match patterns are accepted by the LSP but
        # rejected by the core parser; field navigation must still work.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            geo = root / "geo.gm"
            geo.write_text("record Point { x: Int, y: Int }\n"
                           "enum Color { Red, Green }\n", encoding="utf-8")
            main = root / "main.gm"
            doc = ('import "geo.gm" as geo\n'
                   'let p: geo.Point = geo.Point(1, 2)\n'
                   'print p.x\n'
                   'let c: geo.Color = geo.Color.Red\n'
                   'match c { geo.Color.Red { print 1 } '
                   'geo.Color.Green { print 2 } }\n')
            main.write_text(doc, encoding="utf-8")
            self.open(doc, main.as_uri())
            x = doc.split("\n")[2].index("x")

            decl = self.definition(2, x)
            self.assertEqual(decl["uri"], geo.as_uri())
            self.assertEqual(decl["range"]["start"],
                             {"line": 0, "character": 15})
            expected = [
                (geo.as_uri(), 0, 15, 16),
                (main.as_uri(), 2, x, x + 1),
            ]
            self.assertEqual(
                self.spans_uri(self.references(2, x)), sorted(expected))
            renamed = self.rename(2, x, "z")
            self.assertEqual(self.edit_spans(renamed), sorted(
                (uri, line, start, end, "z")
                for uri, line, start, end in expected))


if __name__ == "__main__":
    unittest.main()
