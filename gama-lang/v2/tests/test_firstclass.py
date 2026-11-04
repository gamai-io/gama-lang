import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class NamedFunctionValueTests(unittest.TestCase):
    def test_named_function_bound_to_let(self):
        src = ('fn add(a: Int, b: Int) -> Int { return a + b }\n'
               'let f = add\n'
               'print f(2, 3)\n')
        self.assertEqual(G.run(src).out, ['5'])

    def test_named_function_as_argument(self):
        src = ('fn inc(x: Int) -> Int { return x + 1 }\n'
               'fn apply(f: Function, x: Int) -> Int { return f(x) }\n'
               'print apply(inc, 41)\n')
        self.assertEqual(G.run(src).out, ['42'])

    def test_named_function_reference_has_function_type(self):
        src = ('fn add(a: Int, b: Int) -> Int { return a + b }\n'
               'let f: Function = add\n'
               'print 1\n')
        self.assertEqual(G.run(src).out, ['1'])

    def test_named_function_value_arity_checked_at_runtime(self):
        src = ('fn add(a: Int, b: Int) -> Int { return a + b }\n'
               'let f = add\n'
               'print f(1)\n')
        with self.assertRaises(G.GamaRuntimeError):
            G.run(src)

    def test_named_function_value_call_through_parens(self):
        src = ('fn add(a: Int, b: Int) -> Int { return a + b }\n'
               'print (add)(20, 22)\n')
        self.assertEqual(G.run(src).out, ['42'])


class IIFETests(unittest.TestCase):
    def test_iife_parses_and_runs(self):
        src = 'print (fn(x: Int) -> Int { return x + 1 })(41)\n'
        self.assertEqual(G.run(src).out, ['42'])

    def test_iife_captures_outer_binding(self):
        src = ('let a = 10\n'
               'print (fn(x: Int) -> Int { return x + a })(5)\n')
        self.assertEqual(G.run(src).out, ['15'])

    def test_iife_arity_error(self):
        src = 'print (fn(x: Int) -> Int { return x + 1 })(1, 2)\n'
        with self.assertRaises(G.GamaTypeError):
            G.run(src)

    def test_iife_argument_type_error(self):
        src = 'print (fn(x: Int) -> Int { return x + 1 })("nope")\n'
        with self.assertRaises(G.GamaTypeError):
            G.run(src)

    def test_call_on_non_function_expression_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print (5)(1)\n')


class RecursiveLambdaTests(unittest.TestCase):
    def test_recursive_factorial(self):
        src = ('let fact = fn(n: Int) -> Int { if n <= 1 { return 1 } '
               'return n * fact(n - 1) }\n'
               'print fact(5)\n')
        self.assertEqual(G.run(src).out, ['120'])

    def test_recursive_lambda_keeps_existing_named_function_semantics(self):
        src = ('fn fact(n: Int) -> Int { if n <= 1 { return 1 } '
               'return n * fact(n - 1) }\n'
               'print fact(6)\n')
        self.assertEqual(G.run(src).out, ['720'])


if __name__ == '__main__':
    unittest.main()
