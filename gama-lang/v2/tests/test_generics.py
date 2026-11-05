"""Generic type parameters on named functions (type erasure).

Syntax::

    fn id<T>(x: T) -> T { return x }
    fn pair<A, B>(a: A, b: B) -> Text { return text(a) + ":" + text(b) }

Type parameters are erased to ``Any`` at runtime.  They are accepted in
parameter and return annotations (and in ``let`` annotations inside the body),
but the checker never instantiates them: calls accept any argument type and
the call result is inferred from the argument where possible, otherwise
``Any``.  Not supported (and intentionally rejected/ignored): generic records or
enums, bounds, explicit call type arguments, and generic lambdas.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir


class GenericFunctionTests(unittest.TestCase):
    # -- basic identity --------------------------------------------------
    def test_identity_int(self):
        it = G.run(
            'fn id<T>(x: T) -> T { return x }\n'
            'let n: Int = id(5)\n'
            'print n\n')
        self.assertEqual(it.out, ["5"])

    def test_identity_text(self):
        it = G.run(
            'fn id<T>(x: T) -> T { return x }\n'
            'let s: Text = id("x")\n'
            'print s\n')
        self.assertEqual(it.out, ["x"])

    def test_identity_list(self):
        it = G.run(
            'fn id<T>(x: T) -> T { return x }\n'
            'let xs: List = id([1, 2, 3])\n'
            'print xs\n')
        self.assertEqual(it.out, ["[1, 2, 3]"])

    def test_identity_preserves_value_at_runtime(self):
        # Erasure: the exact value is passed through, not a cast.
        it = G.run(
            'fn id<T>(x: T) -> T { return x }\n'
            'print id(true)\n'
            'print id(null)\n'
            'print id(1.50)\n')
        self.assertEqual(it.out, ["True", "None", "1.50"])

    # -- multiple type parameters ---------------------------------------
    def test_two_type_parameters(self):
        it = G.run(
            'fn pair<A, B>(a: A, b: B) -> Text '
            '{ return text(a) + ":" + text(b) }\n'
            'print pair(1, "a")\n'
            'print pair("x", 2)\n')
        self.assertEqual(it.out, ["1:a", "x:2"])

    def test_type_parameter_in_return(self):
        it = G.run(
            'fn first<A, B>(a: A, b: B) -> A { return a }\n'
            'let f: Text = first("keep", 2)\n'
            'print f\n')
        self.assertEqual(it.out, ["keep"])

    # -- generics calling generics / recursion / defaults ----------------
    def test_generic_calls_generic(self):
        it = G.run(
            'fn id<T>(x: T) -> T { return x }\n'
            'fn wrap<U>(x: U) -> U { return id(x) }\n'
            'print wrap("q")\n')
        self.assertEqual(it.out, ["q"])

    def test_recursion_with_type_parameter(self):
        it = G.run(
            'fn repeat<T>(n: Int, x: T) -> T {\n'
            '  if n <= 1 { return x }\n'
            '  return repeat(n - 1, x)\n'
            '}\n'
            'print repeat(3, "z")\n')
        self.assertEqual(it.out, ["z"])

    def test_defaults_with_type_parameters(self):
        it = G.run(
            'fn pair<A, B>(a: A, b: B = "d") -> Text '
            '{ return text(a) + ":" + text(b) }\n'
            'print pair(1)\n'
            'print pair(1, "x")\n')
        self.assertEqual(it.out, ["1:d", "1:x"])

    def test_generic_function_as_value_and_higher_order(self):
        it = G.run(
            'fn apply<A, B>(f: Function, x: A) -> B { return f(x) }\n'
            'fn inc(x: Int) -> Int { return x + 1 }\n'
            'fn id<T>(x: T) -> T { return x }\n'
            'let g = id\n'
            'print apply(inc, 3)\n'
            'print g(7)\n')
        self.assertEqual(it.out, ["4", "7"])

    # -- checking errors -------------------------------------------------
    def test_wrong_arity(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"id expects 1\.\.1 args"):
            G.check('fn id<T>(x: T) -> T { return x }\nprint id(1, 2)\n')

    def test_missing_required_argument(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"id expects 1\.\.1 args"):
            G.check('fn id<T>(x: T) -> T { return x }\nprint id()\n')

    def test_duplicate_type_parameter(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type parameter T"):
            G.check('fn f<T, T>(x: T) -> T { return x }\n')

    def test_duplicate_parameter_name(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate parameter x"):
            G.check('fn f<T>(x: T, x: T) -> T { return x }\n')

    def test_concrete_argument_type_still_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"f: n Text != Int"):
            G.check(
                'fn f<T>(x: T, n: Int) -> Text { return text(n) }\n'
                'print f(1, "bad")\n')

    def test_unrelated_return_type_error(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"return Text != Int"):
            G.check('fn f<T>(x: T) -> Int { return text(x) }\n')

    def test_let_annotation_still_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"n: Text != Int"):
            G.check(
                'fn plain() -> Text { return "s" }\n'
                'let n: Int = plain()\n')

    # -- IR --------------------------------------------------------------
    def test_ir_matches_interpreter(self):
        src = (
            'fn id<T>(x: T) -> T { return x }\n'
            'fn pair<A, B>(a: A, b: B) -> Text '
            '{ return text(a) + ":" + text(b) }\n'
            'fn repeat<T>(n: Int, x: T) -> T {\n'
            '  if n <= 1 { return x }\n'
            '  return repeat(n - 1, x)\n'
            '}\n'
            'print id(1)\n'
            'print id("x")\n'
            'print pair(2, "b")\n'
            'print repeat(2, [4])\n')
        expected = G.run(src).out
        got = gama_ir.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def test_ir_treats_generic_as_erased_function(self):
        # The IR compiler ignores type parameters: they do not appear in the
        # emitted function proto, and calls behave like normal functions.
        ast = G.check('fn id<T>(x: T) -> T { return x }\nprint id(9)\n')
        self.assertEqual(gama_ir.run_program(ast).out, ["9"])

    # -- formatting ------------------------------------------------------
    def test_formatter_emits_type_parameters(self):
        src = (
            'fn id<T>(x: T) -> T { return x }\n'
            'fn pair<A, B>(a: A, b: B) -> Text '
            '{ return text(a) + ":" + text(b) }\n')
        out = gama_fmt.format_source(src)
        self.assertIn("fn id<T>(x: T) -> T", out)
        self.assertIn("fn pair<A, B>(a: A, b: B) -> Text", out)

    def test_formatter_round_trips_type_parameters(self):
        src = ('fn id<T>(x: T) -> T { return x }\n'
               'print id(1)\n')
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out))
        fn = G.parse(out)[0]
        self.assertEqual(fn.type_params, ["T"])

    def test_formatter_leaves_plain_functions_unchanged(self):
        src = 'fn add(a: Int, b: Int) -> Int { return a + b }\n'
        out = gama_fmt.format_source(src)
        self.assertNotIn("<", out)
        self.assertIn("fn add(a: Int, b: Int) -> Int", out)

    # -- unsupported surface stays unsupported ---------------------------
    def test_generic_lambda_is_not_supported(self):
        with self.assertRaises(G.GamaError):
            G.check('let f = fn<T>(x: T) -> T { return x }\n')

    def test_explicit_call_type_arguments_are_not_supported(self):
        with self.assertRaises(G.GamaError):
            G.check('fn id<T>(x: T) -> T { return x }\nprint id<Int>(1)\n')


if __name__ == "__main__":
    unittest.main()
