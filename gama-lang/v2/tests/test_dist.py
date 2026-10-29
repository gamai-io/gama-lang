import pathlib
import subprocess
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]


class DistTests(unittest.TestCase):
    def test_files_exist(self):
        for name in ("gama_cli.py", "pyproject.toml", "MANIFEST.in", "gamal2.py", "gama_lsp.py"):
            self.assertTrue((V2 / name).is_file(), "missing %s" % name)

    def test_pyproject_declarations(self):
        text = (V2 / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("py-modules", text)
        self.assertIn('"gamal2"', text)
        self.assertIn('"gama_lsp"', text)
        self.assertIn('"gama_cli"', text)
        self.assertIn("[project.scripts]", text)
        self.assertIn('gama-l = "gama_cli:main"', text)
        self.assertIn('gama-lsp = "gama_lsp:main"', text)

    def test_cli_check_example(self):
        p = subprocess.run(
            [sys.executable, "gama_cli.py", "check", "examples/hello.gm"],
            cwd=str(V2), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_cli_help(self):
        p = subprocess.run(
            [sys.executable, "gama_cli.py", "--help"],
            cwd=str(V2), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("usage", p.stdout.lower())


if __name__ == "__main__":
    unittest.main()
