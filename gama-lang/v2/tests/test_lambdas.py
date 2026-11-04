import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class LambdaTests(unittest.TestCase):
    def test_lambda_call(self):
        src = 'let inc = fn(x: Int) -> Int { return x + 1 }\nprint inc(41)\n'
        self.assertEqual(G.run(src).out, ['42'])

    def test_closure_capture(self):
        src = ('let a = 10\n'
               'let add = fn(x: Int) -> Int { return x + a }\n'
               'print add(5)\n')
        self.assertEqual(G.run(src).out, ['15'])

    def test_higher_order_apply(self):
        src = ('fn apply(f: Function, x: Int) -> Int { return f(x) }\n'
               'let inc = fn(x: Int) -> Int { return x + 1 }\n'
               'print apply(inc, 41)\n')
        self.assertEqual(G.run(src).out, ['42'])

    def test_lambda_passed_directly_as_argument(self):
        src = ('fn apply(f: Function, x: Int) -> Int { return f(x) }\n'
               'print apply(fn(x: Int) -> Int { return x * 2 }, 21)\n')
        self.assertEqual(G.run(src).out, ['42'])

    def test_function_typed_let(self):
        src = ('let f: Function = fn(x: Int) -> Int { return x + 1 }\n'
               'print f(2)\n')
        self.assertEqual(G.run(src).out, ['3'])

    def test_call_on_non_function_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let x = 5\nprint x(1)\n')

    def test_named_function_unaffected(self):
        src = 'fn sq(x: Int) -> Int { return x * x }\nprint sq(6)\n'
        self.assertEqual(G.run(src).out, ['36'])

    def test_lambda_declared_effects_allowed(self):
        src = ('let m = fn(x: Text) -> Bool effects { mail } { '
               'return mail(x, "s", "b") }\nprint 1\n')
        self.assertEqual(G.check(src), G.check(src))
        # checking must not raise; interpreter would need a mail backend
        self.assertIsNotNone(G.check(src))

    def test_lambda_undeclared_effects_rejected(self):
        src = ('let m = fn(x: Text) -> Bool { return mail(x, "s", "b") }\n'
               'print 1\n')
        with self.assertRaises(G.GamaEffectError):
            G.check(src)

    def test_err_propagation_from_lambda_call(self):
        src = ('fn safe(x: Int) -> Result { return Err("bad") }\n'
               'let f = fn(x: Int) -> Result { return safe(x)? }\n'
               'print is_err(f(1))\n')
        self.assertEqual(G.run(src).out, ['True'])

    def test_lambda_arity_checked_at_runtime(self):
        src = 'let inc = fn(x: Int) -> Int { return x + 1 }\nprint inc()\n'
        with self.assertRaises(G.GamaRuntimeError):
            G.run(src)


if __name__ == '__main__':
    unittest.main()
