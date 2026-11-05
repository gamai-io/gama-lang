"""LSP support for type aliases and selective re-exports.

Core GAMA-L has no ``type Name = Target`` syntax and rejects selective
``export { ... }`` lists. The LSP therefore parses both structurally and uses
them only for completion, hover and definition (nothing is evaluated):

* ``alias.`` completion lists exported type aliases; a selective export list
  narrows the surface to exactly its members and applies ``as`` renames.
* Hover on an alias shows its target and, for a rename, the original name.
* Definition on an alias (including ``alias.Alias`` in an annotation) jumps
  to the original alias declaration.
* ``type`` targets and ordinary annotations complete local aliases,
  records, enums and built-in types.
* Malformed documents stay quiet and every response is JSON-serializable.
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


LIB_ALIAS = (
    'type Port = Int\n'                                   # 0
    'export type UserId = Int as UID\n'                   # 1
    'export type Label = Text\n'                          # 2
    'export record Point { x: Int, y: Int }\n'            # 3
    'export fn add(a: Int, b: Int) -> Int effects {} '
    '{ return a + b }\n'                                  # 4
    'fn secret() -> Int effects {} { return 1 }\n'        # 5
)

LIB_SELECTIVE = (
    'type Port = Int\n'                                   # 0
    'type UserId = Int\n'                                 # 1
    'record Point { x: Int, y: Int }\n'                   # 2
    'fn add(a: Int, b: Int) -> Int effects {} '
    '{ return a + b }\n'                                  # 3
    'fn secret() -> Int effects {} { return 1 }\n'        # 4
    'export { Port as PortNum, UserId, add as plus }\n'   # 5
)

LOCAL_ALIAS = (
    'type Port = Int\n'                                   # 0
    'type Copy = Port\n'                                  # 1
    'let x: Port = 1\n'                                   # 2
)


class LspAliasReexportTests(unittest.TestCase):
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

    # ------------------------------------------------- completion: export list
    def test_alias_completion_per_declaration_exports(self):
        self.write("lib.gm", LIB_ALIAS)
        self.open('import "lib.gm" as m\nprint m.\n')
        res = self.completion(1, len("print m."))
        json.dumps(res)
        self.assertEqual(self.labels(res), {"UID", "Label", "Point", "add"})
        for hidden in ("Port", "UserId", "secret"):
            self.assertNotIn(hidden, self.labels(res))

        uid = self.item(res, "UID")
        self.assertEqual(uid["kind"], 25)  # KIND_TYPE
        self.assertEqual(uid["detail"], "type UID = Int")
        self.assertIn("target `Int`", uid["documentation"])
        self.assertIn("Exported from `UserId`.", uid["documentation"])

        label = self.item(res, "Label")
        self.assertEqual(label["detail"], "type Label = Text")
        self.assertNotIn("Exported from", label["documentation"])

    def test_selective_export_list_limits_and_renames(self):
        self.write("lib.gm", LIB_SELECTIVE)
        self.open('import "lib.gm" as m\nprint m.\n')
        res = self.completion(1, len("print m."))
        json.dumps(res)
        self.assertEqual(self.labels(res), {"PortNum", "UserId", "plus"})
        for hidden in ("Port", "add", "Point", "secret", "UID"):
            self.assertNotIn(hidden, self.labels(res))

        port = self.item(res, "PortNum")
        self.assertEqual(port["kind"], 25)
        self.assertEqual(port["detail"], "type PortNum = Int")
        self.assertIn("Exported from `Port`.", port["documentation"])

        plus = self.item(res, "plus")
        self.assertEqual(plus["kind"], 3)
        self.assertIn("plus(a: Int, b: Int) -> Int", plus["detail"])

    # ------------------------------------------------------------- hover
    def test_hover_alias_shows_target_and_original_name(self):
        self.write("lib.gm", LIB_ALIAS)
        self.open('import "lib.gm" as m\n'
                  'print m.UID\n'
                  'print m.Label\n')
        uid = self.hover(1, len("print m.UID"))
        self.assertIn("type UID = Int", uid)
        self.assertIn("target is `Int`", uid)
        self.assertIn("Exported from `UserId`.", uid)

        label = self.hover(2, len("print m.Label"))
        self.assertIn("type Label = Text", label)
        self.assertNotIn("Exported from", label)

    def test_hover_selective_rename(self):
        self.write("lib.gm", LIB_SELECTIVE)
        self.open('import "lib.gm" as m\nprint m.PortNum\n')
        value = self.hover(1, len("print m.PortNum"))
        self.assertIn("type PortNum = Int", value)
        self.assertIn("the alias target is `Int`", value)
        self.assertIn("Exported from `Port`.", value)

    # ----------------------------------------------------------- definition
    def test_definition_alias_lands_on_original_name(self):
        self.write("lib.gm", LIB_ALIAS)
        self.open('import "lib.gm" as m\n'
                  'print m.UID\n'
                  'let u: m.UID = 1\n')
        lines = LIB_ALIAS.split("\n")

        uid = self.definition(1, len("print m.UID"))
        self.assertEqual(uid["uri"], (self.root / "lib.gm").as_uri())
        self.assertEqual(uid["range"]["start"],
                         {"line": 1, "character": lines[1].index("UserId")})

        annotated = self.definition(2, len("let u: m.UID"))
        self.assertEqual(annotated["range"]["start"],
                         {"line": 1, "character": lines[1].index("UserId")})

    def test_definition_selective_rename(self):
        self.write("lib.gm", LIB_SELECTIVE)
        self.open('import "lib.gm" as m\nprint m.PortNum\n')
        lines = LIB_SELECTIVE.split("\n")
        got = self.definition(1, len("print m.PortNum"))
        self.assertEqual(got["uri"], (self.root / "lib.gm").as_uri())
        self.assertEqual(got["range"]["start"],
                         {"line": 0, "character": lines[0].index("Port")})

    # ------------------------------------------------ local aliases
    def test_local_alias_hover_and_definition_in_annotations(self):
        self.open(LOCAL_ALIAS)
        lines = LOCAL_ALIAS.split("\n")

        value = self.hover(2, len("let x: Port"))
        self.assertIn("type Port = Int", value)

        got = self.definition(2, len("let x: Port"))
        self.assertEqual(got["uri"], self.uri)
        self.assertEqual(got["range"]["start"],
                         {"line": 0, "character": lines[0].index("Port")})

        # Alias target position also resolves.
        target = self.definition(1, len("type Copy = Port"))
        self.assertEqual(target["range"]["start"],
                         {"line": 0, "character": lines[0].index("Port")})

    def test_local_alias_completion_in_target_and_annotation(self):
        self.open('type Port = Int\nlet x: \n')
        annotation = self.completion(1, len("let x: "))
        json.dumps(annotation)
        self.assertIn("Port", self.labels(annotation))
        self.assertEqual(self.item(annotation, "Port")["detail"],
                         "type Port = Int")
        self.assertIn("Int", self.labels(annotation))

        self.change('type Port = Int\ntype Alias = \n')
        target = self.completion(1, len("type Alias = "))
        json.dumps(target)
        self.assertIn("Port", self.labels(target))
        self.assertIn("Int", self.labels(target))

    # --------------------------------------------- malformed / smoke
    def test_malformed_alias_and_selective_lists_stay_quiet(self):
        cases = (
            'type = Int\n',
            'type Port Int\n',
            'export { Port as\n',
            'export { Port as PortNum, }\nfn add(a: Int) -> Int '
            'effects {} { return a }\n',
        )
        for text in cases:
            with self.subTest(text=text):
                self.write("lib.gm", text)
                self.open('import "lib.gm" as m\nprint m.\n')
                res = self.completion(1, len("print m."))
                json.dumps(res)
                self.assertIsNone(self.hover(1, len("print m.Po")))
                self.assertIsNone(self.definition(1, len("print m.Po")))

    def test_malformed_local_alias_is_json_safe(self):
        for text in ('type = Int\nlet x: \n', 'type Port = \nlet x: Port\n'):
            with self.subTest(text=text):
                self.open(text)
                self.change(text + "# edit\n")
                for result in (
                        self.completion(1, len("let x: ")),
                        self.at("textDocument/hover", 1, 0),
                        self.at("textDocument/definition", 0, 0)):
                    json.dumps(result)

    def test_smoke_all_alias_requests_are_json_serializable(self):
        self.write("lib.gm", LIB_SELECTIVE)
        self.open('import "lib.gm" as m\n'
                  'type Local = Int\n'
                  'print m.PortNum\n'
                  'let x: Local = 1\n')
        results = [
            self.completion(2, len("print m.PortNum")),
            self.completion(3, len("let x: Local")),
            self.at("textDocument/hover", 2, len("print m.PortNum")),
            self.at("textDocument/definition", 2, len("print m.PortNum")),
            self.at("textDocument/documentSymbol", 0, 0),
        ]
        for result in results:
            json.dumps(result)


if __name__ == "__main__":
    unittest.main()
