import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G

_ENV_KEYS = (
    "GAMA_L_MAX_STEPS",
    "GAMA_L_LOOP_LIMIT",
    "GAMA_L_MAX_CALL_DEPTH",
    "GAMA_L_MAX_OUTPUT_CHARS",
)


def _env(**kw):
    base = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


def _deep_recursion_src():
    return ('fn down(n: Int) -> Int { if n <= 0 { return 0 } '
            'return down(n - 1) }\n'
            'print down(50)\n')


class StepLimitTests(unittest.TestCase):
    def test_small_limit_stops_infinite_loop(self):
        with _env(GAMA_L_MAX_STEPS="5"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        msg = str(ctx.exception)
        self.assertIn("step limit exceeded", msg)
        self.assertIn("GAMA_L_MAX_STEPS=5", msg)

    def test_limit_does_not_fire_under_cap(self):
        src = "let i = 0\nwhile i < 200 { i := i + 1 }\nprint i\n"
        with _env(GAMA_L_MAX_STEPS="1000"):
            it = G.run(src)
        self.assertEqual(it.out, ["200"])
        self.assertGreater(it.steps, 1)

    def test_disabled_by_default(self):
        src = "let i = 0\nwhile i < 200 { i := i + 1 }\nprint i\n"
        with _env():
            it = G.run(src)
        self.assertEqual(it.out, ["200"])
        # Statements are still counted when the cap is disabled.
        self.assertGreater(it.steps, 1)

    def test_malformed_env_falls_back_to_disabled(self):
        src = "let i = 0\nwhile i < 200 { i := i + 1 }\nprint i\n"
        for bad in ("abc", "10x", "-1", ""):
            with self.subTest(value=bad):
                with _env(GAMA_L_MAX_STEPS=bad):
                    it = G.run(src)
                self.assertEqual(it.out, ["200"])

    def test_zero_limit_rejects_first_statement(self):
        with _env(GAMA_L_MAX_STEPS="0"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("print 1\n")
        self.assertIn("step limit exceeded: 1 > GAMA_L_MAX_STEPS=0",
                      str(ctx.exception))

    def test_message_includes_line_and_col(self):
        with _env(GAMA_L_MAX_STEPS="1"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        self.assertIn("(line 1, col 1)", str(ctx.exception))

    def test_for_loop_iterations_counted(self):
        src = "for i in [1,2,3,4,5] { let y = i }\n"
        # For dispatch + one tick per iteration + one per body statement.
        with _env(GAMA_L_MAX_STEPS="100"):
            it = G.run(src)
        self.assertEqual(it.steps, 11)

    def test_exact_statement_count_for_flat_program(self):
        with _env(GAMA_L_MAX_STEPS="100"):
            it = G.run("print 1\nprint 2\n")
        self.assertEqual(it.steps, 2)

    def test_function_body_statements_counted_but_call_itself_not(self):
        src = "fn f(n: Int) -> Int { return n }\nlet x = f(1)\n"
        with _env(GAMA_L_MAX_STEPS="100"):
            it = G.run(src)
        # Fn declaration + let + the executed return in the body = 3.
        self.assertEqual(it.steps, 3)


class StepAndLoopLimitInteractionTests(unittest.TestCase):
    def test_loop_limit_still_fires_alone(self):
        with _env(GAMA_L_LOOP_LIMIT="3"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        self.assertIn("loop limit exceeded", str(ctx.exception))

    def test_step_limit_preempts_when_loop_limit_is_higher(self):
        with _env(GAMA_L_MAX_STEPS="5", GAMA_L_LOOP_LIMIT="1000000"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        self.assertIn("step limit exceeded", str(ctx.exception))

    def test_loop_limit_preempts_when_step_limit_is_higher(self):
        with _env(GAMA_L_MAX_STEPS="1000000", GAMA_L_LOOP_LIMIT="3"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        self.assertIn("loop limit exceeded", str(ctx.exception))


class OtherLimitsUnchangedTests(unittest.TestCase):
    def test_call_depth_limit_still_applies(self):
        with _env(GAMA_L_MAX_STEPS="100000", GAMA_L_MAX_CALL_DEPTH="5"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run(_deep_recursion_src())
        self.assertIn("call depth limit exceeded", str(ctx.exception))
        self.assertIn("GAMA_L_MAX_CALL_DEPTH=5", str(ctx.exception))

    def test_output_limit_still_applies(self):
        with _env(GAMA_L_MAX_STEPS="100000", GAMA_L_MAX_OUTPUT_CHARS="10"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run('print "abcdefghijklmnop"\n')
        self.assertIn("output limit exceeded", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
