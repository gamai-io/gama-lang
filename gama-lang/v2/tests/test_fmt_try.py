"""Formatter regressions for the ``?`` postfix operator, comments and strings.

The runtime/tokenizer (``gamal2.py``) is intentionally not modified here; these
tests pin the formatter contract: the token stream must survive, ``?`` must stay
attached to its left operand, comments keep their text, and multi-line string
bodies are emitted byte-for-byte.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = (V2 / "gamal2.py").resolve()
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _semantic_tokens(src):
    """Token stream ignoring whitespace/newlines (comments are already skipped)."""
    return [(t.kind, t.val) for t in G.lex(src) if t.kind != "NL"]


class FmtTryOperatorTests(unittest.TestCase):
    def test_round_trip_and_idempotent(self):
        src = (
            "fn f() -> Result effects {} {\n"
            "    let x = Ok(1)?;\n"
            "    return Ok(x)\n"
            "}\n"
        )
        out = gama_fmt.format_source(src)
        self.assertEqual(
            out,
            "fn f() -> Result effects {} {\n"
            "  let x = Ok(1)?;\n"
            "  return Ok(x)\n"
            "}\n",
        )
        self.assertEqual(_semantic_tokens(src), _semantic_tokens(out))
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)

    def test_single_line_requirement_fixture(self):
        src = "fn f() -> Result effects {} { let x = Ok(1)?; return Ok(x) }\n"
        out = gama_fmt.format_source(src)
        self.assertIn("Ok(1)?;", out)
        self.assertEqual(_semantic_tokens(src), _semantic_tokens(out))
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)

    def test_postfix_whitespace_is_canonicalized(self):
        src = (
            "fn f() -> Result effects {} {\n"
            "  let x = Ok(1) ?;\n"
            "  return Ok(x)\n"
            "}\n"
        )
        out = gama_fmt.format_source(src)
        self.assertIn("Ok(1)?;", out)
        self.assertNotIn("Ok(1) ?", out)
        self.assertEqual(_semantic_tokens(src), _semantic_tokens(out))
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)

    def test_try_before_comment_keeps_comment_text(self):
        src = (
            "fn f() -> Result effects {} {\n"
            "  let x = Ok(1)? # inline: { } ? # not a second comment\n"
            "  return Ok(x)\n"
            "}\n"
        )
        out = gama_fmt.format_source(src)
        self.assertIn("Ok(1)? # inline: { } ? # not a second comment", out)
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)


class FmtCommentTests(unittest.TestCase):
    def test_comment_text_and_braces_are_preserved(self):
        src = (
            "# leading { } ? comment\n"
            "if true {\n"
            '  print "x" # inline { } ?\n'
            "# } fake close {\n"
            '  print "y"\n'
            "}\n"
        )
        out = gama_fmt.format_source(src)
        lines = out.splitlines()
        self.assertEqual(lines[0], "# leading { } ? comment")
        self.assertEqual(lines[2], '  print "x" # inline { } ?')
        self.assertEqual(lines[3], "  # } fake close {")
        self.assertEqual(lines[4], '  print "y"')
        self.assertEqual(lines[5], "}")
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)


class FmtStringTests(unittest.TestCase):
    def test_multiline_string_bytes_preserved(self):
        literal = '"first { ? # not comment\nsecond } inside # = + - ?"'
        src = (
            "fn f() -> Result effects {} {\n"
            f"let s = {literal}\n"
            "return Ok(s)\n"
            "}\n"
        )
        out = gama_fmt.format_source(src)
        self.assertIn(literal, out)
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)

    def test_single_line_string_contents_untouched(self):
        literal = '"one line ? { } # = + - * /"'
        src = (
            "fn f() -> Result effects {} {\n"
            f"  let s = {literal}\n"
            "  return Ok(s)\n"
            "}\n"
        )
        out = gama_fmt.format_source(src)
        self.assertIn(literal, out)
        self.assertEqual(out, gama_fmt.format_source(out))
        G.check(out)


class FmtTryCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(GAMAL2), *args],
            cwd=str(V2),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_check_write_check_is_idempotent(self):
        unformatted = (
            "fn f() -> Result effects {} {\n"
            "    let x = Ok(1) ?;\n"
            "    return Ok(x)\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "try.gm"
            path.write_text(unformatted, encoding="utf-8")

            p = self.run_cli("fmt", "--check", str(path))
            self.assertEqual(p.returncode, 1, p.stderr + p.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), unformatted)

            p = self.run_cli("fmt", "--write", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            formatted = path.read_text(encoding="utf-8")
            self.assertIn("Ok(1)?;", formatted)
            self.assertEqual(formatted, gama_fmt.format_source(formatted))

            p = self.run_cli("fmt", "--check", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)

            p = self.run_cli("check", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)

    def test_committed_fixture_is_canonical(self):
        path = (FIXTURES / "fmt_try.gm").resolve()
        self.assertTrue(path.is_file(), "missing fixture: %s" % path)
        p = self.run_cli("fmt", "--check", str(path))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main()
