"""Width wrapping and expression-comment tests for the AST formatter.

Contract under test:

* Long list/map/record/call/binary/pipe expressions are wrapped at a
  configurable soft width (default 88); short constructs are untouched.
* Wrapping adds and removes no tokens (no trailing commas, no extra
  parentheses), so ``parse`` yields the same AST and the semantic token stream
  is identical.
* Comments inside expanded list/map/call expressions stay on the entry they
  annotate; comments after binary operators stay on the operator line.
  Comments that cannot be placed without a line break for a *short* expression
  still use the documented safe-position fallback and are never dropped or
  duplicated.
* ``format(format(x)) == format(x)`` at every width.
"""
import dataclasses
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt


def shape(node):
    if dataclasses.is_dataclass(node):
        items = []
        for f in dataclasses.fields(node):
            if f.name in ("line", "col"):
                continue
            value = getattr(node, f.name)
            if type(node).__name__ == "EnumDecl" and f.name == "variants":
                value = [v[0] if isinstance(v, tuple) else v for v in value]
            items.append((f.name, shape(value)))
        return (type(node).__name__, tuple(items))
    if isinstance(node, (list, tuple)):
        return tuple(shape(x) for x in node)
    if isinstance(node, dict):
        return tuple((k, shape(v)) for k, v in node.items())
    if isinstance(node, set):
        return tuple(sorted(shape(x) for x in node))
    return node


def comments_in(src):
    out = []
    i, n = 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            i += 1
            continue
        if c == "#":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(src[i:j].rstrip())
            i = j
            continue
        i += 1
    return out


def tokens(src):
    return [(t.kind, t.val) for t in G.lex(src) if t.kind != "NL"]


class WrapBase(unittest.TestCase):
    def roundtrip(self, src, width=gama_fmt.DEFAULT_WIDTH):
        out = gama_fmt.format_source(src, width=width)
        self.assertEqual(out, gama_fmt.format_source(out, width=width),
                         "not idempotent at width %d" % width)
        self.assertEqual(shape(G.parse(src)), shape(G.parse(out)),
                         "formatted AST differs")
        self.assertEqual(tokens(src), tokens(out),
                         "wrapping changed the token stream")
        self.assertEqual(comments_in(src), comments_in(out),
                         "comments changed or reordered")
        G.check(out)
        return out


class LongContainerTests(WrapBase):
    def test_long_list_wraps_one_item_per_line(self):
        src = ("let xs = [100000, 200000, 300000, 400000, 500000, "
               "600000, 700000, 800000, 900000, 1000000]\n")
        out = self.roundtrip(src)
        lines = out.splitlines()
        self.assertEqual(lines[0], "let xs = [")
        self.assertEqual(lines[-1], "]")
        self.assertIn("  100000,", out)
        self.assertIn("  1000000", out)
        for line in lines:
            self.assertLessEqual(len(line), 88, line)

    def test_long_map_wraps_entries(self):
        src = ("let m = {alpha: 100000, beta: 200000, gamma: 300000, "
               "delta: 400000, epsilon: 500000, zeta: 600000}\n")
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "let m = {")
        self.assertEqual(out.splitlines()[-1], "}")
        self.assertIn("  alpha: 100000,", out)
        self.assertIn("  zeta: 600000", out)

    def test_long_record_literal_wraps_fields(self):
        src = ("record Point { alpha: Int, beta: Int, gamma: Int, delta: Int, "
               "epsilon: Int, zeta: Int, eta: Int }\n"
               "let p: Point = Point { alpha: 100000, beta: 200000, "
               "gamma: 300000, delta: 400000, epsilon: 500000, "
               "zeta: 600000, eta: 700000 }\n"
               "print p.alpha\n")
        out = self.roundtrip(src)
        self.assertIn("let p: Point = Point {", out)
        self.assertIn("  alpha: 100000,", out)

    def test_nested_long_lists_wrap_inner_and_outer(self):
        src = ("let xs = [[100000, 200000, 300000, 400000, 500000], "
               "[600000, 700000, 800000, 900000, 1000000]]\n")
        out = self.roundtrip(src, width=40)
        self.assertIn("let xs = [", out)
        self.assertIn("    100000,", out)  # nested item was wrapped too

    def test_short_container_is_unchanged(self):
        src = "let xs = [1, 2, 3]\nlet m = {a: 1, b: 2}\nprint xs\nprint m\n"
        self.assertEqual(gama_fmt.format_source(src), src)


class LongExpressionTests(WrapBase):
    def test_long_call_wraps_argument_per_line(self):
        src = (
            "fn add(a: Int, b: Int, c: Int, d: Int, e: Int, f: Int, "
            "g: Int, h: Int) -> Int effects {} "
            "{ return a + b + c + d + e + f + g + h }\n"
            "let r = add(1000000000, 2000000000, 3000000000, "
            "4000000000, 5000000000, 6000000000, 7000000000, "
            "8000000000)\n"
            "print r\n"
        )
        out = self.roundtrip(src)
        self.assertIn("let r = add(", out)
        self.assertIn("  1000000000,", out)
        self.assertIn("  8000000000", out)
        self.assertIn(")", out)

    def test_long_binary_breaks_after_operator(self):
        src = ("let total = 100000 + 200000 + 300000 + 400000 + 500000 + "
               "600000 + 700000 + 800000 + 900000 + 1000000\n")
        out = self.roundtrip(src)
        lines = out.splitlines()
        self.assertEqual(lines[0], "let total = 100000 +")
        self.assertEqual(lines[1], "  200000 +")
        self.assertEqual(lines[-1], "  1000000")

    def test_long_binary_respects_precedence(self):
        src = ("let v = 100000 * 2 + 200000 * 2 + 300000 * 2 + "
               "400000 * 2 + 500000 * 2 + 600000 * 2 + 700000 * 2 + "
               "800000 * 2\n")
        out = self.roundtrip(src)
        self.assertIn("100000 * 2 +", out)
        self.assertIn("800000 * 2", out)

    def test_long_pipe_breaks_before_stage(self):
        src = ('let r = sql("select neto from customers where neto > 0") '
               '| filter neto > 100000 | select neto, konto | sum neto\n')
        out = self.roundtrip(src)
        self.assertIn("  | filter neto > 100000", out)
        self.assertIn("  | sum neto", out)

    def test_short_expressions_unchanged(self):
        src = "let x = 1 + 2 * 3\nprint x\n"
        self.assertEqual(gama_fmt.format_source(src), src)


class WidthOptionTests(unittest.TestCase):
    def test_width_controls_wrapping(self):
        src = ("let xs = [100000, 200000, 300000, 400000, 500000, "
               "600000, 700000, 800000]\n")
        wide = gama_fmt.format_source(src, width=200)
        narrow = gama_fmt.format_source(src, width=40)
        self.assertNotIn("\n  ", wide)
        self.assertIn("\n  ", narrow)
        self.assertEqual(wide, gama_fmt.format_source(wide, width=200))
        self.assertEqual(narrow, gama_fmt.format_source(narrow, width=40))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(narrow)))
        self.assertEqual(tokens(src), tokens(narrow))

    def test_width_floor_is_sane(self):
        src = "let xs = [1, 2, 3]\n"
        self.assertEqual(
            gama_fmt.format_source(src, width=0),
            gama_fmt.format_source(src, width=20))


class ExpressionCommentTests(WrapBase):
    def test_comments_stay_on_list_items(self):
        src = ("let xs = [\n"
               "  100000, # one\n"
               "  200000, # two\n"
               "  # before three\n"
               "  300000\n"
               "]\n"
               "print xs\n")
        out = self.roundtrip(src)
        self.assertIn("  100000, # one", out)
        self.assertIn("  200000, # two", out)
        self.assertIn("  # before three\n  300000", out)

    def test_comments_stay_on_call_arguments(self):
        src = ('let r = sql(\n'
               '  "select neto from customers where neto > 0", # query\n'
               '  # limit note\n'
               '  100000\n'
               ')\n'
               'print r\n')
        out = self.roundtrip(src)
        self.assertIn('# query', out)
        self.assertIn('  # limit note\n  100000', out)

    def test_comments_after_operators_stay_on_operator_line(self):
        src = ("let total = 100000 + # first\n"
               "  200000 +\n"
               "  # middle\n"
               "  300000 +\n"
               "  400000 + # fourth\n"
               "  500000 + 600000 + 700000 + 800000 + 900000\n")
        out = self.roundtrip(src)
        self.assertIn("let total = 100000 + # first", out)
        self.assertIn("  200000 +", out)
        self.assertIn("  # middle\n  300000 +", out)
        self.assertIn("  400000 + # fourth", out)

    def test_comments_force_expansion_of_short_list(self):
        src = "let xs = [1, # one\n  2]\nprint xs\n"
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "let xs = [")
        self.assertIn("  1, # one", out)
        self.assertIn("  2", out)

    def test_short_expression_comment_uses_safe_fallback(self):
        src = "let x = 1 # tail\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x = 1 # tail", out)

    def test_trailing_comment_on_wrapped_close_line_is_stable(self):
        src = 'print contains("abc", "b") # tail\nprint len("a b")\n'
        out = self.roundtrip(src, width=20)
        self.assertIn(") # tail", out)

    def test_trailing_comment_on_pipe_stage_is_stable(self):
        src = "print [1, 1, 5, 7] | count # tail\nprint 1\n"
        out = self.roundtrip(src, width=20)
        self.assertIn("| count # tail", out)

    def test_comment_not_dropped_or_duplicated(self):
        src = ("let m = {\n"
               "  alpha: 100000, # a\n"
               "  beta: 200000,\n"
               "  # before gamma\n"
               "  gamma: 300000\n"
               "}\n"
               "print m\n")
        out = self.roundtrip(src)
        self.assertEqual(out.count("# a"), 1)
        self.assertEqual(out.count("# before gamma"), 1)


class DeterminismTests(WrapBase):
    def test_wrapped_output_is_stable(self):
        src = ("let data = [100000, 200000, 300000, 400000, 500000, "
               "600000, 700000, 800000, 900000, 1000000]\n"
               "let total = 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + "
               "11 + 12 + 13 + 14 + 15 + 16 + 17 + 18 + 19 + 20 + "
               "21 + 22 + 23 + 24 + 25 + 26 + 27 + 28 + 29 + 30\n")
        first = gama_fmt.format_source(src)
        for _ in range(3):
            first = gama_fmt.format_source(first)
        self.assertEqual(first, gama_fmt.format_source(first))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(first)))


class FmtCliWidthTests(unittest.TestCase):
    """The CLI contract (--check/--write) is unchanged; --width is opt-in."""

    def run_cli(self, *args):
        v2 = pathlib.Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, str(v2 / "gamal2.py"), *args],
            cwd=str(v2), capture_output=True, text=True, timeout=30)

    def test_width_option_and_check_write(self):
        src = "let xs = [100000, 200000, 300000, 400000, 500000]\n"
        with tempfile.TemporaryDirectory() as td:
            path = (pathlib.Path(td) / "s.gm").resolve()
            path.write_text(src, encoding="utf-8")

            p = self.run_cli("fmt", "--width", "40", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            self.assertIn("\n  ", p.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), src)

            p = self.run_cli("fmt", "--write", "--width", "40", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            wrapped = path.read_text(encoding="utf-8")

            p = self.run_cli("fmt", "--check", "--width", "40", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)

            # The default width (88) leaves the short list on one line.
            p = self.run_cli("fmt", "--check", str(path))
            self.assertEqual(p.returncode, 1, p.stderr + p.stdout)
            self.assertIn("needs formatting", p.stdout)

            p = self.run_cli("fmt", "--write", str(path))
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), src)
            self.assertNotEqual(wrapped, src)


if __name__ == "__main__":
    unittest.main()
