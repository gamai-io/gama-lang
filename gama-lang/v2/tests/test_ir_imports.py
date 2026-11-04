"""IR backend: ``import`` resolution.

The opt-in IR backend must see the same flattened program the interpreter
runs.  ``gama_ir.compile_file`` delegates to the core loader
(``gamal2.check_file``), so root bounds, cycle detection and import depth are
not reimplemented here.  These tests pin plain imports plus the aliased import
path (dotted functions, constants and records resolve like local names).
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR

GAMAL2 = V2 / "gamal2.py"


class IrImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, src):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        return p

    def assert_parity(self, main):
        ref = G.run_file(str(main))
        got = IR.run_file(str(main))
        self.assertEqual(got.out, ref.out)
        return ref.out

    # -- supported: plain imports -------------------------------------------
    def test_plain_import_fn_matches_interpreter(self):
        self.write("lib.gm", "fn double(x: Int) -> Int { return x * 2 }\n")
        main = self.write("main.gm", 'import "lib.gm"\nprint double(21)\n')
        self.assertEqual(self.assert_parity(main), ["42"])

    def test_plain_import_lets_and_records(self):
        self.write(
            "geo.gm",
            "let unit: Text = \"cm\"\n"
            "record Point { x: Int, y: Int }\n"
            "fn origin() -> Point { return Point(0, 0) }\n",
        )
        main = self.write(
            "main.gm",
            'import "geo.gm"\n'
            "let p = origin()\n"
            "print p.x + p.y\n"
            "print unit\n",
        )
        self.assertEqual(self.assert_parity(main), ["0", "cm"])

    def test_nested_plain_imports_flatten(self):
        self.write("inner.gm", "fn triple(x: Int) -> Int { return x * 3 }\n")
        self.write("lib.gm", 'import "inner.gm"\nfn six(x: Int) -> Int { return triple(x) * 2 }\n')
        main = self.write("main.gm", 'import "lib.gm"\nprint six(7)\n')
        self.assertEqual(self.assert_parity(main), ["42"])

    def test_compile_file_returns_program_with_flattened_import(self):
        self.write("lib.gm", "fn add(a: Int, b: Int) -> Int { return a + b }\n")
        main = self.write("main.gm", 'import "lib.gm"\nprint add(2, 3)\n')
        program = IR.compile_file(main)
        self.assertIsInstance(program, IR.Program)
        listing = program.disassemble()
        self.assertIn("DEF", listing)
        self.assertIn("add", listing)

    def test_relative_subdirectory_import(self):
        self.write("mod/util.gm", "fn wrap(x: Int) -> Int { return x + 1 }\n")
        main = self.write("main.gm", 'import "mod/util.gm"\nprint wrap(41)\n')
        self.assertEqual(self.assert_parity(main), ["42"])

    # -- CLI parity ----------------------------------------------------------
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(GAMAL2), *args],
            cwd=str(V2), capture_output=True, text=True, timeout=30,
        )

    def test_cli_ir_run_import_matches_default_run(self):
        self.write("lib.gm", "fn double(x: Int) -> Int { return x * 2 }\n")
        main = self.write("main.gm", 'import "lib.gm"\nprint double(21)\n')
        ref = self.run_cli("run", str(main))
        got = self.run_cli("ir", "run", str(main))
        self.assertEqual(ref.returncode, 0, ref.stderr + ref.stdout)
        self.assertEqual(got.returncode, 0, got.stderr + got.stdout)
        self.assertEqual(got.stdout, ref.stdout)
        self.assertIn("42", got.stdout)

    def test_cli_ir_compile_import_is_flattened(self):
        self.write("lib.gm", "fn add(a: Int, b: Int) -> Int { return a + b }\n")
        main = self.write("main.gm", 'import "lib.gm"\nprint add(2, 3)\n')
        p = self.run_cli("ir", "compile", str(main))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DEF", p.stdout)
        self.assertIn("add", p.stdout)
        self.assertNotIn("Import", p.stdout)

    # -- clear errors -------------------------------------------------------
    def test_missing_import_is_clear(self):
        main = self.write("main.gm", 'import "nope.gm"\nprint 1\n')
        with self.assertRaises(G.GamaError) as ctx:
            IR.compile_file(main)
        self.assertIn("cannot import", str(ctx.exception))
        self.assertIn("nope.gm", str(ctx.exception))

    def test_missing_import_cli_stderr_is_clear(self):
        main = self.write("main.gm", 'import "nope.gm"\nprint 1\n')
        p = self.run_cli("ir", "run", str(main))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertIn("GamaError:", p.stderr)
        self.assertIn("cannot import", p.stderr)

    def test_import_cycle_is_clear(self):
        self.write("a.gm", 'import "b.gm"\nprint 1\n')
        self.write("b.gm", 'import "a.gm"\nprint 2\n')
        with self.assertRaises(G.GamaError) as ctx:
            IR.compile_file(self.root / "a.gm")
        self.assertIn("import cycle", str(ctx.exception))

    def test_import_cycle_cli_stderr_is_clear(self):
        self.write("a.gm", 'import "b.gm"\nprint 1\n')
        self.write("b.gm", 'import "a.gm"\nprint 2\n')
        p = self.run_cli("ir", "run", str(self.root / "a.gm"))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertIn("import cycle", p.stderr)

    def test_import_outside_project_root_is_rejected(self):
        outside = pathlib.Path(self._tmp.name).parent / "outside_ir_import.gm"
        outside.write_text("fn leaked() -> Int { return 1 }\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        main = self.write("main.gm", 'import "../outside_ir_import.gm"\nprint leaked()\n')
        with self.assertRaises(G.GamaError) as ctx:
            IR.compile_file(main)
        self.assertIn("import outside project root", str(ctx.exception))

    # -- aliased imports ----------------------------------------------------
    def test_namespaced_import_matches_interpreter(self):
        self.write("lib.gm", "fn double(x: Int) -> Int { return x * 2 }\n")
        main = self.write("main.gm", 'import "lib.gm" as m\nprint m.double(21)\n')
        self.assertEqual(self.assert_parity(main), ["42"])

    def test_namespaced_import_cli_matches_default_run(self):
        self.write("lib.gm", "fn double(x: Int) -> Int { return x * 2 }\n")
        main = self.write("main.gm", 'import "lib.gm" as m\nprint m.double(21)\n')
        ref = self.run_cli("run", str(main))
        got = self.run_cli("ir", "run", str(main))
        self.assertEqual(ref.returncode, 0, ref.stderr + ref.stdout)
        self.assertEqual(got.returncode, 0, got.stderr + got.stdout)
        self.assertEqual(got.stdout, ref.stdout)
        self.assertIn("42", got.stdout)

    def test_compile_program_source_string_import_points_to_file_api(self):
        # ``compile_program(str)`` has no filesystem context; the core checker
        # rejects the unresolved import with an actionable message.
        with self.assertRaises(G.GamaError) as ctx:
            IR.compile_program('import "lib.gm"\nprint 1\n')
        self.assertIn("import requires check_file/run_file", str(ctx.exception))

    def test_compile_program_parsed_import_is_irunsupported(self):
        # Even bypassing the checker, an unresolved Import node cannot compile.
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(G.parse('import "lib.gm"\nprint 1\n'))
        self.assertIn("IR does not support Import", str(ctx.exception))
        self.assertIn("compile_file", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
