"""CLI tests for the opt-in IR backend: ``gamal2 ir run|compile``.

These go through the real CLI entry point in a subprocess, so they also prove
the default ``run`` backend is untouched.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
GAMA_CLI = V2 / "gama_cli.py"


class IrCliTests(unittest.TestCase):
    def run_cli(self, *args, script=GAMAL2):
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(V2), capture_output=True, text=True, timeout=30,
        )

    def write(self, root, name, src):
        path = pathlib.Path(root) / name
        path.write_text(src, encoding="utf-8")
        return path

    def test_ir_run_supported_matches_interpreter(self):
        src = (
            'print 6*7\n'
            'let xs = [1, 2, 3]\n'
            'for x in xs { print x }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'print add(2, 3)\n'
        )
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "ok.gm", src)
            ref = self.run_cli("run", str(f))
            got = self.run_cli("ir", "run", str(f))
        self.assertEqual(ref.returncode, 0, ref.stderr + ref.stdout)
        self.assertEqual(got.returncode, 0, got.stderr + got.stdout)
        self.assertEqual(got.stdout, ref.stdout)
        self.assertIn("42", got.stdout)

    def test_ir_run_via_console_wrapper(self):
        # ``gama_cli.py`` is the packaged entry point (imports gamal2 normally).
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "ok.gm", "print 2 + 3\n")
            p = self.run_cli("ir", "run", str(f), script=GAMA_CLI)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(p.stdout.strip(), "5")

    def test_ir_compile_and_disasm_listing_non_empty(self):
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "ok.gm", "let x = 1\nprint x + 2\n")
            compiled = self.run_cli("ir", "compile", str(f))
            disasm = self.run_cli("ir", "disasm", str(f))
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertTrue(compiled.stdout.strip())
        self.assertIn("PRINT", compiled.stdout)
        self.assertEqual(disasm.stdout, compiled.stdout)

    def test_ir_compile_json(self):
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "ok.gm", "print 1\n")
            p = self.run_cli("ir", "compile", "--json", str(f))
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["file"], str(f))
        self.assertTrue(data["instructions"])
        self.assertEqual(data["instructions"][0]["index"], 0)
        self.assertTrue(all("op" in i for i in data["instructions"]))

    def test_ir_run_unsupported_fails_clearly(self):
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "unsup.gm", 'print sql("select 1")\n')
            p = self.run_cli("ir", "run", str(f))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertIn("IRUnsupported:", p.stderr)
        self.assertIn("IR does not support", p.stderr)

    def test_ir_invalid_file_error(self):
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "bad.gm", "let x =\n")
            p = self.run_cli("ir", "run", str(f))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertIn("GamaError:", p.stderr)

    def test_ir_missing_file_error(self):
        p = self.run_cli("ir", "run", str(V2 / "does-not-exist.gm"))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertTrue(p.stderr.strip())

    def test_default_run_backend_still_runs_unsupported_ir_program(self):
        # ``split`` is outside the IR subset but fine for the interpreter.
        src = 'print split("a,b", ",")\n'
        with tempfile.TemporaryDirectory() as td:
            f = self.write(td, "split.gm", src)
            default = self.run_cli("run", str(f))
            ir = self.run_cli("ir", "run", str(f))
        self.assertEqual(default.returncode, 0, default.stderr + default.stdout)
        self.assertEqual(default.stdout.strip(), '["a", "b"]')
        self.assertNotEqual(ir.returncode, 0)
        self.assertIn("IRUnsupported:", ir.stderr)


if __name__ == "__main__":
    unittest.main()
