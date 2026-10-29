"""CLI contract for `gamal2.py fmt --check|--write` (subprocess, absolute paths)."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = (V2 / "gamal2.py").resolve()

UNFORMATTED = "fn f() {\nlet x=1\n        return x+2\n}\n"
FORMATTED = "fn f() {\n  let x = 1\n  return x + 2\n}\n"


class FmtCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(GAMAL2), *args],
                              cwd=str(V2), capture_output=True,
                              text=True, timeout=30)

    def make_file(self, text, name="sample.gm"):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = (pathlib.Path(td.name) / name).resolve()
        path.write_text(text, encoding="utf-8")
        return path

    def test_check_reports_unformatted(self):
        path = self.make_file(UNFORMATTED)
        p = self.run_cli("fmt", "--check", str(path))
        self.assertEqual(p.returncode, 1, p.stderr + p.stdout)
        self.assertIn(f"needs formatting: {path}", p.stdout)
        self.assertEqual(path.read_text(encoding="utf-8"), UNFORMATTED)

    def test_write_then_check_clean(self):
        path = self.make_file(UNFORMATTED)
        p = self.run_cli("fmt", "--write", str(path))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertIn(f"{path}: formatted", p.stdout)
        self.assertEqual(path.read_text(encoding="utf-8"), FORMATTED)
        p = self.run_cli("fmt", "--check", str(path))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)

    def test_plain_fmt_prints_and_leaves_file(self):
        path = self.make_file(UNFORMATTED)
        p = self.run_cli("fmt", str(path))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(p.stdout, FORMATTED)
        self.assertEqual(path.read_text(encoding="utf-8"), UNFORMATTED)

    def test_check_and_write_conflict(self):
        path = self.make_file(UNFORMATTED)
        p = self.run_cli("fmt", "--check", "--write", str(path))
        self.assertEqual(p.returncode, 2, p.stderr + p.stdout)
        self.assertIn("cannot be used together", p.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), UNFORMATTED)


if __name__ == "__main__":
    unittest.main()
