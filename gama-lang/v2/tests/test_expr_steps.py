import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G

_ENV_KEYS = (
    "GAMA_L_MAX_EXPR_STEPS",
    "GAMA_L_MAX_STEPS",
    "GAMA_L_LOOP_LIMIT",
    "GAMA_L_MAX_CALL_DEPTH",
    "GAMA_L_MAX_OUTPUT_CHARS",
)


def _env(**kw):
    base = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


_EXPR_HEAVY_LOOP = "let i = 0\nwhile i < 1000 { i := i + 1 }\nprint i\n"


class ExprStepLimitTests(unittest.TestCase):
    def test_small_limit_stops_expression_heavy_loop(self):
        with _env(GAMA_L_MAX_EXPR_STEPS="10"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run(_EXPR_HEAVY_LOOP)
        msg = str(ctx.exception)
        self.assertIn("expression step limit exceeded", msg)
        self.assertIn("GAMA_L_MAX_EXPR_STEPS=10", msg)

    def test_limit_does_not_fire_under_cap(self):
        with _env(GAMA_L_MAX_EXPR_STEPS="100000"):
            it = G.run(_EXPR_HEAVY_LOOP)
        self.assertEqual(it.out, ["1000"])
        self.assertGreater(it.expr_steps, 1)

    def test_disabled_by_default(self):
        with _env():
            it = G.run(_EXPR_HEAVY_LOOP)
        self.assertEqual(it.out, ["1000"])
        # Expressions are still counted when the cap is disabled.
        self.assertGreater(it.expr_steps, 1)

    def test_malformed_env_falls_back_to_disabled(self):
        for bad in ("abc", "10x", "-1", ""):
            with self.subTest(value=bad):
                with _env(GAMA_L_MAX_EXPR_STEPS=bad):
                    it = G.run(_EXPR_HEAVY_LOOP)
                self.assertEqual(it.out, ["1000"])

    def test_zero_limit_rejects_first_expression(self):
        with _env(GAMA_L_MAX_EXPR_STEPS="0"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        msg = str(ctx.exception)
        self.assertIn("expression step limit exceeded: 1 > GAMA_L_MAX_EXPR_STEPS=0", msg)
        self.assertIn("(line 1, col 7)", msg)

    def test_message_includes_line_and_col_for_nested_expression(self):
        # The `true` condition is the first evaluated expression: line 1, col 7.
        with _env(GAMA_L_MAX_EXPR_STEPS="0"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        self.assertIn("(line 1, col 7)", str(ctx.exception))

    def test_exact_simple_count_counts_only_the_root_expression(self):
        with _env(GAMA_L_MAX_EXPR_STEPS="100"):
            it = G.run("print 1\n")
        self.assertEqual(it.expr_steps, 1)
        self.assertEqual(it.steps, 1)

    def test_exact_nested_count_counts_each_nested_expression(self):
        # Binary root + left literal + right literal = three expression steps.
        with _env(GAMA_L_MAX_EXPR_STEPS="100"):
            it = G.run("print 1 + 2\n")
        self.assertEqual(it.expr_steps, 3)
        self.assertEqual(it.steps, 1)

    def test_counter_reaches_limit_plus_one_on_failure(self):
        it = G.Interpreter()
        ast = G.check("while true { }\n")
        with _env(GAMA_L_MAX_EXPR_STEPS="5"):
            with self.assertRaises(G.GamaRuntimeError):
                it.run(ast)
        self.assertEqual(it.expr_steps, 6)
        self.assertGreater(it.steps, 0)


class ExprStepAndStepLimitInteractionTests(unittest.TestCase):
    def test_expression_limit_fires_when_other_limits_are_higher(self):
        with _env(GAMA_L_MAX_EXPR_STEPS="5", GAMA_L_MAX_STEPS="1000000",
                  GAMA_L_LOOP_LIMIT="1000000"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        self.assertIn("expression step limit exceeded", str(ctx.exception))

    def test_statement_step_limit_still_fires_independently(self):
        with _env(GAMA_L_MAX_EXPR_STEPS="1000000", GAMA_L_MAX_STEPS="5"):
            with self.assertRaises(G.GamaRuntimeError) as ctx:
                G.run("while true { }\n")
        msg = str(ctx.exception)
        self.assertIn("step limit exceeded", msg)
        self.assertNotIn("expression step limit exceeded", msg)

    def test_statement_limit_counts_are_independent_of_expression_limit(self):
        # A tiny expression cap must not change statement counting before it fires.
        it = G.Interpreter()
        ast = G.check("let i = 0\nwhile i < 1000 { i := i + 1 }\n")
        with _env(GAMA_L_MAX_EXPR_STEPS="6"):
            with self.assertRaises(G.GamaRuntimeError):
                it.run(ast)
        # Two statement dispatches (let, while) plus one per loop iteration.
        self.assertEqual(it.steps, 4)


if __name__ == "__main__":
    unittest.main()
