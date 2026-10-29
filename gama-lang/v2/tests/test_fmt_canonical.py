import sys, unittest, pathlib, shutil, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"


def _semantic_tokens(src: str):
    """Token stream ignoring whitespace/newlines (the lexer already does)."""
    return [(t.kind, t.val) for t in G.lex(src) if t.kind != "NL"]


class CanonicalFormatterTests(unittest.TestCase):
    def test_examples_idempotent(self):
        files = sorted(EXAMPLES.glob("*.gm"))
        self.assertTrue(files, "no examples found")
        for path in files:
            with self.subTest(example=path.name):
                src = path.read_text(encoding="utf-8")
                out = gama_fmt.format_source(src)
                self.assertEqual(out, gama_fmt.format_source(out), "not idempotent")

    def test_examples_still_check_and_keep_tokens(self):
        for path in sorted(EXAMPLES.glob("*.gm")):
            with self.subTest(example=path.name):
                src = path.read_text(encoding="utf-8")
                out = gama_fmt.format_source(src)
                self.assertEqual(_semantic_tokens(src), _semantic_tokens(out))
                if "import " in src:
                    # imports resolve relative paths, so validate in a real copy
                    with tempfile.TemporaryDirectory() as td:
                        for p in EXAMPLES.glob("*.gm"):
                            shutil.copy(p, td)
                        (pathlib.Path(td) / path.name).write_text(out, encoding="utf-8")
                        G.check_file(str(pathlib.Path(td) / path.name))
                    G.check_file(str(path))  # baseline stays valid
                else:
                    G.check(src)  # baseline must stay valid
                    G.check(out)  # formatted source is still valid

    def test_strings_with_specials_untouched(self):
        src = (
            'let a = "a { b } # = 5 + 3 - 2"\n'
            "let b = 'x } y { z # != <= '\n"
            'print "1+2*3=7"\n'
        )
        out = gama_fmt.format_source(src)
        self.assertIn('"a { b } # = 5 + 3 - 2"', out)
        self.assertIn("'x } y { z # != <= '", out)
        self.assertIn('"1+2*3=7"', out)
        G.check(out)
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_escaped_quote_inside_string(self):
        src = 'print "he said \\"a { b\\" ok"\n'
        out = gama_fmt.format_source(src)
        self.assertIn('"he said \\"a { b\\" ok"', out)
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_comment_with_brace_does_not_break_indent(self):
        src = (
            "if true {\n"
            'print "a"\n'
            "# } this brace is only a comment {\n"
            'print "b"\n'
            "}\n"
        )
        out = gama_fmt.format_source(src)
        lines = out.splitlines()
        self.assertEqual(lines[1], '  print "a"')
        self.assertEqual(lines[2], "  # } this brace is only a comment {")
        self.assertEqual(lines[3], '  print "b"')
        self.assertEqual(lines[4], "}")
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_else_on_same_line(self):
        src = 'if x > 3 {\nprint "big"\n} else {\nprint "small"\n}\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(
            out,
            'if x > 3 {\n  print "big"\n} else {\n  print "small"\n}\n',
        )
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_else_on_its_own_line(self):
        src = 'if x {\nprint 1\n}\nelse {\nprint 2\n}\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(out, "if x {\n  print 1\n}\nelse {\n  print 2\n}\n")
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_single_block(self):
        src = "if x {\nprint 1\n}\n"
        self.assertEqual(gama_fmt.format_source(src), "if x {\n  print 1\n}\n")

    def test_nested_blocks(self):
        src = "if a {\nif b {\nprint 1\n}\nprint 2\n}\n"
        self.assertEqual(
            gama_fmt.format_source(src),
            "if a {\n  if b {\n    print 1\n  }\n  print 2\n}\n",
        )

    def test_crlf_trailing_ws_and_blank_collapse(self):
        src = "let x = 1   \r\n\r\n\r\n\r\nprint x\t\r\n\r\n"
        out = gama_fmt.format_source(src)
        self.assertEqual(out, "let x = 1\n\nprint x\n")

    def test_operator_spacing_outside_strings(self):
        src = 'let x=1+2*3\nlet y = "1+2"\nprint x==7&&y!="z"\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(
            out,
            'let x = 1 + 2 * 3\nlet y = "1+2"\nprint x == 7 && y != "z"\n',
        )
        G.check(out)

    def test_arrow_and_assignment_and_comparisons(self):
        src = "fn f(a:Int)->Bool effects{} {\nreturn a<=1||a>=2\n}\nlet b = 1\nb := b+1\n"
        out = gama_fmt.format_source(src)
        self.assertIn("-> Bool", out)
        self.assertIn("a <= 1 || a >= 2", out)
        self.assertIn("b := b + 1", out)
        G.check(out)

    def test_unary_minus_preserved(self):
        src = "let x = -5\nprint -5 + 2\nprint x - -1\n"
        out = gama_fmt.format_source(src)
        self.assertIn("let x = -5", out)
        self.assertIn("print -5 + 2", out)
        self.assertIn("print x - -1", out)
        it = G.run(out)
        self.assertEqual(it.out, ["-3", "-4"])

    def test_no_extra_or_missing_tokens(self):
        src = 'let a=1\nif a>0{\nprint "yes"\n}else{\nprint "no"\n}\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(_semantic_tokens(src), _semantic_tokens(out))
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_multiline_string_left_verbatim(self):
        src = 'let s = "line1 {\nstill } inside # = string"\nprint s\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(out, src)  # untouched, cannot reformat safely
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_blank_source(self):
        self.assertEqual(gama_fmt.format_source(""), "\n")
        self.assertEqual(gama_fmt.format_source("\n\n"), "\n")

    def test_empty_input_idempotent(self):
        for src in ("", "\n", "\r\n\r\n", "   \n\t\n"):
            with self.subTest(src=src):
                out = gama_fmt.format_source(src)
                self.assertEqual(out, gama_fmt.format_source(out))


if __name__ == "__main__":
    unittest.main()
