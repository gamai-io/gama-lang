import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class NamespaceTests(unittest.TestCase):
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

    def test_namespaced_call_works(self):
        self._write(
            "lib.gm",
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write("main.gm", 'import "lib.gm" as math\nprint math.add(2,3)\n')
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["5"])

    def test_namespaced_constant_read_from_main(self):
        self._write("lib.gm", 'let pi: Decimal = 3.14\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint math.pi\n')
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["3.14"])

    def test_module_function_reads_module_constant(self):
        self._write("lib.gm", 'let factor = 10\n'
                              'fn scale(x: Int) -> Int effects {} { return x * factor }\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint math.scale(3)\n')
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["30"])

    def test_constant_before_function_reference(self):
        # A constant declared before an unrelated function is in scope when
        # that function body is checked and executed.
        self._write("lib.gm", 'let base = 5\n'
                              'fn plus(x: Int) -> Int effects {} { return base + x }\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint math.plus(2)\n')
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["7"])

    def test_alias_rejects_top_level_print(self):
        self._write("lib.gm", 'print "MODULE SIDE EFFECT"\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint 1\n')
        with self.assertRaisesRegex(
            G.GamaError,
            r"aliased import 'math'.*unsupported top-level statement 'print'",
        ):
            G.run_file(str(self.root / "main.gm"))

    def test_alias_rejects_top_level_prove(self):
        self._write("lib.gm", 'prove total = 2 + 2\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint 1\n')
        with self.assertRaisesRegex(
            G.GamaError,
            r"aliased import 'math'.*unsupported top-level statement 'prove'",
        ):
            G.run_file(str(self.root / "main.gm"))

    def test_alias_rejects_top_level_verify(self):
        self._write("lib.gm", 'verify true\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint 1\n')
        with self.assertRaisesRegex(
            G.GamaError,
            r"aliased import 'math'.*unsupported top-level statement 'verify'",
        ):
            G.run_file(str(self.root / "main.gm"))

    def test_alias_isolation_same_fn_name(self):
        self._write("one.gm", 'fn value() -> Int effects {} { return 1 }\n')
        self._write("two.gm", 'fn value() -> Int effects {} { return 2 }\n')
        self._write(
            "main.gm",
            'import "one.gm" as a\n'
            'import "two.gm" as b\n'
            'print a.value()\n'
            'print b.value()\n',
        )
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["1", "2"])

    def test_missing_export_is_rejected(self):
        self._write(
            "lib.gm",
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write("main.gm", 'import "lib.gm" as math\nprint math.nope()\n')
        with self.assertRaises(G.GamaTypeError):
            G.run_file(str(self.root / "main.gm"))

    def test_missing_constant_is_rejected(self):
        self._write("lib.gm", 'let value = 5\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint math.missing\n')
        with self.assertRaisesRegex(G.GamaTypeError, "unknown variable math.missing"):
            G.run_file(str(self.root / "main.gm"))

    def test_duplicate_constant_name_in_module_rejected(self):
        self._write("lib.gm", 'let value = 5\nlet value = 6\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint math.value\n')
        with self.assertRaisesRegex(
            G.GamaError, r"import alias 'math' produces duplicate name 'math.value'"
        ):
            G.run_file(str(self.root / "main.gm"))

    def test_constant_collides_with_existing_top_level_name(self):
        self._write("lib.gm", 'let value = 5\n')
        self._write(
            "main.gm",
            'let math.value = 1\nimport "lib.gm" as math\nprint math.value\n',
        )
        with self.assertRaisesRegex(
            G.GamaError, r"import alias 'math' produces duplicate name 'math.value'"
        ):
            G.run_file(str(self.root / "main.gm"))

    def test_no_alias_keeps_flatten_behavior(self):
        self._write(
            "lib.gm",
            'let value = 41\n'
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write(
            "main.gm",
            'import "lib.gm"\nprint add(2,3)\nprint value\n',
        )
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["5", "41"])

    def test_plain_import_allows_top_level_let(self):
        self._write("lib.gm", 'let answer = 42\n')
        self._write("main.gm", 'import "lib.gm"\nprint answer\n')
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["42"])

    def test_alias_module_with_only_fn_and_import_works(self):
        self._write("helper.gm", 'fn triple(x: Int) -> Int effects {} { return x * 3 }\n')
        self._write(
            "lib.gm",
            'import "helper.gm"\n'
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write(
            "main.gm",
            'import "lib.gm" as math\nprint math.add(2,3)\nprint math.triple(3)\n',
        )
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["5", "9"])

    def test_module_own_imports_and_sibling_calls(self):
        self._write("helper.gm", 'fn twice(x: Int) -> Int effects {} { return x * 2 }\n')
        self._write(
            "lib.gm",
            'import "helper.gm"\n'
            'fn inc(x: Int) -> Int effects {} { return x + 1 }\n'
            'fn add(a: Int, b: Int) -> Int effects {} { return inc(a) + b }\n'
            'fn quad(x: Int) -> Int effects {} { return twice(twice(x)) }\n',
        )
        self._write(
            "main.gm",
            'import "lib.gm" as math\nprint math.add(2,3)\nprint math.quad(2)\n',
        )
        it = G.run_file(str(self.root / "main.gm"))
        self.assertEqual(it.out, ["6", "8"])

    def test_path_outside_root_rejected(self):
        # The project root is the main file's directory (`app`); a sibling of
        # `app` is outside it.
        outside = self._write("outside_module_xyz.gm", 'fn leak() -> Int effects {} { return 1 }\n')
        app = self.root / "app"
        app.mkdir()
        (app / "main.gm").write_text(
            'import "../outside_module_xyz.gm" as x\nprint x.leak()\n',
            encoding="utf-8",
        )
        self.assertTrue(outside.exists())
        with self.assertRaisesRegex(G.GamaError, "outside project root"):
            G.run_file(str(app / "main.gm"))

    def test_duplicate_alias_rejected(self):
        self._write("lib.gm", 'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n')
        self._write(
            "main.gm",
            'import "lib.gm" as math\n'
            'import "lib.gm" as math\n'
            'print math.add(1,2)\n',
        )
        with self.assertRaisesRegex(G.GamaError, "duplicate import alias"):
            G.run_file(str(self.root / "main.gm"))

    def test_invalid_alias_rejected(self):
        self._write("lib.gm", 'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n')
        self._write("main.gm", 'import "lib.gm" as a.b\nprint a.b.add(1,2)\n')
        with self.assertRaisesRegex(G.GamaError, "invalid import alias"):
            G.run_file(str(self.root / "main.gm"))

    def test_alias_colliding_with_top_level_name_rejected(self):
        self._write("lib.gm", 'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n')
        self._write(
            "main.gm",
            'fn math() -> Int effects {} { return 1 }\n'
            'import "lib.gm" as math\n'
            'print math.add(1,2)\n',
        )
        with self.assertRaisesRegex(G.GamaError, "collides with existing top-level name"):
            G.run_file(str(self.root / "main.gm"))


if __name__ == "__main__":
    unittest.main()
