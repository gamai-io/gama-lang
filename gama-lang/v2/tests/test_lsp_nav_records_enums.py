"""LSP definition/references/rename for GAMA-L records and enums.

Covers record type names, record field accesses, enum type names, enum
variants, precise rename edits, prepareRename rejection on unsupported or
ambiguous positions, cross-file navigation for aliased imported records and
enums, bare variant match/constructor renames, missing modules, and malformed
documents staying quiet instead of crashing.
"""
import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from test_lsp_features import LspClient  # reuse the framed JSON-RPC client

URI = "file:///tmp/lsp_nav_records_enums.gm"

DOC = (
    "record Point { x: Int, y: Int }\n"       # 0
    "let p: Point = Point(1, 2)\n"             # 1
    "print p.x\n"                              # 2
    "enum Color { Red, Green }\n"              # 3
    "let c: Color = Color.Red\n"               # 4
    "print Color.Green\n"                      # 5
)


class LspRecordEnumNavigationTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        resp, _ = self.c.wait_for(mid=mid)
        self.caps = resp["result"]["capabilities"]
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

    def request_result(self, method, line, character, **extra):
        params = {"textDocument": {"uri": self.uri},
                  "position": {"line": line, "character": character}}
        params.update(extra)
        mid = self.c.request(method, params)
        return self.c.wait_for(mid=mid)[0].get("result")

    def definition(self, line, character):
        return self.request_result(
            "textDocument/definition", line, character)

    def references(self, line, character):
        return self.request_result(
            "textDocument/references", line, character)

    def prepare(self, line, character):
        return self.request_result(
            "textDocument/prepareRename", line, character)

    def rename(self, line, character, new_name):
        return self.request_result(
            "textDocument/rename", line, character, newName=new_name)

    def spans(self, locations):
        return sorted((loc["range"]["start"]["line"],
                       loc["range"]["start"]["character"],
                       loc["range"]["end"]["character"])
                      for loc in locations)

    def spans_uri(self, locations):
        return sorted((loc["uri"],
                       loc["range"]["start"]["line"],
                       loc["range"]["start"]["character"],
                       loc["range"]["end"]["character"])
                      for loc in locations)

    def test_capabilities_prepare_rename(self):
        provider = self.caps.get("renameProvider")
        self.assertTrue(provider)
        self.assertEqual(provider.get("prepareProvider"), True)

    def test_definition_record_enum_and_members(self):
        self.open(DOC)

        # record type annotation and constructor
        for character in (DOC.split("\n")[1].index("Point"),
                          DOC.split("\n")[1].index("Point", 9)):
            loc = self.definition(1, character)
            self.assertEqual(loc["uri"], URI)
            self.assertEqual(loc["range"]["start"], {"line": 0, "character": 7})
            self.assertEqual(loc["range"]["end"], {"line": 0, "character": 12})

        # record field declaration and access
        decl = self.definition(0, DOC.split("\n")[0].index("x"))
        self.assertEqual(decl["range"]["start"], {"line": 0, "character": 15})
        access = self.definition(2, DOC.split("\n")[2].index("x"))
        self.assertEqual(access["range"]["start"], {"line": 0, "character": 15})

        # enum type annotation and qualifier
        enum = self.definition(4, DOC.split("\n")[4].index("Color"))
        self.assertEqual(enum["range"]["start"], {"line": 3, "character": 5})
        qualifier = self.definition(5, DOC.split("\n")[5].index("Color"))
        self.assertEqual(qualifier["range"]["start"],
                         {"line": 3, "character": 5})

        # enum variant (declaration and qualified use)
        variant = self.definition(3, DOC.split("\n")[3].index("Red"))
        self.assertEqual(variant["range"]["start"],
                         {"line": 3, "character": 13})
        use = self.definition(4, DOC.split("\n")[4].index("Red"))
        self.assertEqual(use["range"]["start"], {"line": 3, "character": 13})

    def test_references_counts_are_precise(self):
        self.open(DOC)

        record = self.references(1, DOC.split("\n")[1].index("Point"))
        self.assertEqual(self.spans(record), [
            (0, 7, 12), (1, 7, 12), (1, 15, 20)])

        field = self.references(2, DOC.split("\n")[2].index("x"))
        self.assertEqual(self.spans(field), [(0, 15, 16), (2, 8, 9)])

        enum = self.references(4, DOC.split("\n")[4].index("Color"))
        self.assertEqual(self.spans(enum), [
            (3, 5, 10), (4, 7, 12), (4, 15, 20), (5, 6, 11)])

        variant = self.references(4, DOC.split("\n")[4].index("Red"))
        self.assertEqual(self.spans(variant), [(3, 13, 16), (4, 21, 24)])

    def test_rename_record_field_and_variant_edits(self):
        self.open(DOC)

        record = self.rename(1, DOC.split("\n")[1].index("Point"), "Point2")
        edits = record["changes"][URI]
        self.assertEqual(
            sorted((e["range"]["start"]["line"],
                    e["range"]["start"]["character"],
                    e["range"]["end"]["character"],
                    e["newText"]) for e in edits),
            [(0, 7, 12, "Point2"), (1, 7, 12, "Point2"),
             (1, 15, 20, "Point2")])

        field = self.rename(2, DOC.split("\n")[2].index("x"), "z")
        self.assertEqual(
            sorted((e["range"]["start"]["line"],
                    e["range"]["start"]["character"],
                    e["range"]["end"]["character"]) for e in field["changes"][URI]),
            [(0, 15, 16), (2, 8, 9)])
        self.assertTrue(all(e["newText"] == "z"
                            for e in field["changes"][URI]))

        variant = self.rename(4, DOC.split("\n")[4].index("Red"), "Crimson")
        self.assertEqual(
            sorted((e["range"]["start"]["line"],
                    e["range"]["start"]["character"],
                    e["range"]["end"]["character"]) for e in variant["changes"][URI]),
            [(3, 13, 16), (4, 21, 24)])
        self.assertTrue(all(e["newText"] == "Crimson"
                            for e in variant["changes"][URI]))

    def test_prepare_rename_success_and_unsupported_positions(self):
        self.open(DOC)
        prepared = self.prepare(2, DOC.split("\n")[2].index("x"))
        self.assertEqual(prepared["placeholder"], "x")
        self.assertEqual(prepared["range"]["start"],
                         {"line": 2, "character": 8})

        # builtin type names are not record/enum symbols
        self.assertIsNone(self.prepare(0, DOC.split("\n")[0].index("Int")))
        # unknown member expression is intentionally not renamed
        self.assertIsNone(self.prepare(2, 6))

    def test_rename_rejects_collisions_and_ambiguous_fields(self):
        self.open(DOC)
        self.assertIsNone(self.rename(1, DOC.split("\n")[1].index("Point"),
                                      "Color"))
        self.assertIsNone(self.rename(2, DOC.split("\n")[2].index("x"), "y"))
        self.assertIsNone(self.rename(4, DOC.split("\n")[4].index("Red"),
                                      "Green"))

        ambiguous = ("record A { x: Int }\n"
                     "record B { x: Int }\n"
                     "print q.x\n")
        self.open(ambiguous)
        # `q` has no known record type, so the field owner is ambiguous.
        self.assertIsNone(self.prepare(2, 8))
        self.assertIsNone(self.rename(2, 8, "z"))

    def test_malformed_documents_do_not_crash(self):
        for bad in ("record Point {\nprint 1\n",
                    "enum Color {\nprint Color.\n"):
            self.open(bad)
            self.assertIsNone(self.definition(0, 8))
            self.assertEqual(self.references(0, 8), [])
            self.assertIsNone(self.prepare(0, 8))
            self.assertIsNone(self.rename(0, 8, "Renamed"))

    def test_namespaced_record_and_enum_cross_file_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            module = root / "geo.gm"
            module_text = (
                "record Point { x: Int, y: Int }\n"
                "enum Color { Red, Green }\n")
            module.write_text(module_text, encoding="utf-8")
            main = root / "main.gm"
            doc = ('import "geo.gm" as geo\n'
                   'let p: geo.Point = geo.Point(1, 2)\n'
                   'let c: geo.Color = geo.Color.Red\n')
            main.write_text(doc, encoding="utf-8")
            self.open(doc, uri=main.as_uri())
            lines = doc.split("\n")

            # imported record definition, references and rename
            point_positions = [
                idx for idx in range(len(lines[1]))
                if lines[1].startswith("Point", idx)]
            self.assertEqual(len(point_positions), 2)
            loc = self.definition(1, point_positions[0])
            self.assertEqual(loc["uri"], module.as_uri())
            self.assertEqual(loc["range"]["start"],
                             {"line": 0, "character": 7})
            refs = self.references(1, point_positions[0])
            self.assertEqual(self.spans_uri(refs), sorted([
                (main.as_uri(), 1, point_positions[0], point_positions[0] + 5),
                (main.as_uri(), 1, point_positions[1], point_positions[1] + 5),
                (module.as_uri(), 0, 7, 12),
            ]))
            self.assertEqual(self.prepare(1, point_positions[0])["placeholder"],
                             "Point")
            renamed = self.rename(1, point_positions[0], "Point2")
            self.assertEqual(sorted(renamed["changes"]),
                             sorted([main.as_uri(), module.as_uri()]))
            self.assertEqual(
                self.spans_uri([{"uri": uri, "range": edit["range"]}
                                for uri, edits in renamed["changes"].items()
                                for edit in edits]),
                sorted([(main.as_uri(), 1, point_positions[0], point_positions[0] + 5),
                        (main.as_uri(), 1, point_positions[1], point_positions[1] + 5),
                        (module.as_uri(), 0, 7, 12)]))

            # imported enum definition, references and rename
            color_positions = [
                idx for idx in range(len(lines[2]))
                if lines[2].startswith("Color", idx)]
            self.assertEqual(len(color_positions), 2)
            loc = self.definition(2, color_positions[0])
            self.assertEqual(loc["uri"], module.as_uri())
            self.assertEqual(loc["range"]["start"],
                             {"line": 1, "character": 5})
            refs = self.references(2, color_positions[0])
            self.assertEqual(self.spans_uri(refs), sorted([
                (main.as_uri(), 2, color_positions[0], color_positions[0] + 5),
                (main.as_uri(), 2, color_positions[1], color_positions[1] + 5),
                (module.as_uri(), 1, 5, 10),
            ]))
            renamed = self.rename(2, color_positions[0], "Palette")
            self.assertEqual(sorted(renamed["changes"]),
                             sorted([main.as_uri(), module.as_uri()]))
            self.assertTrue(all(edit["newText"] == "Palette"
                                for edits in renamed["changes"].values()
                                for edit in edits))

            # A collision with another declaration in the module is refused.
            self.assertIsNone(self.rename(1, point_positions[0], "Color"))

            # Starting from the declaration also reaches importing siblings.
            self.open(module_text, uri=module.as_uri())
            refs = self.references(0, module_text.split("\n")[0].index("Point"))
            self.assertEqual(self.spans_uri(refs), sorted([
                (main.as_uri(), 1, point_positions[0], point_positions[0] + 5),
                (main.as_uri(), 1, point_positions[1], point_positions[1] + 5),
                (module.as_uri(), 0, 7, 12),
            ]))

    def test_bare_enum_variant_uses(self):
        doc = (
            "enum Color { Red(Int), Green }\n"          # 0
            "let c: Color = Color.Green\n"              # 1
            "match c { Red(v) { print v } Green { print 2 } }\n"  # 2
            "let d: Color = Red(3)\n"                   # 3
        )
        self.open(doc)
        lines = doc.split("\n")
        arm = lines[2].index("Red")
        ctor = lines[3].index("Red")

        prepared = self.prepare(2, arm)
        self.assertEqual(prepared["placeholder"], "Red")
        self.assertEqual(prepared["range"]["start"],
                         {"line": 2, "character": arm})
        self.assertEqual(self.definition(2, arm)["range"]["start"],
                         {"line": 0, "character": 13})
        self.assertEqual(self.definition(3, ctor)["range"]["start"],
                         {"line": 0, "character": 13})

        refs = self.references(0, lines[0].index("Red"))
        self.assertEqual(self.spans(refs), [
            (0, 13, 16), (2, arm, arm + 3), (3, ctor, ctor + 3)])

        renamed = self.rename(3, ctor, "Crimson")
        self.assertEqual(self.spans(renamed["changes"][URI]), [
            (0, 13, 16), (2, arm, arm + 3), (3, ctor, ctor + 3)])
        self.assertTrue(all(edit["newText"] == "Crimson"
                            for edit in renamed["changes"][URI]))

        # Renaming the declaration also updates the bare uses.
        renamed = self.rename(0, lines[0].index("Red"), "Crimson")
        self.assertEqual(self.spans(renamed["changes"][URI]), [
            (0, 13, 16), (2, arm, arm + 3), (3, ctor, ctor + 3)])

    def test_ambiguous_bare_variant_is_refused(self):
        doc = (
            "enum A { Red(Int) }\n"
            "enum B { Red(Text) }\n"
            "let c = 1\n"
            "match c { Red(v) { print v } }\n"
        )
        self.open(doc)
        arm = doc.split("\n")[3].index("Red")
        self.assertIsNone(self.prepare(3, arm))
        self.assertIsNone(self.rename(3, arm, "Crimson"))
        self.assertIsNone(self.definition(3, arm))

    def test_missing_module_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = pathlib.Path(tmp) / "main.gm"
            doc = ('import "missing.gm" as geo\n'
                   'print geo.Point\n'
                   'print geo.Color\n')
            self.open(doc, uri=main.as_uri())
            for line in (1, 2):
                idx = doc.split("\n")[line].index("geo.") + 4
                self.assertIsNone(self.definition(line, idx))
                self.assertEqual(self.references(line, idx), [])
                self.assertIsNone(self.prepare(line, idx))
                self.assertIsNone(self.rename(line, idx, "Renamed"))


if __name__ == "__main__":
    unittest.main()
