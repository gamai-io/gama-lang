import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class IRMatchesInterpreter(unittest.TestCase):
    """Each program is run by the reference interpreter and by the IR; printed
    output must be byte-for-byte identical (no silent divergence)."""

    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def test_int_arithmetic(self):
        self.assert_same('print 6*7\nprint (2+3)*4\nprint 10 - 3\nprint 2*3 + 4\n')

    def test_decimal_literals_and_division(self):
        self.assert_same('print 117.00 + 17.00\nprint 1/3\nprint 7.5 % 2\nprint 1.5 * 2\n')

    def test_text_concat_and_comparison(self):
        self.assert_same('print "a" + "b"\nprint "a" < "b"\nprint "a" == "a"\n')

    def test_unary_and_logical(self):
        self.assert_same(
            'print !false\nprint !true\nprint true && false\n'
            'print false || true\nprint true && true\n')

    def test_logical_short_circuit(self):
        # The right operand raises if it is ever evaluated, so a mismatch here
        # would surface as an exception rather than a wrong value.
        self.assert_same(
            'fn boom() -> Bool { return 1/0 == 0 }\n'
            'print true || boom()\nprint false && boom()\n')

    def test_let_and_assign(self):
        self.assert_same('let x = 2\nx := x + 3\nprint x\n')

    def test_if_true_branch(self):
        self.assert_same('let x = 5\nif x > 3 { print "big" } else { print "small" }\n')

    def test_if_else_branch(self):
        self.assert_same('let x = 1\nif x > 3 { print "big" } else { print "small" }\n')

    def test_if_block_scope_shadowing(self):
        self.assert_same('let x = 1\nif true { let x = 9\n print x }\nprint x\n')

    def test_assign_updates_enclosing_scope(self):
        self.assert_same('let x = 1\nif true { if true { x := 7 } }\nprint x\n')

    def test_while_accumulate(self):
        self.assert_same('let i = 0\nlet s = 0\nwhile i < 4 { i := i + 1\n s := s + i }\nprint s\n')

    def test_while_break(self):
        self.assert_same(
            'let i = 0\nwhile i < 5 { i := i + 1\n if i == 3 { break }\n print i }\n')

    def test_while_continue(self):
        self.assert_same(
            'let i = 0\nwhile i < 4 { i := i + 1\n if i == 2 { continue }\n print i }\n')

    def test_nested_while_break_continue(self):
        # Exercises frame unwinding: break/continue leave `if` frames behind.
        self.assert_same(
            'let i = 0\nlet total = 0\n'
            'while i < 4 {\n'
            ' i := i + 1\n'
            ' if i == 3 { continue }\n'
            ' let j = 0\n'
            ' while j < 3 {\n'
            '  j := j + 1\n'
            '  if j == 2 { continue }\n'
            '  if j == 3 { break }\n'
            '  total := total + 1\n'
            ' }\n'
            '}\nprint total\n')

    def test_function_call(self):
        self.assert_same(
            'fn add(a: Int, b: Int) -> Int { return a + b }\nprint add(2,3)\n')

    def test_recursive_function(self):
        self.assert_same(
            'fn fact(n: Int) -> Int { if n <= 1 { return 1 }\n'
            ' return n * fact(n-1) }\nprint fact(5)\n')

    def test_default_parameters(self):
        self.assert_same(
            'fn f(a: Int, b: Int = 10) -> Int { return a + b }\n'
            'print f(1)\nprint f(1,2)\n')

    def test_closure_over_global(self):
        self.assert_same('let a = 10\nfn f(x: Int) -> Int { return x + a }\nprint f(5)\n')

    def test_function_without_return_is_null(self):
        self.assert_same('fn noret(x: Int) { }\nprint noret(1)\n')

    def test_function_effect_print(self):
        self.assert_same(
            'fn p(n: Int) -> Int effects { io.print } { print n\n return n + 1 }\n'
            'print p(3)\n')

    def test_expression_statement_call(self):
        self.assert_same('fn f() -> Int { return 3 }\nf()\nprint f()\n')


class IRRejectsUnsupported(unittest.TestCase):
    def assert_unsupported(self, src):
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(G.parse(src))
        self.assertIn("IR does not support", str(ctx.exception))

    def test_for_loop(self):
        self.assert_unsupported('for i in [1,2] { print i }\n')

    def test_list_literal(self):
        self.assert_unsupported('let xs = [1, 2]\n')

    def test_map_literal(self):
        self.assert_unsupported('let m = { a: 1 }\n')

    def test_index_expression(self):
        self.assert_unsupported('print xs[0]\n')

    def test_dotted_name_and_field_access(self):
        self.assert_unsupported('print m.a\n')

    def test_pipe(self):
        self.assert_unsupported('print xs | count\n')

    def test_match_statement(self):
        self.assert_unsupported('match x { _ { print 1 } }\n')

    def test_prove_statement(self):
        self.assert_unsupported('prove p = { a: 1 }\n')

    def test_import_statement(self):
        self.assert_unsupported('import "x.gm"\n')

    def test_lambda(self):
        self.assert_unsupported('let f = fn(x: Int) -> Int { return x }\n')

    def test_expression_callee(self):
        self.assert_unsupported('print (fn(x: Int) -> Int { return x })(1)\n')

    def test_builtin_call(self):
        self.assert_unsupported('print len("abc")\n')

    def test_builtin_sql_call(self):
        self.assert_unsupported('print sql("select 1")\n')

    def test_return_outside_function(self):
        self.assert_unsupported('return 1\n')

    def test_break_outside_loop(self):
        self.assert_unsupported('break\n')

    def test_continue_outside_loop(self):
        self.assert_unsupported('continue\n')

    def test_else_if_chain(self):
        # The reference interpreter raises TypeError for this shape, so the IR
        # rejects it explicitly instead of inventing a different meaning.
        self.assert_unsupported('if false { print 1 } else if true { print 2 }\n')


class IRProgramAPI(unittest.TestCase):
    def test_compile_accepts_source_string(self):
        self.assertEqual(IR.run_program('print 1 + 1\n').out, ['2'])

    def test_precompiled_program_is_reusable(self):
        program = IR.compile_program(G.parse('print x\n'))
        self.assertEqual(IR.run_program(program, {'x': 7}).out, ['7'])
        self.assertEqual(IR.run_program(program, {'x': 9}).out, ['9'])

    def test_initial_env_dict_seeds_globals(self):
        program = IR.compile_program(G.parse('print a + b\n'))
        result = IR.run_program(program, {'a': 1, 'b': 2})
        self.assertEqual(result.out, ['3'])

    def test_run_result_exposes_globals(self):
        result = IR.run_program('let a = 1\nlet b = 2\n')
        self.assertEqual(result.globals, {'a': 1, 'b': 2})

    def test_disassemble(self):
        listing = IR.compile_program(G.parse('let x = 1\nprint x\n')).disassemble()
        self.assertIn("LET 'x'", listing)
        self.assertIn("PRINT", listing)

    def test_program_passthrough(self):
        program = IR.compile_program(G.parse('print 1\n'))
        self.assertIs(IR.compile_program(program), program)


if __name__ == '__main__':
    unittest.main()
