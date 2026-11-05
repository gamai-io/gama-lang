import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def _run(self, main, lib):
        self._write("lib.gm", lib)
        self._write("main.gm", main)
        return G.run_file(str(self.root / "main.gm"))

    def test_exported_fn_and_let_visible_through_alias(self):
        it = self._run(
            'import "lib.gm" as m\nprint m.add(2, 3)\nprint m.pi\n',
            'export let pi: Decimal = 3.14\n'
            'export fn add(a: Int, b: Int) -> Int effects {} '
            '{ return a + b }\n',
        )
        self.assertEqual(it.out, ["5", "3.14"])

    def test_private_fn_is_hidden_from_alias(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n'
            'fn helper(x: Int) -> Int effects {} { return x }\n',
        )
        self._write("main.gm", 'import "lib.gm" as m\nprint m.helper(1)\n')
        with self.assertRaisesRegex(G.GamaTypeError, "unknown function m.helper"):
            G.run_file(str(self.root / "main.gm"))

    def test_private_let_is_hidden_from_alias(self):
        self._write(
            "lib.gm",
            'let secret = 7\n'
            'export fn get() -> Int effects {} { return secret }\n',
        )
        self._write("main.gm", 'import "lib.gm" as m\nprint m.get()\nprint m.secret\n')
        with self.assertRaisesRegex(G.GamaTypeError, "unknown variable m.secret"):
            G.run_file(str(self.root / "main.gm"))

    def test_private_helper_callable_inside_module(self):
        it = self._run(
            'import "lib.gm" as m\nprint m.add(2, 3)\n',
            'export fn add(a: Int, b: Int) -> Int effects {} '
            '{ return helper(a) + b }\n'
            'fn helper(x: Int) -> Int effects {} { return x }\n',
        )
        self.assertEqual(it.out, ["5"])

    def test_exported_record_visible_private_record_hidden(self):
        lib = (
            'export record Point { x: Int, y: Int }\n'
            'record Hidden { z: Int }\n'
            'export fn origin() -> Point effects {} { return Point(0, 0) }\n'
        )
        it = self._run(
            'import "lib.gm" as g\n'
            'let p: g.Point = g.origin()\n'
            'print p.x\n'
            'let q = g.Point(1, 2)\n'
            'print q.y\n',
            lib,
        )
        self.assertEqual(it.out, ["0", "2"])
        self._write("hidden.gm", 'import "lib.gm" as g\nlet h = g.Hidden(1)\n')
        with self.assertRaisesRegex(G.GamaError, "unknown function g.Hidden"):
            G.run_file(str(self.root / "hidden.gm"))

    def test_legacy_module_without_exports_keeps_alias_behaviour(self):
        it = self._run(
            'import "lib.gm" as m\nprint m.add(2, 3)\nprint m.secret\n',
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n'
            'let secret = 7\n',
        )
        self.assertEqual(it.out, ["5", "7"])

    def test_plain_import_flattens_private_declarations(self):
        it = self._run(
            'import "lib.gm"\nprint add(1, 2)\nprint secret\n',
            'export fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n'
            'fn helper(x: Int) -> Int effects {} { return x }\n'
            'let secret = 7\n',
        )
        self.assertEqual(it.out, ["3", "7"])
        self._write("main.gm", 'import "lib.gm"\nprint helper(4)\n')
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["4"])

    def test_duplicate_export_rejected(self):
        self._write("main.gm", "export let x = 1\nexport let x = 2\n")
        with self.assertRaisesRegex(G.GamaError, "duplicate export 'x'"):
            G.check_file(str(self.root / "main.gm"))

    def test_duplicate_export_across_kinds_rejected(self):
        self._write(
            "main.gm",
            'export fn x() -> Int effects {} { return 1 }\n'
            'export let x = 2\n',
        )
        with self.assertRaisesRegex(G.GamaError, "duplicate export 'x'"):
            G.check_file(str(self.root / "main.gm"))

    def test_export_inside_block_rejected(self):
        self._write(
            "main.gm",
            'fn f() -> Int effects {} {\n  export let x = 1\n  return x\n}\n',
        )
        with self.assertRaisesRegex(G.GamaError, "export is only allowed at top level"):
            G.check_file(str(self.root / "main.gm"))

    def test_export_enum_is_allowed(self):
        self._write(
            "lib.gm",
            "export enum Color { Red, Green }\n",
        )
        self._write(
            "main.gm",
            'import "lib.gm" as m\nprint m.Color.Green\n',
        )
        self.assertEqual(G.run_file(str(self.root / "main.gm")).out,
                         ["m.Color.Green"])

    def test_export_requires_declaration(self):
        self._write("main.gm", "export 1\n")
        with self.assertRaisesRegex(
            G.GamaError, "export must be followed by fn, let, record or enum"
        ):
            G.check_file(str(self.root / "main.gm"))

    def test_alias_collides_with_existing_top_level_name(self):
        self._write("lib.gm", 'export fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n')
        self._write(
            "main.gm",
            'let math = 1\nimport "lib.gm" as math\nprint math.add(1, 2)\n',
        )
        with self.assertRaisesRegex(
            G.GamaError, "import alias 'math' collides with existing top-level name"
        ):
            G.run_file(str(self.root / "main.gm"))

    def test_exported_namespace_has_no_global_leak(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write("main.gm", 'import "lib.gm" as m\nprint add(1, 2)\n')
        with self.assertRaisesRegex(G.GamaTypeError, "unknown function add"):
            G.run_file(str(self.root / "main.gm"))


if __name__ == "__main__":
    unittest.main()
