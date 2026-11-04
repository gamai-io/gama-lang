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

    def test_namespaced_call_and_non_fn_ignored(self):
        self._write(
            "lib.gm",
            'let hidden = 7\n'
            'print "MODULE SIDE EFFECT"\n'
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write("main.gm", 'import "lib.gm" as math\nprint math.add(2,3)\n')
        it = G.run_file(str(self.root / "main.gm"))
        # Only the caller's print runs; the module's top-level print/let are
        # ignored by the bounded MVP.
        self.assertEqual(it.out, ["5"])

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
            'let value = 5\n'
            'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n',
        )
        self._write("main.gm", 'import "lib.gm" as math\nprint math.nope()\n')
        with self.assertRaises(G.GamaTypeError):
            G.run_file(str(self.root / "main.gm"))

    def test_top_level_let_not_exposed_under_alias(self):
        self._write("lib.gm", 'let value = 5\n')
        self._write("main.gm", 'import "lib.gm" as math\nprint math.value\n')
        with self.assertRaises(G.GamaTypeError):
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
