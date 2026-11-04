import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


def _env(**kw):
    base = {k: v for k, v in os.environ.items()
            if k not in ("GAMA_L_MAX_CALL_DEPTH", "GAMA_L_MAX_OUTPUT_CHARS")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


class CallDepthLimitTests(unittest.TestCase):
    def test_deep_recursion_hits_limit(self):
        src = ('fn down(n: Int) -> Int { if n <= 0 { return 0 } '
               'return down(n - 1) }\n'
               'print down(50)\n')
        with _env(GAMA_L_MAX_CALL_DEPTH="5"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run(src)
        msg = str(ctx.exception)
        self.assertIn("call depth limit exceeded", msg)
        self.assertIn("GAMA_L_MAX_CALL_DEPTH=5", msg)

    def test_normal_recursion_under_limit(self):
        src = ('fn fact(n: Int) -> Int { if n <= 1 { return 1 } '
               'return n * fact(n - 1) }\n'
               'print fact(5)\n')
        with _env(GAMA_L_MAX_CALL_DEPTH="50"):
            self.assertEqual(G.run(src).out, ["120"])

    def test_depth_is_decremented_after_failure(self):
        it = G.Interpreter()
        src = ('fn down(n: Int) -> Int { if n <= 0 { return 0 } '
               'return down(n - 1) }\n')
        ast = G.check(src + 'print down(50)\n')
        with _env(GAMA_L_MAX_CALL_DEPTH="5"):
            with self.assertRaises(G.GamaRuntimeError):
                it.run(ast)
        self.assertEqual(it.call_depth, 0)

    def test_named_function_and_lambda_share_depth_tracking(self):
        src = ('let f = fn(n: Int) -> Int { return n }\n'
               'print f(1)\n')
        with _env(GAMA_L_MAX_CALL_DEPTH="100"):
            self.assertEqual(G.run(src).out, ["1"])


class OutputLimitTests(unittest.TestCase):
    def test_output_cap_raises(self):
        src = 'print "abcdefghijklmnop"\n'
        with _env(GAMA_L_MAX_OUTPUT_CHARS="10"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run(src)
        msg = str(ctx.exception)
        self.assertIn("output limit exceeded", msg)
        self.assertIn("GAMA_L_MAX_OUTPUT_CHARS=10", msg)

    def test_output_cap_is_cumulative(self):
        src = 'print "12345"\nprint "67890"\nprint "!"\n'
        with _env(GAMA_L_MAX_OUTPUT_CHARS="10"):
            with self.assertRaises(G.GamaRuntimeError):
                G.run(src)

    def test_normal_output_unaffected(self):
        src = 'print "hello"\nprint 42\n'
        with _env(GAMA_L_MAX_OUTPUT_CHARS="1000"):
            it = G.run(src)
        self.assertEqual(it.out, ["hello", "42"])
        self.assertEqual(it.out_chars, len("hello") + len("42"))

    def test_verify_output_is_capped(self):
        src = 'prove p = { a: 1 }\nverify p\n'
        with _env(GAMA_L_MAX_OUTPUT_CHARS="0"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run(src)
        self.assertIn("output limit exceeded", str(ctx.exception))

    def test_default_limits_do_not_change_normal_runs(self):
        src = ('fn sq(x: Int) -> Int { return x * x }\n'
               'print sq(6)\n')
        self.assertEqual(G.run(src).out, ["36"])


if __name__ == "__main__":
    unittest.main()
