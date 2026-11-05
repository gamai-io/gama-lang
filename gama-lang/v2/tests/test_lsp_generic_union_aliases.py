"""LSP support for generic and union type aliases.

Core GAMA-L rejects both ``type Name<T> = ...`` and union aliases
(``type Name = A | B``). The structural LSP reader accepts them for
completion, hover, definition and outline only; nothing is parsed,
checked or evaluated by the core.

Covered here:

* generic and union aliases appear in annotation, alias-target and
  ``alias.`` completion with ``type Name<T> = Target`` details;
* hover shows type parameters, union members and a recursively expanded
  target (alias chains and generic applications substitute arguments);
* definition lands on the original alias declaration, including renamed
  imports;
* malformed generic/union documents stay quiet and JSON-serializable.
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
    'type Port = Int\n'                                     # 0
    'export type Maybe<T> = T | Nil as Opt\n'               # 1
    'export type Name = Text | Bool\n'                      # 2
    'export type Alias = Port\n'                            # 3
    'export fn id(x: Int) -> Int { return x }\n'            # 4
)

LOCAL = (
    'type Port = Int\n'                                     # 0
    'type Copy = Port\n'                                    # 1
    'type Box<T> = T\n'                                     # 2
    'type Maybe<T> = T | Nil\n'                             # 3
    'type Nested = Box<Port>\n'                             # 4
    'type Name = Text | Bool\n'                             # 5
    'let x: Maybe<Int> = 1\n'                               # 6
)


class LspGenericUnionAliasTests(unittest.TestCase):
    def setUp(self):
        self.c = LspClient()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.uri = (self.root / "main.gm").as_uri()
        mid = self.c.request("initialize", {
            "processId": None, "rootUri": None, "capabilities": {}})
        self.caps = self.c.wait_for(mid=mid)[0]["result"]["capabilities"]
        self.c.notify("initialized", {})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def open(self, text, uri=None):
        self.uri = uri or self.uri
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri, "languageId": "gama-l", "version": 1,
            "text": text}})
        return self.c.wait_for(method="textDocument/publishDiagnostics")[0]

    def change(self, text, version=2):
        self.c.notify("textDocument/didChange", {
            "textDocument": {"uri": self.uri, "version": version},
            "contentChanges": [{"text": text}]})
        self.c.wait_for(method="textDocument/publishDiagnostics")

    def at(self, method, line, character):
        mid = self.c.request(method, {
            "textDocument": {"uri": self.uri},
            "position": {"line": line, "character": character}})
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

    # ------------------------------------------------- completion
    def test_annotation_completion_generic_and_union_aliases(self):
        self.open('type Port = Int\n'
                  'type Maybe<T> = T | Nil\n'
                  'type Name = Text | Bool\n'
                  'let x: \n')
        res = self.completion(3, len("let x: "))
        json.dumps(res)
        self.assertIn("Maybe", self.labels(res))
        self.assertIn("Name", self.labels(res))
        self.assertEqual(self.item(res, "Maybe")["detail"],
                         "type Maybe<T> = T | Nil")
        self.assertIn("Type parameters: `T`.",
                      self.item(res, "Maybe")["documentation"])
        self.assertIn("Union of: `T`, `Nil`.",
                      self.item(res, "Maybe")["documentation"])
        self.assertEqual(self.item(res, "Name")["detail"],
                         "type Name = Text | Bool")
        self.assertIn("Union of: `Text`, `Bool`.",
                      self.item(res, "Name")["documentation"])

    def test_annotation_completion_after_generic_open(self):
        self.open('record Box<T> { value: T }\n'
                  'type Maybe<T> = T | Nil\n'
                  'let x: Maybe<\n')
        res = self.completion(2, len("let x: Maybe<"))
        json.dumps(res)
        self.assertIn("Int", self.labels(res))
        self.assertIn("Maybe", self.labels(res))

    def test_target_completion_generic_and_union(self):
        self.open('type Maybe<T> = T | Nil\n'
                  'type Name = Text | Bool\n'
                  'type Alias = \n')
        target = self.completion(2, len("type Alias = "))
        json.dumps(target)
        self.assertIn("Maybe", self.labels(target))
        self.assertEqual(self.item(target, "Maybe")["detail"],
                         "type Maybe<T> = T | Nil")

        self.change('type Maybe<T> = T | Nil\n'
                    'type Name = Text | Bool\n'
                    'type Alias = Text | \n')
        after_pipe = self.completion(2, len("type Alias = Text | "))
        json.dumps(after_pipe)
        self.assertIn("Maybe", self.labels(after_pipe))
        self.assertIn("Name", self.labels(after_pipe))

    def test_namespaced_completion_generic_and_renamed(self):
        self.write("lib.gm", LIB)
        self.open('import "lib.gm" as m\nprint m.\n')
        res = self.completion(1, len("print m."))
        json.dumps(res)
        self.assertEqual(self.labels(res), {"Opt", "Name", "Alias", "id"})
        opt = self.item(res, "Opt")
        self.assertEqual(opt["kind"], 25)  # KIND_TYPE
        self.assertEqual(opt["detail"], "type Opt<T> = T | Nil")
        self.assertIn("target `T | Nil`", opt["documentation"])
        self.assertIn("Exported from `Maybe`.", opt["documentation"])

    # ------------------------------------------------------- hover
    def test_hover_generic_alias_shows_params_and_union(self):
        self.open(LOCAL)
        value = self.hover(6, len("let x: Maybe"))
        self.assertIn("type Maybe<T> = T | Nil", value)
        self.assertIn("Type parameters: `T`.", value)
        self.assertIn("Union of: `T`, `Nil`.", value)

    def test_hover_union_alias_lists_members(self):
        self.open(LOCAL)
        value = self.hover(5, len("type Name"))
        self.assertIn("type Name = Text | Bool", value)
        self.assertIn("Union of: `Text`, `Bool`.", value)
        self.assertNotIn("Type parameters:", value)

    def test_hover_expands_alias_chain_and_generic_application(self):
        self.open(LOCAL)
        copy = self.hover(1, len("type Copy"))
        self.assertIn("type Copy = Port", copy)
        self.assertIn("Expanded target: `Int`.", copy)

        nested = self.hover(4, len("type Nested"))
        self.assertIn("type Nested = Box<Port>", nested)
        self.assertIn("Expanded target: `Int`.", nested)

    def test_hover_namespaced_renamed_generic_alias(self):
        self.write("lib.gm", LIB)
        self.open('import "lib.gm" as m\nlet x: m.Opt<Int> = 1\n')
        value = self.hover(1, len("let x: m.Opt"))
        self.assertIn("type Opt<T> = T | Nil", value)
        self.assertIn("target is `T | Nil`", value)
        self.assertIn("Exported from `Maybe`.", value)
        self.assertIn("Type parameters: `T`.", value)
        self.assertIn("Union of: `T`, `Nil`.", value)

    def test_hover_union_alias_target_position(self):
        self.open('type Port = Int\n'
                  'type Name = Port | Text\n'
                  'type Local = Name\n')
        value = self.hover(2, len("type Local = Name"))
        self.assertIn("type Name = Port | Text", value)
        self.assertIn("Union of: `Port`, `Text`.", value)
        self.assertIn("Expanded target: `Int | Text`.", value)

    # --------------------------------------------------- definition
    def test_definition_local_generic_alias_in_annotation(self):
        self.open(LOCAL)
        lines = LOCAL.split("\n")
        got = self.definition(6, len("let x: Maybe"))
        self.assertEqual(got["uri"], self.uri)
        self.assertEqual(got["range"]["start"],
                         {"line": 3, "character": lines[3].index("Maybe")})

    def test_definition_local_generic_alias_in_target(self):
        self.open(LOCAL)
        lines = LOCAL.split("\n")
        got = self.definition(4, lines[4].index("Box") + 1)
        self.assertEqual(got["range"]["start"],
                         {"line": 2, "character": lines[2].index("Box")})

    def test_definition_namespaced_renamed_generic_alias(self):
        self.write("lib.gm", LIB)
        self.open('import "lib.gm" as m\nlet x: m.Opt<Int> = 1\n')
        lines = LIB.split("\n")
        got = self.definition(1, len("let x: m.Opt"))
        self.assertEqual(got["uri"], (self.root / "lib.gm").as_uri())
        self.assertEqual(got["range"]["start"],
                         {"line": 1, "character": lines[1].index("Maybe")})

    def test_definition_union_alias_in_annotation(self):
        self.open('type A = Int\n'
                  'type B = Text\n'
                  'type Name = A | B\n'
                  'let x: Name = 1\n')
        lines = 'type A = Int\ntype B = Text\ntype Name = A | B\n'.split("\n")
        got = self.definition(3, len("let x: Name"))
        self.assertEqual(got["range"]["start"],
                         {"line": 2, "character": lines[2].index("Name")})

    # ------------------------------------------- malformed / smoke
    def test_malformed_generic_and_union_aliases_stay_quiet(self):
        cases = (
            'type Pair<T = Int\n',
            'type Pair<T> = \n',
            'type Pair<> = Int\n',
            'type U = |\n',
            'type U = Int | \n',
            'type U = List<Int |\n',
        )
        for text in cases:
            with self.subTest(text=text):
                self.write("lib.gm", text)
                self.open('import "lib.gm" as m\nprint m.\n')
                res = self.completion(1, len("print m."))
                json.dumps(res)
                self.assertIsNone(self.hover(1, len("print m.Pa")))
                self.assertIsNone(self.definition(1, len("print m.Pa")))

    def test_malformed_local_generic_union_is_json_safe(self):
        cases = (
            'type Pair<T = Int\nlet x: \n',
            'type Pair<T> = \nlet x: Pair\n',
            'type U = Int | \nlet x: U\n',
            'type U = | \nlet x: \n',
        )
        for text in cases:
            with self.subTest(text=text):
                self.open(text)
                self.change(text + "# edit\n")
                results = [
                    self.completion(1, len("let x: ")),
                    self.at("textDocument/hover", 1, 0),
                    self.at("textDocument/definition", 0, 0),
                    self.at("textDocument/documentSymbol", 0, 0),
                ]
                for result in results:
                    json.dumps(result)

    def test_smoke_generic_union_requests_are_json_serializable(self):
        self.write("lib.gm", LIB)
        self.open('import "lib.gm" as m\n'
                  'type Local<T> = T | Nil\n'
                  'let x: m.Opt<Int> = 1\n'
                  'let y: Local<Int> = 1\n')
        results = [
            self.completion(1, len("print m.")),
            self.completion(2, len("let x: m.Opt")),
            self.completion(3, len("let y: Local")),
            self.at("textDocument/hover", 2, len("let x: m.Opt")),
            self.at("textDocument/hover", 3, len("let y: Local")),
            self.at("textDocument/definition", 2, len("let x: m.Opt")),
            self.at("textDocument/documentSymbol", 0, 0),
        ]
        for result in results:
            json.dumps(result)


if __name__ == "__main__":
    unittest.main()
