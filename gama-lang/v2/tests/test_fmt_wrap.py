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


class LongHeaderTests(WrapBase):
    def test_long_fn_header_wraps_parameters(self):
        src = (
            "fn calculate_total_of_all_things(alpha_one: Int, "
            "beta_two: Int, gamma_three: Int, delta_four: Int, "
            "epsilon_five: Int) -> Int effects {io, net} {\n"
            "return alpha_one\n"
            "}\n"
            "print 1\n"
        )
        out = self.roundtrip(src)
        lines = out.splitlines()
        self.assertEqual(lines[0], "fn calculate_total_of_all_things(")
        self.assertIn("  alpha_one: Int,", out)
        self.assertIn("  epsilon_five: Int", out)
        self.assertIn(") -> Int effects {io, net} {", out)
        for line in lines:
            self.assertLessEqual(len(line), 88, line)

    def test_long_fn_default_wraps_inside_parameter(self):
        src = (
            "fn long_function_name("
            "alpha: Int = 100000 + 200000 + 300000 + 400000 + 500000 + "
            "600000 + 700000, beta: Int = 1) -> Int { return alpha }\n"
            "print long_function_name()\n"
        )
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "fn long_function_name(")
        self.assertIn("  alpha: Int = 100000 +", out)
        self.assertIn("  beta: Int = 1", out)
        self.assertIn(") -> Int { return alpha }", out)

    def test_long_record_and_enum_declarations_wrap(self):
        src = (
            "record VeryLongRecordName { alpha_field: Int, beta_field: Text, "
            "gamma_field: Decimal, delta_field: Bool, epsilon_field: Int }\n"
            "enum VeryLongEnumerationName { AlphaVariant, BetaVariant, "
            "GammaVariant, DeltaVariant, EpsilonVariant, ZetaVariant }\n"
            "print 1\n"
        )
        out = self.roundtrip(src)
        self.assertIn("record VeryLongRecordName {", out)
        self.assertIn("  alpha_field: Int,", out)
        self.assertIn("  epsilon_field: Int\n}", out)
        self.assertIn("enum VeryLongEnumerationName {", out)
        self.assertIn("  AlphaVariant,", out)
        self.assertIn("  ZetaVariant\n}", out)

    def test_short_declarations_stay_on_one_line(self):
        src = (
            "record Point { x: Int, y: Int }\n"
            "enum Color { Red, Green }\n"
            "fn add(a: Int, b: Int) -> Int { return a }\n"
            "print add(1, 2)\n"
        )
        self.assertEqual(gama_fmt.format_source(src), src)

    def test_long_control_flow_headers_wrap(self):
        src = (
            "let x = 1\n"
            "if x == 1 && x == 2 && x == 3 && x == 4 && x == 5 && "
            "x == 6 && x == 7 && x == 8 && x == 9 && x == 10 && "
            "x == 11 {\n"
            "print x\n"
            "}\n"
            "while 100000 + 200000 + 300000 + 400000 + 500000 + "
            "600000 + 700000 + 800000 + 900000 + 1000000 > 0 {\n"
            "break\n"
            "}\n"
            "for i in [100000, 200000, 300000, 400000, 500000, "
            "600000, 700000, 800000, 900000, 1000000] {\n"
            "print i\n"
            "}\n"
        )
        out = self.roundtrip(src)
        self.assertIn("if x == 1 &&\n  x == 2 &&", out)
        self.assertIn("while 100000 +\n  200000 +", out)
        self.assertIn("for i in [\n  100000,", out)

    def test_long_match_subject_wraps(self):
        src = (
            "match Ok(100000 + 200000 + 300000 + 400000 + 500000 + "
            "600000 + 700000 + 800000 + 900000) {\n"
            "Ok(x) { print x }\n"
            "_ { print 0 }\n"
            "}\n"
        )
        out = self.roundtrip(src)
        self.assertIn("match Ok(\n  100000 +", out)
        self.assertIn(") {", out)

    def test_long_prove_wraps_expression(self):
        src = (
            "prove long_name = {\n"
            "alpha: 100000, beta: 200000, gamma: 300000, delta: 400000, "
            "epsilon: 500000, zeta: 600000, eta: 700000\n"
            "}\n"
            "verify long_name\n"
        )
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "prove long_name = {")
        self.assertIn("  alpha: 100000,", out)
        self.assertIn("  eta: 700000\n}\nverify long_name", out)

        brace = (
            "prove long_name {\n"
            "alpha: 100000, beta: 200000, gamma: 300000, delta: 400000, "
            "epsilon: 500000, zeta: 600000, eta: 700000\n"
            "}\n"
            "verify long_name\n"
        )
        out = self.roundtrip(brace)
        self.assertEqual(out.splitlines()[0], "prove long_name {")
        self.assertIn("  eta: 700000\n}\nverify long_name", out)

    def test_width_option_applies_to_headers(self):
        src = (
            "fn add(a: Int, b: Int, c: Int, d: Int, e: Int) -> Int "
            "{ return a }\n"
            "print add(1, 2, 3, 4, 5)\n"
        )
        wide = gama_fmt.format_source(src, width=200)
        narrow = gama_fmt.format_source(src, width=40)
        self.assertNotIn("\n  ", wide)
        self.assertIn("add(\n  a: Int,", narrow)
        self.assertEqual(wide, gama_fmt.format_source(wide, width=200))
        self.assertEqual(narrow, gama_fmt.format_source(narrow, width=40))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(narrow)))
        self.assertEqual(tokens(src), tokens(narrow))


class PipeStageCommentTests(WrapBase):
    def test_comments_between_stages_stay_with_their_stage(self):
        src = (
            'let r = sql("select neto from customers where neto > 0")\n'
            "# before filter\n"
            "| filter neto > 100000 # after filter\n"
            "| select neto, konto\n"
            "| sum neto\n"
            "print r\n"
        )
        out = self.roundtrip(src)
        self.assertIn(
            "  # before filter\n  | filter neto > 100000 # after filter", out)
        self.assertIn("  | select neto, konto", out)
        self.assertIn("  | sum neto", out)

    def test_comment_after_pipe_bar_is_kept(self):
        src = "let r = [1, 2, 3] | # bar note\n count\nprint r\n"
        out = self.roundtrip(src)
        self.assertEqual(out.count("# bar note"), 1)
        # The comment stays between ``|`` and the stage keyword, so the bar
        # line keeps the comment and the stage moves to the next line.
        self.assertIn("  | # bar note\n  count", out)

    def test_comment_inside_filter_stage_stays_on_operator(self):
        src = (
            'let r = sql("select neto from customers where neto > 0") '
            "| filter neto + # sum note\n 0 > 0 | count\n"
            "print r\n"
        )
        out = self.roundtrip(src)
        self.assertIn("| filter neto + # sum note", out)
        self.assertIn("  | count", out)

    def test_comment_inside_count_pipe_stays_on_statement(self):
        src = "let r = [1, 2, 3] | count # tail\nprint r\n"
        out = self.roundtrip(src)
        self.assertIn("[1, 2, 3] | count # tail", out)

    def test_nested_pipe_stage_comments_stay_in_place(self):
        src = (
            'let r = (sql("select neto from customers where neto > 0")\n'
            "# inner stage\n"
            "| filter neto > 0) | # outer stage\n"
            " count\n"
            "print r\n"
        )
        out = self.roundtrip(src, width=40)
        self.assertIn("  # inner stage", out)
        self.assertIn("| filter neto > 0)", out)
        # The outer comment sits after ``|`` and before ``count``; it stays
        # on the bar line and the stage moves down.
        self.assertIn("  | # outer stage\n  count", out)


class PipeBarCommentTests(WrapBase):
    """Comments between ``|`` and the stage keyword keep the bar line."""

    def test_short_pipe_bar_comment_moves_stage_to_next_line(self):
        src = "let r = [1, 2, 3] | # bar\n count\nprint r\n"
        out = self.roundtrip(src)
        self.assertIn("let r = [1, 2, 3]\n  | # bar\n  count", out)

    def test_wrapped_pipe_bar_comment_keeps_attachment(self):
        src = ('let r = sql("select neto from customers where neto > 0") '
               "| # bar\n filter neto > 100000 | select neto, konto "
               "| sum neto\nprint r\n")
        out = self.roundtrip(src)
        self.assertIn("  | # bar\n  filter neto > 100000", out)
        self.assertIn("  | select neto, konto", out)
        self.assertIn("  | sum neto", out)

    def test_own_line_comment_between_bar_and_stage_stays_own_line(self):
        src = "let r = [1, 2, 3] |\n # before count\n count\nprint r\n"
        out = self.roundtrip(src)
        self.assertIn("  |\n  # before count\n  count", out)

    def test_multiple_stage_bar_comments(self):
        src = ('let r = sql("select neto from customers where neto > 0")'
               " | # one\n filter neto > 1 | # two\n count\nprint r\n")
        out = self.roundtrip(src)
        self.assertIn("  | # one\n  filter neto > 1", out)
        self.assertIn("  | # two\n  count", out)

    def test_nested_pipe_inner_and_outer_bar_comments(self):
        src = ('let r = (sql("select neto from customers where neto > 0")\n'
               "| # inner bar\nfilter neto > 0) | # outer bar\ncount\n"
               "print r\n")
        out = self.roundtrip(src, width=40)
        self.assertIn("  | # inner bar\n  filter neto > 0", out)
        self.assertIn("  | # outer bar\n  count", out)


class LetEqualsCommentTests(WrapBase):
    """Comments between ``let x =`` and the expression stay there."""

    def test_own_line_comment_after_equals(self):
        src = "let x =\n # before value\n 1\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x =\n  # before value\n  1", out)

    def test_inline_comment_on_equals_line(self):
        src = "let x = # tail\n 1\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x = # tail\n  1", out)

    def test_multiple_comments_after_equals(self):
        src = "let x =\n # one\n # two\n 1 + 2\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x =\n  # one\n  # two\n  1 + 2", out)

    def test_long_expression_after_equals_comment_wraps(self):
        src = ("let x =\n # before long\n 100000 + 200000 + 300000 + "
               "400000 + 500000 + 600000 + 700000 + 800000 + 900000\n"
               "print x\n")
        out = self.roundtrip(src, width=40)
        self.assertIn("let x =\n  # before long\n  100000 +\n    200000 +", out)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 40, line)

    def test_comment_after_equals_and_trailing_comment(self):
        src = "let x =\n # before\n 1 + 2 # after\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x =\n  # before\n  1 + 2 # after", out)

    def test_comment_after_equals_with_interior_container_comment(self):
        src = "let x =\n # before list\n [1, # one\n 2]\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x =\n  # before list\n  [", out)
        self.assertIn("1, # one", out)


class EffectsCommentTests(WrapBase):
    """Comments inside a ``fn`` effects set expand the set."""

    def test_own_line_and_trailing_effect_comments(self):
        src = ("fn f() -> Int effects {\n # eff note\n io,\n"
               " net # net tail\n} { return 1 }\nprint f()\n")
        out = self.roundtrip(src)
        self.assertIn("effects {\n  # eff note\n  io,\n  net # net tail\n}",
                      out)

    def test_inline_comment_on_effects_open_line(self):
        src = ("fn f() -> Int effects { # header note\n io, net\n}"
               " { return 1 }\nprint f()\n")
        out = self.roundtrip(src)
        self.assertIn("effects { # header note\n  io,\n  net\n}", out)

    def test_unsorted_effects_keep_source_order_with_comments(self):
        src = ("fn f() -> Int effects {\n # note one\n net,\n"
               " io # note two\n} { return 1 }\nprint f()\n")
        out = self.roundtrip(src)
        self.assertIn("effects {\n  # note one\n  net,\n  io # note two\n}",
                      out)

    def test_empty_effects_with_comment_expands(self):
        src = "fn f() -> Int effects {\n # only\n} { return 1 }\nprint f()\n"
        out = self.roundtrip(src)
        self.assertIn("effects {\n  # only\n}", out)

    def test_long_fn_header_and_effects_comments(self):
        src = ("fn long_function_name(a: Int, b: Int, c: Int, d: Int, "
               "e: Int, f: Int) -> Int effects {\n # eff note\n"
               " io, net, fs\n} { return a }\n"
               "print long_function_name(1, 2, 3, 4, 5, 6)\n")
        out = self.roundtrip(src)
        self.assertIn(") -> Int effects {\n  # eff note\n  io,\n  net,\n  fs\n}",
                      out)

    def test_multiple_effect_comments(self):
        src = ("fn f() -> Int effects {\n # a\n io,\n # b\n net,\n"
               " # c\n fs\n} { return 1 }\nprint f()\n")
        out = self.roundtrip(src)
        self.assertIn("  # a\n  io,\n  # b\n  net,\n  # c\n  fs", out)


class LambdaBodyCommentTests(WrapBase):
    """Comments inside an inline lambda body expand the lambda."""

    def test_lambda_body_header_and_trailing_comments(self):
        src = ("let f = fn(a: Int) -> Int { # body note\n"
               " return a # ret tail\n }\nprint f(1)\n")
        out = self.roundtrip(src)
        self.assertIn("fn(a: Int) -> Int { # body note\n", out)
        self.assertIn("  return a # ret tail\n}", out)

    def test_empty_lambda_body_with_comment(self):
        src = "let f = fn() -> Int { # only\n }\nprint 1\n"
        out = self.roundtrip(src)
        self.assertIn("fn() -> Int { # only\n}", out)

    def test_lambda_inside_container_expands(self):
        src = "let fs = [fn(a: Int) -> Int { # c\n return a\n }]\nprint 1\n"
        out = self.roundtrip(src)
        self.assertIn("[\n  fn(a: Int) -> Int { # c\n    return a\n  }\n]",
                      out)

    def test_lambda_effects_and_body_comments(self):
        src = ("let f = fn(x: Int) -> Int effects { # eff\n io\n}"
               " { # body\n return x\n}\nprint f(1)\n")
        out = self.roundtrip(src)
        self.assertIn("effects { # eff\n  io\n} { # body\n  return x\n}", out)

    def test_comment_inside_lambda_body_statement_keeps_attachment(self):
        src = ("let f = fn(x: Int) -> Int {\n"
               " let y = [1, 2, 3] | # inner\n count\n return y\n}\n"
               "print f(1)\n")
        out = self.roundtrip(src)
        self.assertIn("  let y = [1, 2, 3]\n    | # inner\n    count", out)
        self.assertIn("  return y", out)


class InlineLambdaWidthTests(WrapBase):
    """Inline lambdas without comments are width-wrapped like any expression.

    The header wraps its parameter list and effects set at legal newline
    points; the body becomes a one-statement-per-line block.  Wrapping adds no
    tokens (commas between parameters already exist) and never reloads the
    comments of a *commented* lambda onto a different attachment point.
    """

    def test_long_inline_lambda_body_wraps(self):
        src = ("let f = fn(x: Int) -> Int { return x + 100000000 + "
               "200000000 + 300000000 + 400000000 + 500000000 }\n"
               "print f(1)\n")
        out = self.roundtrip(src)
        lines = out.splitlines()
        self.assertEqual(lines[0], "let f = fn(x: Int) -> Int {")
        self.assertIn("  return x + 100000000 +", out)
        self.assertEqual(lines[-2], "}")
        for line in lines:
            self.assertLessEqual(len(line), 88, line)

    def test_long_inline_lambda_parameters_wrap(self):
        src = ("let f = fn(alpha_one: Int, beta_two: Int, gamma_three: Int, "
               "delta_four: Int, epsilon_five: Int) -> Int effects {io, net} "
               "{ return alpha_one }\n"
               "print f(1, 2, 3, 4, 5)\n")
        out = self.roundtrip(src)
        lines = out.splitlines()
        self.assertEqual(lines[0], "let f = fn(")
        self.assertIn("  alpha_one: Int,", out)
        self.assertIn("  epsilon_five: Int", out)
        self.assertIn(") -> Int effects {io, net} {", out)
        self.assertEqual(lines[-2], "}")

    def test_long_inline_lambda_effects_wrap(self):
        src = ("let f = fn(a: Int) -> Int effects {alpha_effect, beta_effect, "
               "delta_effect, epsilon_effect, gamma_effect} { return a }\n"
               "print f(1)\n")
        out = self.roundtrip(src)
        self.assertIn("effects {", out)
        self.assertIn("  alpha_effect,", out)
        self.assertIn("  gamma_effect\n} {", out)

    def test_inline_lambda_inside_list_wraps(self):
        src = ("let fs = [fn(x: Int) -> Int { return x + 100000000 + "
               "200000000 + 300000000 + 400000000 + 500000000 }, "
               "fn(y: Int) -> Int { return y }]\n"
               "print 1\n")
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "let fs = [")
        self.assertIn("  fn(x: Int) -> Int {", out)
        self.assertIn("    return x +", out)
        self.assertIn("  },", out)
        self.assertIn("  fn(y: Int) -> Int { return y }", out)
        self.assertEqual(out.splitlines()[-2], "]")

    def test_inline_lambda_inside_call_wraps(self):
        src = ("fn apply(f: Function, x: Int) -> Int { return f(x) }\n"
               "print apply(fn(x: Int) -> Int { return x + 100000000 + "
               "200000000 + 300000000 + 400000000 + 500000000 }, 21)\n")
        out = self.roundtrip(src)
        self.assertIn("print apply(", out)
        self.assertIn("  fn(x: Int) -> Int {", out)
        self.assertIn("    return x +", out)
        self.assertIn("  },", out)
        self.assertIn("\n  21\n)", out)

    def test_inline_lambda_inside_map_wraps(self):
        src = ("let m = {handler: fn(x: Int) -> Int { return x + 100000000 + "
               "200000000 + 300000000 + 400000000 + 500000000 }}\n"
               "print m\n")
        out = self.roundtrip(src)
        self.assertIn("let m = {", out)
        self.assertIn("  handler: fn(x: Int) -> Int {", out)
        self.assertIn("  }\n}", out)

    def test_nested_inline_lambdas_wrap(self):
        src = ("let f = fn(x: Int) -> Int {\n"
               "let g = fn(y: Int) -> Int { return y + 100000000 + "
               "200000000 + 300000000 + 400000000 + 500000000 }\n"
               "return g(x)\n"
               "}\n"
               "print f(1)\n")
        out = self.roundtrip(src)
        self.assertIn("let f = fn(x: Int) -> Int {", out)
        self.assertIn("  let g = fn(y: Int) -> Int {", out)
        self.assertIn("    return y +", out)

    def test_lambda_parameter_comment_stays_on_parameter(self):
        src = ("let f = fn(\n"
               " alpha: Int, # p\n"
               " beta: Int\n"
               ") -> Int { return alpha + beta + 100000000 + 200000000 + "
               "300000000 + 400000000 }\n"
               "print f(1, 2)\n")
        out = self.roundtrip(src)
        self.assertIn("let f = fn(", out)
        self.assertIn("  alpha: Int, # p", out)
        self.assertIn("  beta: Int", out)
        self.assertIn(") -> Int {", out)

    def test_lambda_own_line_parameter_comment_stays_own_line(self):
        src = ("let f = fn(\n"
               " # before alpha\n"
               " alpha: Int,\n"
               " beta: Int\n"
               ") -> Int { return alpha + beta + 100000000 + 200000000 + "
               "300000000 + 400000000 }\n"
               "print f(1, 2)\n")
        out = self.roundtrip(src)
        self.assertIn("  # before alpha\n  alpha: Int,", out)

    def test_inline_lambda_width_is_configurable(self):
        src = ("let f = fn(x: Int) -> Int { return x + 100000000 + "
               "200000000 + 300000000 + 400000000 + 500000000 }\n"
               "print f(1)\n")
        wide = gama_fmt.format_source(src, width=200)
        narrow = gama_fmt.format_source(src, width=40)
        self.assertNotIn("\n  ", wide)
        self.assertIn("fn(x: Int) -> Int {\n", narrow)
        self.assertEqual(wide, gama_fmt.format_source(wide, width=200))
        self.assertEqual(narrow, gama_fmt.format_source(narrow, width=40))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(narrow)))
        self.assertEqual(tokens(src), tokens(narrow))


class BeforeBinaryOperatorCommentTests(unittest.TestCase):
    """A comment before a binary operator has no legal source position.

    The grammar only skips newlines *after* an operator, so a comment cannot
    be written between an operand and its operator in valid source.  The
    renderer still has a deterministic rule (emit it immediately after the
    operator, never before it) for robustness; the tests document both facts.
    """

    def test_source_before_operator_is_not_valid(self):
        with self.assertRaises(gama_fmt._G.GamaError):
            gama_fmt._G.parse("let x = 1\n # c\n + 2\nprint x\n")

    def _operator_rule(self, own_line):
        f = gama_fmt._AstFormatter("let x = 1 + 2\n")
        node = dataclasses.replace(f.ast[0].expr, line=2, col=1)
        f.comments = [gama_fmt._Comment(1, 11, "# c", own_line)]
        f.used = set()
        return f._wrap_binary(node, 0, 0, 0)

    def test_operator_rule_places_comment_after_operator(self):
        self.assertEqual(self._operator_rule(True), "1 +\n  # c\n  2")
        self.assertEqual(self._operator_rule(False), "1 +\n  # c\n  2")

    def test_operator_rule_places_comment_after_later_operator(self):
        f = gama_fmt._AstFormatter("let x = 1 + 2 + 3\n")
        node = dataclasses.replace(f.ast[0].expr, line=2, col=1)
        expected = "1 +\n  2 +\n  # c\n  3"
        for own_line in (True, False):
            f.comments = [gama_fmt._Comment(1, 15, "# c", own_line)]
            f.used = set()
            self.assertEqual(f._wrap_binary(node, 0, 0, 0), expected)


class ShortExpressionCommentTests(WrapBase):
    def test_short_record_with_inner_comment_expands(self):
        src = (
            "record P {\n"
            "  # field note\n"
            "  x: Int, # x tail\n"
            "  y: Int\n"
            "}\n"
            "print 1\n"
        )
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "record P {")
        self.assertIn("  # field note", out)
        self.assertIn("  x: Int, # x tail", out)

    def test_short_enum_with_inner_comment_expands(self):
        src = (
            "enum C {\n"
            "  # variant note\n"
            "  Red, # red tail\n"
            "  Green\n"
            "}\n"
            "print 1\n"
        )
        out = self.roundtrip(src)
        self.assertEqual(out.splitlines()[0], "enum C {")
        self.assertIn("  # variant note", out)
        self.assertIn("  Red, # red tail", out)

    def test_short_if_condition_list_comment_stays_on_item(self):
        src = (
            "if [1, # one\n 2] == [1, 2] {\n"
            "print 1\n"
            "}\n"
        )
        out = self.roundtrip(src)
        self.assertIn("  1, # one", out)
        self.assertIn("  2", out)

    def test_short_binary_own_line_comment_uses_operator_position(self):
        src = "let x = 1 +\n # between\n 2\nprint x\n"
        out = self.roundtrip(src)
        self.assertIn("let x = 1 +", out)
        self.assertIn("  # between", out)

    def test_comment_before_short_expression_is_not_dropped(self):
        src = "let x =\n # before value\n 1\nprint x\n"
        out = self.roundtrip(src)
        self.assertEqual(out.count("# before value"), 1)


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
