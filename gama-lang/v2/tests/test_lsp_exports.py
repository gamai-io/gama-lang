"""LSP support for exported and renamed module members.

Core GAMA-L understands ``export fn/let/record`` but rejects ``export enum``
and ``export ... as`` re-exports. The LSP therefore parses those declarations
structurally and exposes them through an alias:

* ``alias.`` completion lists exported enums, re-exported names and constants;
  private declarations stay hidden when the module uses explicit exports.
* ``alias.Enum.`` completion lists the enum's variants.
* Hover on a re-export notes the original declaration name.
* Definition on a re-export jumps to the original name in the imported file.
* Malformed/unsupported export syntax never crashes and stays quiet.
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


LIB = (
    'export enum Color { Red, Green }\n'
    'export fn add(a: Int, b: Int) -> Int effects {} '
    '{ return a + b } as plus\n'
    'export let pi: Decimal = 3.14 as PI\n'
    'export record Point { x: Int, y: Int } as P\n'
    'export fn mul(a: Int, b: Int) -> Int as times '
    'effects {} { return a * b }\n'
    'fn secret() -> Int effects {} { return 1 }\n'
    'let hidden = 2\n'
    'enum Size { Small, Large }\n'
)

MAIN = 'import "lib.gm" as m\nprint m.\n'


class LspExportTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "lib.gm").write_text(LIB, encoding="utf-8")
        self.uri = (self.root / "main.gm").as_uri()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        self.caps = self.c.wait_for(mid=mid)[0]["result"]["capabilities"]
        self.c.notify("initialized", {})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def open(self, text, uri=None):
        self.uri = uri or self.uri
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        return self.c.wait_for(method="textDocument/publishDiagnostics")[0]

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

    def definition(self, line, character):
        return self.at("textDocument/definition", line, character)

    def item(self, result, label):
        for entry in result["items"]:
            if entry["label"] == label:
                return entry
        self.fail("missing completion item %r" % label)

    def labels(self, result):
        return {entry["label"] for entry in result["items"]}

    # ------------------------------------------------------------ completion
    def test_alias_completion_exported_enums_and_renames(self):
        self.open(MAIN)
        res = self.completion(1, len("print m."))
        json.dumps(res)
        self.assertEqual(self.labels(res),
                         {"Color", "plus", "PI", "P", "times", "Size"})
        # Private declarations are hidden because explicit exports exist.
        for hidden in ("add", "mul", "pi", "Point", "secret", "hidden"):
            self.assertNotIn(hidden, self.labels(res))
        self.assertEqual(self.item(res, "Color")["kind"], 13)
        self.assertEqual(self.item(res, "Size")["kind"], 13)
        self.assertEqual(self.item(res, "plus")["kind"], 3)
        self.assertEqual(self.item(res, "times")["kind"], 3)
        self.assertEqual(self.item(res, "PI")["kind"], 21)
        self.assertEqual(self.item(res, "P")["kind"], 7)
        self.assertIn("plus(a: Int, b: Int) -> Int",
                      self.item(res, "plus")["detail"])
        self.assertIn("times(a: Int, b: Int) -> Int",
                      self.item(res, "times")["detail"])
        self.assertEqual(self.item(res, "PI")["detail"], "let PI: Decimal")
        self.assertEqual(self.item(res, "P")["detail"], "record P")
        self.assertIn("enum Color { Red, Green }",
                      self.item(res, "Color")["detail"])
        self.assertIn("Exported from `add`.",
                      self.item(res, "plus")["documentation"])
        self.assertIn("Exported from `pi`.",
                      self.item(res, "PI")["documentation"])

    def test_enum_variant_completion_for_exported_and_private_enums(self):
        self.open('import "lib.gm" as m\nprint m.Color.\n')
        exported = self.completion(1, len("print m.Color."))
        self.assertEqual(self.labels(exported), {"Red", "Green"})
        for entry in exported["items"]:
            self.assertEqual(entry["kind"], 20)
            self.assertIn("Color.%s" % entry["label"], entry["detail"])
        json.dumps(exported)

        # Enums stay module-global even with explicit exports (core rule).
        self.change('import "lib.gm" as m\nprint m.Size.\n')
        unexported = self.completion(1, len("print m.Size."))
        self.assertEqual(self.labels(unexported), {"Small", "Large"})

    # ---------------------------------------------------------------- hover
    def test_hover_renamed_exports_notes_original_name(self):
        self.open('import "lib.gm" as m\n'
                  'print m.plus\n'
                  'print m.PI\n'
                  'print m.P\n'
                  'print m.Color\n')
        fn = self.hover(1, len("print m.pl"))
        self.assertIn("m.plus", fn)
        self.assertIn("a: Int, b: Int", fn)
        self.assertIn("Exported from `add`.", fn)

        const = self.hover(2, len("print m.PI"))
        self.assertIn("type `Decimal`", const)
        self.assertIn("value `3.14`", const)
        self.assertIn("Exported from `pi`.", const)

        record = self.hover(3, len("print m.P"))
        self.assertIn("GAMA-L record `P`", record)
        self.assertIn("Exported from `Point`.", record)

        enum = self.hover(4, len("print m.Color"))
        self.assertIn("enum `Color`", enum)

    def test_hover_imported_enum_variant(self):
        self.open('import "lib.gm" as m\nprint m.Color.Red\n')
        value = self.hover(1, len("print m.Color.Red"))
        self.assertIn("variant `Red`", value)
        self.assertIn("`m.Color.Red`", value)
        self.assertIn("lib.gm", value)

    # ----------------------------------------------------------- definition
    def test_definition_renamed_exports_lands_on_original_name(self):
        self.open('import "lib.gm" as m\n'
                  'print m.plus\n'
                  'print m.times\n'
                  'print m.PI\n'
                  'print m.P\n'
                  'print m.Color\n')
        lines = LIB.split("\n")

        plus = self.definition(1, len("print m.pl"))
        self.assertEqual(plus["uri"], (self.root / "lib.gm").as_uri())
        self.assertEqual(plus["range"]["start"],
                         {"line": 1, "character": lines[1].index("add")})

        times = self.definition(2, len("print m.ti"))
        self.assertEqual(times["range"]["start"],
                         {"line": 4, "character": lines[4].index("mul")})

        const = self.definition(3, len("print m.PI"))
        self.assertEqual(const["range"]["start"],
                         {"line": 2, "character": lines[2].index("pi")})

        record = self.definition(4, len("print m.P"))
        self.assertEqual(record["range"]["start"],
                         {"line": 3, "character": lines[3].index("Point")})

        enum = self.definition(5, len("print m.Color"))
        self.assertEqual(enum["range"]["start"],
                         {"line": 0, "character": lines[0].index("Color")})

    # --------------------------------------------- malformed / unsupported
    def test_malformed_export_syntax_is_quiet_and_json_safe(self):
        cases = (
            'export enum Broken { Red, Green\n',
            'export fn add( as plus\n',
            'export enum\n',
            'export let as\n',
        )
        for text in cases:
            with self.subTest(text=text):
                (self.root / "lib.gm").write_text(text, encoding="utf-8")
                self.open('import "lib.gm" as m\nprint m.\n')
                res = self.completion(1, len("print m."))
                self.assertEqual(res["items"], [])
                json.dumps(res)
                self.assertIsNone(self.hover(1, len("print m.th")))
                self.assertIsNone(self.definition(1, len("print m.th")))

    def test_missing_module_and_side_effect_module_stay_quiet(self):
        (self.root / "side.gm").write_text(
            'print 1\n'
            'export enum Color { Red }\n',
            encoding="utf-8")
        self.open('import "side.gm" as s\nprint s.\n')
        res = self.completion(1, len("print s."))
        # A side-effect module is not a legal alias import; stay quiet rather
        # than advertising members the runtime would reject.
        self.assertEqual(res["items"], [])
        json.dumps(res)

    def test_smoke_all_requests_are_json_serializable(self):
        self.open('import "lib.gm" as m\n'
                  'print m.plus\n'
                  'print m.Color.\n')
        results = [
            self.completion(1, len("print m.pl")),
            self.completion(2, len("print m.Color.")),
            self.at("textDocument/hover", 1, len("print m.pl")),
            self.at("textDocument/definition", 1, len("print m.pl")),
            self.at("textDocument/documentSymbol", 0, 0),
        ]
        for result in results:
            json.dumps(result)


if __name__ == "__main__":
    unittest.main()
