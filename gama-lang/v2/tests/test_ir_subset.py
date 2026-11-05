"""IR-vs-interpreter comparison tests for the expanded IR subset.

Every successful program is run by the reference interpreter and by the IR;
printed output must be byte-for-byte identical.  Runtime failures are compared
by full message (including line/col), and unsupported constructs must still be
rejected loudly with ``IRUnsupported``.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class IRSubsetMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    # -- list / map literals and indexing --------------------------------
    def test_list_literal_and_print(self):
        self.assert_same('let xs = [1, 2, 3]\nprint xs\n')

    def test_list_index_positive_and_negative(self):
        self.assert_same('let xs = [10, 20, 30]\nprint xs[0]\nprint xs[1]\nprint xs[-1]\nprint xs[-3]\n')

    def test_text_index_positive_and_negative(self):
        self.assert_same('let s = "abc"\nprint s[0]\nprint s[2]\nprint s[-1]\n')

    def test_map_literal_and_print(self):
        self.assert_same('let m = {a: 1, b: 2}\nprint m\n')

    def test_nested_list_and_map_literal(self):
        self.assert_same('let m = {a: [1, 2], b: {c: "x"}}\nprint m\n')

    def test_nested_index(self):
        self.assert_same('let xs = [[1, 2], [3, 4]]\nprint xs[1][0]\nprint xs[-1][-1]\n')

    def test_empty_list_and_map(self):
        self.assert_same('let xs = []\nlet m = {}\nprint xs\nprint m\nprint len(xs)\nprint len(m)\n')

    def test_list_and_map_decimal_rendering(self):
        # Rendering goes through json.dumps(default=str) in both runtimes.
        self.assert_same('print [1.50, 2]\nprint {a: 1.50}\n')

    # -- for loops -------------------------------------------------------
    def test_for_over_list_sum(self):
        self.assert_same('let xs = [1, 2, 3, 4]\nlet total = 0\nfor x in xs { total := total + x }\nprint total\n')

    def test_for_over_text(self):
        self.assert_same('let out = ""\nfor c in "abc" { out := out + c + "-" }\nprint out\n')

    def test_for_break(self):
        self.assert_same('let xs = [1, 2, 3, 4]\nfor x in xs { if x == 3 { break }\n print x }\n')

    def test_for_continue(self):
        self.assert_same('let xs = [1, 2, 3, 4]\nfor x in xs { if x == 2 { continue }\n print x }\n')

    def test_nested_for(self):
        self.assert_same(
            'let total = 0\n'
            'for a in [1, 2] { for b in [10, 20] { total := total + a*b } }\n'
            'print total\n')

    def test_for_shadowing_and_scope(self):
        # The loop variable lives in a fresh child frame per item and does not
        # overwrite an outer binding of the same name.
        self.assert_same('let x = 99\nfor x in [1, 2] { print x }\nprint x\n')

    def test_for_body_let_does_not_leak(self):
        self.assert_same('let total = 0\nfor x in [1, 2] { let y = x\n total := total + y }\nprint total\n')

    def test_for_empty_iterable(self):
        self.assert_same('let xs = []\nfor x in xs { print x }\nprint "done"\n')

    def test_for_inside_while_break_targets_for(self):
        self.assert_same(
            'let i = 0\nlet total = 0\n'
            'while i < 3 { i := i + 1\n for x in [1, 2, 3] { if x == 2 { break }\n total := total + x } }\n'
            'print total\n')

    def test_for_inside_while_continue_targets_for(self):
        self.assert_same(
            'let i = 0\nlet total = 0\n'
            'while i < 2 { i := i + 1\n for x in [1, 2, 3] { if x == 2 { continue }\n total := total + x } }\n'
            'print total\n')

    # -- core builtins ---------------------------------------------------
    def test_len_builtin(self):
        self.assert_same('print len([1, 2, 3])\nprint len("abcd")\nprint len({a: 1, b: 2})\n')

    def test_text_builtin(self):
        self.assert_same('print text(5)\nprint text(1.50)\nprint text(true)\nprint text("x")\n')

    def test_number_builtin(self):
        self.assert_same('print number("2.50")\nprint number(3)\nprint number(2.5)\n')

    def test_round_builtin(self):
        self.assert_same('print round(2.345, 2)\nprint round(2.5)\nprint round("1.234", 1)\nprint round(7)\n')

    # -- Ok/Err and match ------------------------------------------------
    def test_ok_err_print(self):
        self.assert_same('print Ok(1)\nprint Err("x")\nprint Ok([1, 2])\n')

    def test_match_ok_arm(self):
        self.assert_same('let r = Ok(5)\nmatch r { Ok(v) { print v } Err(e) { print e } }\n')

    def test_match_err_arm(self):
        self.assert_same('let r = Err("bad")\nmatch r { Ok(v) { print v } Err(e) { print e } }\n')

    def test_match_wildcard(self):
        self.assert_same('match Err(1) { Ok(v) { print v } _ { print "other" } }\n')

    def test_match_nested(self):
        self.assert_same(
            'let r = Ok(Err("inner"))\n'
            'match r { Ok(v) { match v { Ok(z) { print z } Err(e) { print e } } } Err(e) { print e } }\n')

    def test_match_continue_in_for(self):
        self.assert_same(
            'let total = 0\n'
            'for x in [1, 2, 3, 4] { match Ok(x) { Ok(v) { if v == 2 { continue }\n total := total + v } _ { print "?" } } }\n'
            'print total\n')

    def test_match_break_in_nested_for(self):
        self.assert_same(
            'let total = 0\n'
            'for a in [1, 2, 3] { for b in [1, 2, 3] { match Ok(b) { Ok(v) { if v == 2 { break }\n total := total + v } _ { print "?" } } } }\n'
            'print total\n')


class IRSubsetRuntimeErrorsMatchInterpreter(unittest.TestCase):
    def assert_same_runtime_error(self, src):
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(G.GamaError) as got:
            IR.run_program(G.parse(src))
        # Same class family, same message and same source position.
        self.assertEqual(str(got.exception), str(ref.exception))
        self.assertIsInstance(got.exception, IR.IRError)

    def test_index_out_of_range(self):
        self.assert_same_runtime_error('let xs = [1, 2]\nprint xs[5]\n')

    def test_index_negative_out_of_range(self):
        self.assert_same_runtime_error('let xs = [1, 2]\nprint xs[-5]\n')

    def test_index_into_scalar(self):
        self.assert_same_runtime_error('let x = 1\nprint x[0]\n')

    def test_len_wrong_type(self):
        self.assert_same_runtime_error('print len(5)\n')

    def test_len_arity(self):
        self.assert_same_runtime_error('print len()\n')

    def test_text_arity(self):
        self.assert_same_runtime_error('print text()\n')

    def test_number_bad_text(self):
        self.assert_same_runtime_error('print number("abc")\n')

    def test_number_arity(self):
        self.assert_same_runtime_error('print number()\n')

    def test_round_bad_digits(self):
        self.assert_same_runtime_error('print round(1.2, "x")\n')

    def test_round_bad_value(self):
        self.assert_same_runtime_error('print round("abc")\n')

    def test_round_arity(self):
        self.assert_same_runtime_error('print round()\n')

    def test_ok_arity(self):
        self.assert_same_runtime_error('print Ok()\n')

    def test_err_arity(self):
        self.assert_same_runtime_error('print Err(1, 2)\n')

    def test_match_non_result(self):
        self.assert_same_runtime_error('match 5 { Ok(v) { print v } _ { print 1 } }\n')

    def test_match_no_arm(self):
        self.assert_same_runtime_error('match Ok(1) { Err(e) { print e } }\n')

    def test_for_needs_list_or_text_map(self):
        self.assert_same_runtime_error('for x in {a: 1} { print x }\n')

    def test_for_needs_list_or_text_scalar(self):
        self.assert_same_runtime_error('for x in 5 { print x }\n')


class IRSubsetStillRejectsUnsupported(unittest.TestCase):
    def assert_unsupported(self, src):
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(G.parse(src))
        self.assertIn("IR does not support", str(ctx.exception))

    def test_pipe_unknown_stage(self):
        # Pipes themselves are supported now; a hand-built AST with a stage
        # outside the documented set must still fail loudly.
        node = G.Pipe(G.Literal([1, 2], 1, 1), [("explode", [])], 1, 1)
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program([G.ExprStmt(node, 1, 1)])
        self.assertIn("IR does not support pipe stage", str(ctx.exception))

    def test_prove_statement(self):
        self.assert_unsupported('prove p = { a: 1 }\n')

    def test_import_statement(self):
        self.assert_unsupported('import "x.gm"\n')

    # Dotted calls (``mod.fn()``) are supported now; their IR-vs-interpreter
    # parity lives in ``test_ir_namespaces.py``.

    def test_lambda(self):
        self.assert_unsupported('let f = fn(x: Int) -> Int { return x }\n')

    def test_expression_callee(self):
        self.assert_unsupported('print (fn(x: Int) -> Int { return x })(1)\n')

    def test_try_expression(self):
        self.assert_unsupported('fn f() -> Result { return Ok(1) }\nprint f()?\n')

    def test_builtin_sql_call(self):
        self.assert_unsupported('print sql("select 1")\n')

    def test_builtin_split_call(self):
        self.assert_unsupported('print split("a,b", ",")\n')

    def test_builtin_unwrap_call(self):
        self.assert_unsupported('print unwrap(Ok(1))\n')

    def test_builtin_wait_user_call(self):
        self.assert_unsupported('wait user "ok?"\n')


class IRRuntimeErrorIsLanguageError(unittest.TestCase):
    def test_runtime_error_is_gama_error(self):
        with self.assertRaises(G.GamaError):
            IR.run_program(G.parse('print len(5)\n'))

    def test_runtime_error_is_ir_error(self):
        with self.assertRaises(IR.IRError):
            IR.run_program(G.parse('print number("abc")\n'))

    def test_round_nan_digits_is_language_error(self):
        # ``int(Decimal('NaN'))`` raises InvalidOperation in Python; the IR
        # must report it as a language error, not leak the raw exception.
        with self.assertRaises(G.GamaError):
            IR.run_program(G.parse('print round(1, number("nan"))\n'))


if __name__ == '__main__':
    unittest.main()
