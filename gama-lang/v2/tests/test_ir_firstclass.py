"""IR-vs-interpreter parity for first-class functions and lambdas.

Every successful program is run by the reference interpreter and by the IR;
printed output must be byte-for-byte identical.  Runtime failures are compared
by full message (including line/col), with the IR error also required to be an
``IRError``.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class FirstClassMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def assert_same_runtime_error(self, src):
        # Bypass the checker so runtime dispatch (not static typing) decides.
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(G.GamaError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(ref.exception))
        self.assertIsInstance(got.exception, IR.IRError)

    # -- closures ---------------------------------------------------------
    def test_lambda_call(self):
        self.assert_same(
            'let inc = fn(x: Int) -> Int { return x + 1 }\nprint inc(41)\n')

    def test_closure_capture(self):
        self.assert_same(
            'let a = 10\n'
            'let add = fn(x: Int) -> Int { return x + a }\n'
            'print add(5)\n')

    def test_closure_sees_later_assignment(self):
        # Closures capture the live environment, not a snapshot.
        self.assert_same(
            'let a = 10\n'
            'let f = fn(x: Int) -> Int { return x + a }\n'
            'a := 20\n'
            'print f(1)\n')

    def test_closure_escapes_block(self):
        self.assert_same(
            'let fs = []\n'
            'if true {\n'
            ' let a = 9\n'
            ' fs := [fn(x: Int) -> Int { return x + a }]\n'
            '}\n'
            'print fs[0](1)\n')

    def test_for_loop_captures_latest_iteration_frame(self):
        self.assert_same(
            'let f = fn(x: Int) -> Int { return x }\n'
            'for i in [7, 8] {\n'
            ' f := fn(x: Int) -> Int { return x + i }\n'
            '}\n'
            'print f(1)\n')

    def test_captured_parameter_of_enclosing_lambda(self):
        self.assert_same(
            'let mk = fn(a: Int) -> Function {\n'
            ' return fn(b: Int) -> Int { return a + b }\n'
            '}\n'
            'print mk(2)(3)\n')

    # -- higher order -----------------------------------------------------
    def test_higher_order_apply_named_function(self):
        self.assert_same(
            'fn inc(x: Int) -> Int { return x + 1 }\n'
            'fn apply(f: Function, x: Int) -> Int { return f(x) }\n'
            'print apply(inc, 41)\n')

    def test_higher_order_apply_lambda_argument(self):
        self.assert_same(
            'fn apply(f: Function, x: Int) -> Int { return f(x) }\n'
            'print apply(fn(x: Int) -> Int { return x * 2 }, 21)\n')

    def test_higher_order_function_returned_from_function(self):
        self.assert_same(
            'fn twice(f: Function, x: Int) -> Int { return f(f(x)) }\n'
            'print twice(fn(x: Int) -> Int { return x + 1 }, 40)\n')

    def test_lambda_returned_from_function(self):
        self.assert_same(
            'fn makeAdder(a: Int) -> Function {\n'
            ' return fn(x: Int) -> Int { return x + a }\n'
            '}\n'
            'let add5 = makeAdder(5)\n'
            'print add5(37)\n')

    def test_curried_returned_lambdas(self):
        self.assert_same(
            'let mk = fn(a: Int) -> Function {\n'
            ' return fn(b: Int) -> Function {\n'
            '  return fn(c: Int) -> Int { return a + b + c }\n'
            ' }\n'
            '}\n'
            'print mk(1)(2)(3)\n')

    def test_expression_callee_call_result(self):
        self.assert_same(
            'fn makeAdder(a: Int) -> Function {\n'
            ' return fn(x: Int) -> Int { return x + a }\n'
            '}\n'
            'print makeAdder(5)(37)\n')

    # -- named function values -------------------------------------------
    def test_named_function_bound_to_let(self):
        self.assert_same(
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let f = add\n'
            'print f(2, 3)\n')

    def test_named_function_alias(self):
        self.assert_same(
            'let f = fn(x: Int) -> Int { return x + 1 }\n'
            'let g = f\n'
            'print g(2)\n')

    def test_named_function_as_argument(self):
        self.assert_same(
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'fn apply(f: Function, x: Int) -> Int { return f(x, 10) }\n'
            'print apply(add, 5)\n')

    def test_generic_named_function_as_value(self):
        self.assert_same(
            'fn id<T>(x: T) -> T { return x }\n'
            'let f = id\n'
            'print f(5)\n'
            'print f("x")\n')

    def test_parenthesized_named_function_call(self):
        self.assert_same(
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'print (add)(20, 22)\n')

    def test_function_value_rendering(self):
        self.assert_same(
            'let f = fn(x: Int) -> Int { return x + 1 }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'print f\n'
            'print add\n')

    # -- lambdas and recursion -------------------------------------------
    def test_iife(self):
        self.assert_same(
            'print (fn(x: Int) -> Int { return x + 1 })(41)\n')

    def test_iife_captures_outer_binding(self):
        self.assert_same(
            'let a = 10\n'
            'print (fn(x: Int) -> Int { return x + a })(5)\n')

    def test_recursive_lambda(self):
        self.assert_same(
            'let fact = fn(n: Int) -> Int {\n'
            ' if n <= 1 { return 1 }\n'
            ' return n * fact(n - 1)\n'
            '}\n'
            'print fact(5)\n')

    def test_lambda_default_parameters(self):
        self.assert_same(
            'let f = fn(x: Int, y: Int = 10) -> Int { return x + y }\n'
            'print f(1)\n'
            'print f(1, 2)\n')

    # -- runtime errors ---------------------------------------------------
    def test_lambda_arity_error(self):
        self.assert_same_runtime_error(
            'let inc = fn(x: Int) -> Int { return x + 1 }\nprint inc()\n')

    def test_lambda_too_many_arguments(self):
        self.assert_same_runtime_error(
            'let inc = fn(x: Int) -> Int { return x + 1 }\nprint inc(1, 2)\n')

    def test_named_function_value_arity_error(self):
        self.assert_same_runtime_error(
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let f = add\n'
            'print f(1)\n')

    def test_non_callable_binding(self):
        self.assert_same_runtime_error('let f = 5\nprint f(1)\n')

    def test_non_callable_expression(self):
        self.assert_same_runtime_error('print (5)(1)\n')

    def test_unknown_function(self):
        self.assert_same_runtime_error('print nope(1)\n')


if __name__ == '__main__':
    unittest.main()
