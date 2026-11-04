"""AST formatter tests for GAMA-L 2.

Pins the new contract:

* ``parse(format(x))`` is AST-equivalent to ``parse(x)`` (line/column ignored).
* ``format(format(x)) == format(x)`` (idempotence).
* comments survive (text and order), strings keep their exact raw contents,
  multi-line strings are byte-for-byte, and optional semicolons plus the
  ``prove name { ... }`` spelling are preserved.
"""
import dataclasses
import pathlib
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt

V2 = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = V2 / "examples"


def shape(node):
    """Structural AST shape ignoring source positions and set ordering."""
    if dataclasses.is_dataclass(node):
        return (
            type(node).__name__,
            tuple(
                (f.name, shape(getattr(node, f.name)))
                for f in dataclasses.fields(node)
                if f.name not in ("line", "col")
            ),
        )
    if isinstance(node, (list, tuple)):
        return tuple(shape(x) for x in node)
    if isinstance(node, dict):
        return tuple((k, shape(v)) for k, v in node.items())
    if isinstance(node, set):
        return tuple(sorted(shape(x) for x in node))
    return node


def comments_in(src):
    """Comment texts in source order (string-aware; used only for assertions)."""
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
            out.append(src[i:j])
            i = j
            continue
        i += 1
    return out


class FormatterBase(unittest.TestCase):
    def assert_equivalent(self, src, check=True):
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out), "not idempotent")
        self.assertEqual(shape(G.parse(src)), shape(G.parse(out)),
                         "formatted AST differs from input AST")
        if check:
            G.check(out)
        return out

    def assert_comments_preserved(self, src, out):
        before = comments_in(src)
        after = comments_in(out)
        self.assertEqual(after, before, "comments changed or reordered")


class ExampleTests(FormatterBase):
    def test_examples_ast_equivalent_idempotent_and_valid(self):
        files = sorted(EXAMPLES.glob("*.gm"))
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            with self.subTest(example=path.name):
                src = path.read_text(encoding="utf-8")
                out = gama_fmt.format_source(src)
                self.assertEqual(out, gama_fmt.format_source(out))
                self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))
                if "import " in src:
                    with tempfile.TemporaryDirectory() as td:
                        for p in EXAMPLES.glob("*.gm"):
                            shutil.copy(p, td)
                        target = pathlib.Path(td) / path.name
                        target.write_text(out, encoding="utf-8")
                        G.check_file(str(target))
                else:
                    G.check(out)
                self.assert_comments_preserved(src, out)


class StatementTests(FormatterBase):
    def test_let_assign_and_operators(self):
        src = (
            "let x=1+2*3\n"
            "x := x-4\n"
            "let s: Text = \"a+b\"\n"
            "print x==7||s!=\"\"\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("let x = 1 + 2 * 3", out)
        self.assertIn("x := x - 4", out)
        self.assertIn('let s: Text = "a+b"', out)
        self.assertIn('x == 7 || s != ""', out)

    def test_fn_effects_and_return(self):
        src = (
            "fn add(a:Int,b:Int)->Int effects{io,net}{\n"
            "return a+b\n"
            "}\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("fn add(a: Int, b: Int) -> Int", out)
        self.assertIn("effects {io, net}", out)
        self.assertIn("return a + b", out)

    def test_if_else_if_else_same_line(self):
        src = (
            "if true {\nprint 1\n} else if false {\nprint 2\n} else {\nprint 3\n}\n"
        )
        out = self.assert_equivalent(src)
        self.assertEqual(
            out,
            "if true {\n"
            "  print 1\n"
            "} else if false {\n"
            "  print 2\n"
            "} else {\n"
            "  print 3\n"
            "}\n",
        )

    def test_else_on_its_own_line_is_preserved(self):
        src = "if true {\nprint 1\n}\nelse {\nprint 2\n}\n"
        out = self.assert_equivalent(src)
        self.assertIn("}\nelse {", out)

    def test_while_and_for(self):
        src = (
            "for i in [1,2,3] {\n"
            "if i == 2 { continue }\n"
            "print i\n"
            "}\n"
            "while false {\nbreak\n}\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("for i in [1, 2, 3] {", out)
        self.assertIn("continue", out)
        self.assertIn("while false {", out)

    def test_match_arms(self):
        src = (
            "let r = Ok(3)\n"
            "match r {\n"
            "Ok(x) { print x }\n"
            "Err(e) { print e }\n"
            "_ { print 0 }\n"
            "}\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("match r {", out)
        self.assertIn("Ok(x) {", out)
        self.assertIn("Err(e) {", out)
        self.assertIn("_ {", out)

    def test_prove_brace_form_preserved(self):
        src = 'prove doc.posted {\ninvoice: "MOCK"\n}\nverify doc.posted\n'
        out = self.assert_equivalent(src)
        self.assertNotIn("prove doc.posted =", out)
        self.assertIn("prove doc.posted {", out)

    def test_prove_equals_form_preserved(self):
        src = 'prove doc.posted = {invoice: "MOCK"}\nverify doc.posted\n'
        out = self.assert_equivalent(src)
        self.assertIn("prove doc.posted = {", out)

    def test_prove_newline_before_equals_or_brace(self):
        for src in (
            "prove p\n= 1 + 2\nverify p\n",
            "prove p\n{ a: 1 }\nverify p\n",
        ):
            with self.subTest(src=src):
                out = gama_fmt.format_source(src)
                self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))
                self.assertEqual(out, gama_fmt.format_source(out))

    def test_import_single_quote_raw_preserved(self):
        src = "import 'lib.gm' as l\nuse l\n"
        out = gama_fmt.format_source(src)
        self.assertEqual(out, "import 'lib.gm' as l\nuse l\n")
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_import_alias_and_use(self):
        src = 'import "lib.gm" as lib\nuse lib\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(out, 'import "lib.gm" as lib\nuse lib\n')
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_make_keyword_preserved(self):
        src = "make add(a: Int) -> Int effects {} { return a }\n"
        out = self.assert_equivalent(src)
        self.assertTrue(out.startswith("make add("))

    def test_semicolons_preserved(self):
        src = "let x=1; let y=2;\nif true { print x; print y }\n"
        out = self.assert_equivalent(src)
        self.assertIn("let x = 1;", out)
        self.assertIn("let y = 2;", out)
        self.assertIn("print x;", out)


class ExpressionTests(FormatterBase):
    def test_nested_lists_and_maps(self):
        src = (
            "let xs=[[1,2],[3]]\n"
            'let m={a:1,"b c":2,true:xs[0][1]}\n'
            "print m\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("let xs = [[1, 2], [3]]", out)
        self.assertIn('{a: 1, "b c": 2, true: xs[0][1]}', out)

    def test_lambda_inline(self):
        src = "let f = fn(a: Int, b: Int) -> Int { return a + b }\nprint f(1, 2)\n"
        out = self.assert_equivalent(src)
        self.assertIn("let f = fn(a: Int, b: Int) -> Int { return a + b }", out)

    def test_lambda_with_block_body(self):
        src = (
            "let f = fn(x: Int) -> Int {\n"
            "if x > 0 { return x }\n"
            "return -x\n"
            "}\n"
            "print f(-2)\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("fn(x: Int) -> Int {", out)
        self.assertIn("if x > 0 { return x }", out)

    def test_try_postfix_and_pipes(self):
        src = (
            "fn f() -> Result effects {} {\n"
            "let x = Ok(1)?\n"
            "return Ok(x)\n"
            "}\n"
            "let r = sql(\"select neto from customers\")\n"
            "print r | filter neto > 0 | sum neto\n"
            "print r | count\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("Ok(1)?", out)
        self.assertIn("r | filter neto > 0 | sum neto", out)
        self.assertIn("r | count", out)

    def test_scalar_literals(self):
        src = "print 1.50\nprint -0\nprint true && !false || null == null\n"
        out = self.assert_equivalent(src)
        self.assertIn("1.50", out)
        self.assertIn("true && !false || null == null", out)

    def test_precedence_associativity_and_postfix_bases(self):
        src = (
            "let a = 1 - 2 - 3\n"
            "let b = 1 - (2 - 3)\n"
            "let c = (1 + 2) * 3\n"
            "print (a | count) | count\n"
            "print (1 + 2)[0]\n"
            "let f = fn(x: Int) -> Int { return x }\n"
            "print f(1)(2)\n"
            "print -(1 + 2)\n"
            "print (-1)?\n"
        )
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))
        self.assertIn("let a = 1 - 2 - 3", out)
        self.assertIn("let b = 1 - (2 - 3)", out)
        self.assertIn("(1 + 2) * 3", out)
        self.assertIn("(a | count) | count", out)
        self.assertIn("(1 + 2)[0]", out)
        self.assertIn("f(1)(2)", out)
        self.assertIn("-(1 + 2)", out)
        self.assertIn("(-1)?", out)

    def test_parser_sugar_is_ast_equivalent(self):
        for src in (
            'remember demo = "vrijednost"\nprint recall demo\n',
            "let a = wait user\n",
        ):
            with self.subTest(src=src):
                out = gama_fmt.format_source(src)
                self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))
                self.assertEqual(out, gama_fmt.format_source(out))


class CommentTests(FormatterBase):
    def test_leading_trailing_and_inner_comments(self):
        src = (
            "# head\n"
            "let x = 1 # tail\n"
            "if true { # open\n"
            "# inside\n"
            "print x # print tail\n"
            "} # close\n"
            "# end\n"
        )
        out = self.assert_equivalent(src)
        self.assert_comments_preserved(src, out)
        self.assertIn("let x = 1 # tail", out)
        self.assertIn("if true { # open", out)
        self.assertIn("  # inside", out)
        self.assertIn("  print x # print tail", out)
        self.assertIn("} # close", out)

    def test_comment_braces_do_not_affect_structure(self):
        src = (
            "if true {\n"
            "# } fake close {\n"
            'print "x"\n'
            "# } another\n"
            "}\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("  # } fake close {", out)
        self.assertIn("  # } another", out)
        self.assertIn("  print \"x\"", out)

    def test_comments_inside_expression_are_kept_at_safe_position(self):
        src = (
            "let xs = [\n"
            "  1, # one\n"
            "  2 # two\n"
            "]\n"
            "print xs\n"
        )
        out = self.assert_equivalent(src)
        self.assert_comments_preserved(src, out)
        self.assertIn("# one", out)
        self.assertIn("# two", out)

    def test_comment_before_block_close(self):
        src = (
            "fn f(x: Int) -> Int {\n"
            "# before return\n"
            "return x\n"
            "# before close\n"
            "}\n"
        )
        out = self.assert_equivalent(src)
        self.assert_comments_preserved(src, out)
        self.assertIn("  # before close", out)

    def test_comment_between_then_and_else_keeps_order(self):
        src = (
            "if true {\n"
            "print 1\n"
            "}\n"
            "# between\n"
            "else {\n"
            "print 2\n"
            "# inside else\n"
            "}\n"
            "print 3\n"
        )
        out = self.assert_equivalent(src)
        self.assert_comments_preserved(src, out)
        self.assertIn("}\n# between\nelse {", out)
        self.assertIn("  # inside else", out)

    def test_match_arm_comment_keeps_order(self):
        src = (
            "match Ok(1) {\n"
            "# arm comment\n"
            "Ok(x) { print x }\n"
            "} # close match\n"
        )
        out = self.assert_equivalent(src)
        self.assert_comments_preserved(src, out)
        self.assertIn("  # arm comment", out)
        self.assertIn("} # close match", out)

    def test_comment_trailing_whitespace_removed(self):
        src = 'let x = 1   # tail   \n# own   \nprint x\n'
        out = self.assert_equivalent(src)
        for line in out.splitlines():
            self.assertEqual(line, line.rstrip(), "trailing whitespace kept")


class StringTests(FormatterBase):
    def test_multiline_string_raw_bytes(self):
        literal = '"first { ? # not comment\nsecond } inside # = + - ?"'
        src = (
            "fn f() -> Result effects {} {\n"
            "let s = " + literal + "\n"
            "return Ok(s)\n"
            "}\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn(literal, out)

    def test_single_quoted_and_escaped_strings(self):
        src = (
            "let a = 'x } y { z # != <= '\n"
            'let b = "he said \\"a { b\\" ok"\n'
            'let c = "tab\\tnewline\\nbackslash\\\\"\n'
        )
        out = self.assert_equivalent(src)
        self.assertIn("'x } y { z # != <= '", out)
        self.assertIn('"he said \\"a { b\\" ok"', out)
        self.assertIn('"tab\\tnewline\\nbackslash\\\\"', out)

    def test_string_with_trailing_comment(self):
        src = 'let s = "a # b" # real comment\nprint s\n'
        out = self.assert_equivalent(src)
        self.assertIn('"a # b" # real comment', out)

    def test_map_with_string_values_and_keys(self):
        src = 'let m = {"a b": "x\\ny", z: "q"}\nprint m\n'
        out = self.assert_equivalent(src)
        self.assertIn('{"a b": "x\\ny", z: "q"}', out)


class StructureTests(FormatterBase):
    def test_nested_blocks(self):
        src = (
            "fn f(x: Int) -> Int {\n"
            "if x > 0 {\n"
            "for i in [1,2] {\n"
            "while false { break }\n"
            "}\n"
            "}\n"
            "return x\n"
            "}\n"
        )
        out = self.assert_equivalent(src)
        self.assertIn("    for i in [1, 2] {", out)
        self.assertIn("      while false {", out)

    def test_blank_lines_collapsed_to_one(self):
        src = "let x = 1\n\n\n\nlet y = 2\n"
        out = self.assert_equivalent(src)
        self.assertEqual(out, "let x = 1\n\nlet y = 2\n")

    def test_crlf_and_trailing_whitespace(self):
        src = "let x = 1   \r\n\r\nprint x\t\r\n"
        out = self.assert_equivalent(src)
        self.assertEqual(out, "let x = 1\n\nprint x\n")

    def test_empty_inputs(self):
        for src in ("", "\n", "\n\n", "   \n\t\n"):
            with self.subTest(src=repr(src)):
                out = gama_fmt.format_source(src)
                self.assertEqual(out, "\n")
                self.assertEqual(out, gama_fmt.format_source(out))

    def test_comment_only_source_keeps_comments(self):
        src = "# only a comment\n# second\n"
        out = gama_fmt.format_source(src)
        self.assertEqual(out, "# only a comment\n# second\n")
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_comment_only_source_with_blank_line(self):
        src = "# first\n\n# second\n"
        out = gama_fmt.format_source(src)
        self.assertEqual(out, "# first\n\n# second\n")
        self.assertEqual(out, gama_fmt.format_source(out))


class RandomishFixtureTests(FormatterBase):
    """Deterministic composed programs (not fuzzing the parser itself)."""

    def _program(self, r):
        parts = ["# generated fixture"]
        n = r.randint(3, 8)
        for i in range(n):
            kind = r.choice(
                ["let", "if", "for", "listmap", "fn", "comment", "while"]
            )
            if kind == "let":
                parts.append(f"let v{i} = {r.randint(0, 9)} + {i}")
            elif kind == "listmap":
                parts.append(
                    f'let l{i} = [{i}, {i + 1}, {i + 2}]  # list {i}'
                )
                parts.append(f"let m{i} = {{k{i}: l{i}[0], r: {i}}}")
            elif kind == "comment":
                parts.append(f"# note {i} {{ }} #")
            elif kind == "if":
                parts.append(
                    f"if true {{ # cond {i}\n"
                    f"  print {i}\n"
                    f"}} else {{\n"
                    f"  print -{i}\n"
                    f"}}"
                )
            elif kind == "for":
                parts.append(
                    f"for j{i} in [1, 2] {{\n"
                    f"  print j{i}\n"
                    f"}}"
                )
            elif kind == "while":
                parts.append("while false {\n  break\n}")
            else:
                parts.append(
                    f"fn f{i}(a: Int) -> Int effects {{}} {{\n"
                    f"  return a + {i}\n"
                    f"}}"
                )
        return "\n".join(parts) + "\n"

    def test_deterministic_composed_fixtures(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                src = self._program(random.Random(seed))
                out = self.assert_equivalent(src)
                self.assert_comments_preserved(src, out)


class FallbackTests(unittest.TestCase):
    def test_invalid_source_does_not_crash(self):
        # The CLI parses before formatting; direct calls fall back to the old
        # token formatter instead of raising.
        broken = 'let x = "unterminated\n'
        out = gama_fmt.format_source(broken)
        self.assertIsInstance(out, str)
        self.assertIn('let x = "unterminated', out)


if __name__ == "__main__":
    unittest.main()
