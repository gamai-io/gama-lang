"""Generic type parameters on lambda expressions (type erasure).

Syntax::

    let id = fn<T>(x: T) -> T { return x }
    let n: Int = id(5)
    let s: Text = id("x")

    let pair = fn<A, B>(a: A, b: B) -> Text {
      return text(a) + ":" + text(b)
    }
    pair(1, "x")

Type parameters are erased to ``Any`` at runtime (exactly like generic named
functions).  The checker validates duplicate names, checks concrete argument
positions, and infers the call result when the return annotation is a type
parameter.  Explicit call type arguments work for lambda values bound with
``let`` (``id<Int>(5)``).  Generic lambdas are first-class: they can be passed
where a ``Function`` is expected and called through that parameter.  The IR
backend keeps rejecting lambdas explicitly (``IRUnsupported``); it never
silently compiles them with different semantics.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir


ID = 'let id = fn<T>(x: T) -> T { return x }\n'
PAIR = ('let pair = fn<A, B>(a: A, b: B) -> Text '
        '{ return text(a) + ":" + text(b) }\n')


class GenericLambdaRuntimeTests(unittest.TestCase):
    def test_identity_int(self):
        it = G.run(ID + 'let n: Int = id(5)\nprint n\n')
        self.assertEqual(it.out, ["5"])

    def test_identity_text(self):
        it = G.run(ID + 'let s: Text = id("x")\nprint s\n')
        self.assertEqual(it.out, ["x"])

    def test_two_type_parameters(self):
        it = G.run(PAIR + 'print pair(1, "x")\nprint pair("y", 2)\n')
        self.assertEqual(it.out, ["1:x", "y:2"])

    def test_runtime_erasure_preserves_values(self):
        # Erasure: no casts; the exact value passes through.
        it = G.run(ID + 'print id(true)\nprint id(null)\nprint id(1.50)\n')
        self.assertEqual(it.out, ["True", "None", "1.50"])

    def test_lambda_as_value(self):
        it = G.run(ID + 'let g: Function = id\nprint g(7)\n')
        self.assertEqual(it.out, ["7"])

    def test_higher_order_generic_lambda_argument(self):
        src = (ID +
               'let twice = fn<T>(f: Function, x: T) -> T '
               '{ return f(f(x)) }\n'
               'let inc = fn(n: Int) -> Int { return n + 1 }\n'
               'let n: Int = twice(inc, 1)\n'
               'print n\n')
        self.assertEqual(G.run(src).out, ["3"])

    def test_generic_lambda_passed_to_function_parameter(self):
        src = (ID +
               'let apply = fn<T>(f: Function, x: T) -> T '
               '{ return f(x) }\n'
               'let n: Int = apply(id, 41)\n'
               'print n\n')
        self.assertEqual(G.run(src).out, ["41"])


class GenericLambdaCallerTests(unittest.TestCase):
    def test_explicit_type_argument_on_lambda_value(self):
        it = G.run(ID + 'print id<Int>(5)\nprint id<Text>("x")\n')
        self.assertEqual(it.out, ["5", "x"])

    def test_explicit_type_arguments_two_parameters(self):
        it = G.run(PAIR + 'print pair<Int, Text>(1, "a")\n')
        self.assertEqual(it.out, ["1:a"])

    def test_explicit_type_argument_rejects_wrong_value(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"lambda: x Text != Int"):
            G.check(ID + 'print id<Int>("x")\n')

    def test_explicit_type_argument_count_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"2 type arguments, got 1"):
            G.check(PAIR + 'print pair<Int>(1, "x")\n')

    def test_wrong_arity(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"lambda expects 1\.\.1 args, got 2"):
            G.check(ID + 'print id(1, 2)\n')

    def test_result_inference_from_return_type_parameter(self):
        # The call result is Text, so binding it to Int must fail.
        with self.assertRaisesRegex(G.GamaTypeError, r"n: Text != Int"):
            G.check(ID + 'let n: Int = id("x")\n')

    def test_duplicate_type_parameter(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type parameter T"):
            G.check('let f = fn<T, T>(x: T) -> T { return x }\n')

    def test_duplicate_parameter(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate parameter x"):
            G.check('let f = fn<T>(x: T, x: T) -> T { return x }\n')

    def test_concrete_parameter_still_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"lambda: n Text != Int"):
            G.check('let f = fn<T>(x: T, n: Int) -> Text '
                    '{ return text(x) + text(n) }\n'
                    'print f(1, "bad")\n')


class GenericLambdaFormatterTests(unittest.TestCase):
    def test_formatter_emits_lambda_type_parameters(self):
        out = gama_fmt.format_source(ID + PAIR)
        self.assertIn("fn<T>(x: T) -> T", out)
        self.assertIn("fn<A, B>(a: A, b: B) -> Text", out)

    def test_formatter_round_trips(self):
        src = (ID + PAIR +
               'let n: Int = id<Int>(5)\n'
               'print pair<Int, Text>(1, "x")\n')
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertIn("id<Int>", out)
        lambdas = [s.expr for s in G.parse(out)
                   if isinstance(s, G.Let) and isinstance(s.expr, G.Lambda)]
        self.assertEqual(lambdas[0].type_params, ["T"])
        self.assertEqual(lambdas[1].type_params, ["A", "B"])

    def test_formatter_wraps_long_lambda_header(self):
        src = ('let pair = fn<A, B>(a: A, b: B) -> Text '
               '{ return text(a) + ":" + text(b) }\n')
        out = gama_fmt.format_source(src, width=30)
        self.assertIn("fn<A, B>(", out)
        self.assertIn("\n", out)
        self.assertEqual(out, gama_fmt.format_source(out, width=30))


class GenericLambdaIRTests(unittest.TestCase):
    def test_ir_runs_generic_lambda_with_erasure(self):
        src = ID + 'print id(1)\n'
        self.assertEqual(gama_ir.run_program(G.check(src)).out,
                         G.run(src).out)

    def test_ir_runs_generic_lambda_inside_named_function(self):
        src = ('fn outer() -> Any {\n'
               '  let id = fn<T>(x: T) -> T { return x }\n'
               '  return id(1)\n'
               '}\n'
               'print outer()\n')
        self.assertEqual(gama_ir.run_program(G.check(src)).out,
                         G.run(src).out)


if __name__ == "__main__":
    unittest.main()
