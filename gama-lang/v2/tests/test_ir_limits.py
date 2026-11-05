# -*- coding: utf-8 -*-
"""Runtime-limit parity between the standalone IR and the interpreter.

The IR VM enforces the same five guards as ``gamal2.Interpreter``:

* ``GAMA_L_MAX_STEPS``      -- one tick per executed statement dispatch plus
  one extra tick per ``for``/``while`` body iteration
* ``GAMA_L_MAX_EXPR_STEPS`` -- one tick per evaluated expression node
* ``GAMA_L_LOOP_LIMIT``     -- one guard per ``while`` iteration (strict parse)
* ``GAMA_L_MAX_OUTPUT_CHARS`` -- cumulative rendered output cap
* ``GAMA_L_MAX_CALL_DEPTH`` -- call depth, clamped to the Python stack budget

The tests below compare the IR against the reference interpreter on the same
programs and the same small caps, assert identical error text, and pin the
documented scope differences (for loops are not bounded by
``GAMA_L_LOOP_LIMIT``; only compiled statement/expression nodes are counted).

Exact scope note: the IR counts one statement tick per *compiled* statement
node and one expression tick per *compiled* expression node, emitted in
evaluation order.  Constructs outside the IR subset are rejected at compile
time and are therefore never counted.  All supported constructs (including
``else if``, function/lambda bodies, defaults, pipe stages, record methods and
``prove``/``verify``) match the interpreter's counters exactly.
"""
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR

_ALL_LIMIT_KEYS = (
    "GAMA_L_MAX_STEPS",
    "GAMA_L_MAX_EXPR_STEPS",
    "GAMA_L_LOOP_LIMIT",
    "GAMA_L_MAX_CALL_DEPTH",
    "GAMA_L_MAX_OUTPUT_CHARS",
)

# Generous caps used when a test only cares about one guard.
_WIDE = {
    "GAMA_L_MAX_STEPS": "1000000",
    "GAMA_L_MAX_EXPR_STEPS": "1000000",
    "GAMA_L_LOOP_LIMIT": "1000000",
    "GAMA_L_MAX_CALL_DEPTH": "1000",
    "GAMA_L_MAX_OUTPUT_CHARS": "1000000",
}


def _env(**kw):
    """Clean GAMA-L limit environment with deterministic overrides."""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith("GAMA_L_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


def _with(base, **kw):
    merged = dict(base)
    merged.update(kw)
    return merged


def _run_it(src, env):
    with _env(**env):
        return G.run(src)


def _run_it_unchecked(src, env):
    with _env(**env):
        return G.Interpreter().run(G.parse(src))


def _run_ir(src, env, checked=True):
    with _env(**env):
        ast = G.check(src) if checked else G.parse(src)
        return IR.run_program(IR.compile_program(ast))


def _error_of(fn):
    try:
        fn()
    except G.GamaError as ex:
        return str(ex)
    raise AssertionError("expected a GamaError")


def _both_errors(src, env, checked=True):
    it = _error_of(lambda: _run_it(src, env))
    ir = _error_of(lambda: _run_ir(src, env, checked=checked))
    return it, ir


_WHILE_LOOP = "let i = 0\nwhile i < 20 { i := i + 1 }\nprint i\n"
_FOR_LOOP = "let s = 0\nfor i in [1,2,3,4,5] { s := s + i }\nprint s\n"
_RECURSION = ("fn down(n: Int) -> Int { if n <= 0 { return 0 } "
              "return down(n - 1) }\nprint down(50)\n")
_EXPR_HEAVY = "let i = 0\nwhile i < 1000 { i := i + 1 }\nprint i\n"


class CounterParityTests(unittest.TestCase):
    """The IR counters equal the interpreter's for supported programs."""

    PROGRAMS = {
        "flat": "print 1\nprint 2\n",
        "nested_expr": "print 1 + 2 * 3\n",
        "while": _WHILE_LOOP,
        "for": _FOR_LOOP,
        "text_for": "let s = 0\nfor c in \"abc\" { s := s + 1 }\nprint s\n",
        "function": "fn f(n: Int) -> Int { return n }\nlet x = f(1)\n",
        "recursion": ("fn fact(n: Int) -> Int { if n <= 1 { return 1 } "
                      "return n * fact(n - 1) }\nprint fact(5)\n"),
        "else_if": ("let x = 2\nif x == 1 { print 1 } "
                    "else if x == 2 { print 2 } else { print 3 }\n"),
        "defaults": ("fn f(a: Int, b: Int = 2) -> Int { return a + b }\n"
                     "print f(1)\nprint f(1, 3)\n"),
        "lambda": ("let f = fn(x: Int) -> Int { return x + 1 }\n"
                   "print f(4)\n"),
        "higher_order": ("fn inc(x: Int) -> Int { return x + 1 }\n"
                         "fn apply(f: Function, v: Int) -> Int { return f(v) }\n"
                         "print apply(inc, 5)\n"),
        "nested_loops": ("let t = 0\nfor i in [1,2,3] { let j = 0\n"
                         "while j < 2 { t := t + i\nj := j + 1 } }\nprint t\n"),
        "break_continue": ("let s = 0\nfor i in [1,2,3,4,5] { "
                           "if i == 3 { continue }\n"
                           "if i == 5 { break }\ns := s + i }\nprint s\n"),
        "record_method": ("record P { x: Int; y: Int; "
                          "fn sum(self: P) -> Int { return self.x + self.y } }\n"
                          "let p = P(2, 3)\nprint p.sum()\n"),
        "record_with": ("record P { x: Int, y: Int }\nlet p = P(1, 2)\n"
                        "let q = p with { x = 5 }\nprint q.x + p.x\n"),
        "record_field_assign": ("record P { x: Int, y: Int }\nlet p = P(1, 2)\n"
                                "p.x := 9\nprint p.x\n"),
        "enum_payload": ("enum O { Some(Int), None }\n"
                         "fn f(o: O) -> Int { match o { Some(v) { return v } "
                         "_ { return 0 } } }\nprint f(Some(7))\n"),
        "match_result": ("fn f() -> Result { return Ok(3) }\nlet r = f()\n"
                         "match r { Ok(v) { print v } Err(e) { print e } }\n"),
        "prove_verify": "prove p = { a: 1 }\nverify p\n",
        "list_index": "let xs = [10, 20, 30]\nprint xs[1]\n",
        "short_circuit": "let f = false\nprint f && (1 / 0 == 1)\n",
    }

    def test_counters_match_interpreter(self):
        for name, src in self.PROGRAMS.items():
            with self.subTest(program=name):
                it = _run_it(src, _WIDE)
                ir = _run_ir(src, _WIDE)
                self.assertEqual(ir.out, it.out)
                self.assertEqual(ir.steps, it.steps)
                self.assertEqual(ir.expr_steps, it.expr_steps)
                self.assertEqual(ir.out_chars, it.out_chars)

    def test_pipe_counters_match_interpreter(self):
        # Column stages need an unchecked Table/Any base (see test_ir_pipe).
        cases = [
            'print [{"n": 1}, {"n": 2}, {"n": 3}] | filter n >= 2 | '
            'select n | sum n\n',
            'print [{"n": 1}, {"n": 2}, {"n": 3}] | filter n > 1 | '
            'limit 2 | map n\n',
            'print [{"n": 1}, {"n": 2}] | count\n',
        ]
        for src in cases:
            with self.subTest(src=src):
                it = _run_it_unchecked(src, _WIDE)
                ir = _run_ir(src, _WIDE, checked=False)
                self.assertEqual(ir.out, it.out)
                self.assertEqual(ir.steps, it.steps)
                self.assertEqual(ir.expr_steps, it.expr_steps)

    def test_exact_simple_counts(self):
        ir = _run_ir("print 1\n", _WIDE)
        self.assertEqual((ir.steps, ir.expr_steps), (1, 1))
        ir = _run_ir("print 1 + 2\n", _WIDE)
        self.assertEqual((ir.steps, ir.expr_steps), (1, 3))
        # For dispatch + one tick per iteration + one per body statement.
        ir = _run_ir("for i in [1,2,3,4,5] { let y = i }\n", _WIDE)
        self.assertEqual(ir.steps, 11)

    def test_function_call_itself_is_not_a_statement_step(self):
        ir = _run_ir("fn f(n: Int) -> Int { return n }\nlet x = f(1)\n", _WIDE)
        self.assertEqual(ir.steps, 3)  # fn decl + let + executed return

    def test_counters_tracked_when_caps_disabled(self):
        with _env():  # all GAMA_L_* removed
            ir = IR.run_program(IR.compile_program(G.check(_WHILE_LOOP)))
        self.assertGreater(ir.steps, 1)
        self.assertGreater(ir.expr_steps, 1)
        self.assertGreater(ir.out_chars, 0)


class StepLimitTests(unittest.TestCase):
    def test_small_limit_matches_interpreter(self):
        for value in (0, 1, 2, 3, 5, 10):
            with self.subTest(limit=value):
                env = _with(_WIDE, GAMA_L_MAX_STEPS=str(value))
                it, ir = _both_errors("while true { }\n", env)
                self.assertEqual(ir, it)
                self.assertIn("step limit exceeded", ir)
                self.assertIn("GAMA_L_MAX_STEPS=%d" % value, ir)
                self.assertIn("(line 1, col 1)", ir)

    def test_limit_does_not_fire_under_cap(self):
        env = _with(_WIDE, GAMA_L_MAX_STEPS="1000")
        self.assertEqual(_run_ir(_WHILE_LOOP, env).out, ["20"])
        self.assertEqual(_run_ir(_WHILE_LOOP, env).steps,
                         _run_it(_WHILE_LOOP, env).steps)

    def test_zero_limit_rejects_first_statement(self):
        env = _with(_WIDE, GAMA_L_MAX_STEPS="0")
        it, ir = _both_errors("print 1\n", env)
        self.assertEqual(ir, it)
        self.assertIn("step limit exceeded: 1 > GAMA_L_MAX_STEPS=0", ir)

    def test_for_iterations_are_counted(self):
        env = _with(_WIDE, GAMA_L_MAX_STEPS="100")
        ir = _run_ir(_FOR_LOOP, env)
        self.assertEqual(ir.steps, _run_it(_FOR_LOOP, env).steps)


class ExprStepLimitTests(unittest.TestCase):
    def test_small_limit_matches_interpreter(self):
        for value in (0, 1, 2, 5, 10):
            with self.subTest(limit=value):
                env = _with(_WIDE, GAMA_L_MAX_EXPR_STEPS=str(value))
                it, ir = _both_errors("while true { }\n", env)
                self.assertEqual(ir, it)
                self.assertIn("expression step limit exceeded", ir)
                self.assertIn("GAMA_L_MAX_EXPR_STEPS=%d" % value, ir)
                self.assertIn("(line 1, col 7)", ir)

    def test_expression_heavy_loop_matches(self):
        env = _with(_WIDE, GAMA_L_MAX_EXPR_STEPS="100")
        it, ir = _both_errors(_EXPR_HEAVY, env)
        self.assertEqual(ir, it)

    def test_exact_nested_count(self):
        env = _with(_WIDE, GAMA_L_MAX_EXPR_STEPS="100")
        ir = _run_ir("print 1 + 2\n", env)
        self.assertEqual(ir.expr_steps, 3)


class LoopLimitTests(unittest.TestCase):
    def test_loop_limit_matches_interpreter(self):
        for value in (0, 1, 2, 3):
            with self.subTest(limit=value):
                env = _with(_WIDE, GAMA_L_LOOP_LIMIT=str(value))
                it, ir = _both_errors("while true { }\n", env)
                self.assertEqual(ir, it)
                self.assertIn("loop limit exceeded", ir)
                self.assertIn("GAMA_L_LOOP_LIMIT=%d" % value, ir)

    def test_loop_limit_does_not_bound_for_loops(self):
        # Documented scope: only ``while`` iterations are capped.
        src = "let s = 0\nfor i in [1,2,3,4,5] { s := s + i }\nprint s\n"
        env = _with(_WIDE, GAMA_L_LOOP_LIMIT="1")
        self.assertEqual(_run_ir(src, env).out, ["15"])
        self.assertEqual(_run_it(src, env).out, ["15"])

    def test_malformed_loop_limit_is_strict(self):
        for bad in ("abc", "10x", ""):
            with self.subTest(value=bad):
                env = _with(_WIDE, GAMA_L_LOOP_LIMIT=bad)
                it, ir = _both_errors("while true { }\n", env)
                self.assertEqual(ir, it)
                self.assertIn("GAMA_L_LOOP_LIMIT must be an integer", ir)

    def test_negative_loop_limit_is_parsed_and_fires(self):
        # Unlike the step caps, a negative value is a real (tiny) limit.
        env = _with(_WIDE, GAMA_L_LOOP_LIMIT="-1")
        it, ir = _both_errors("while true { }\n", env)
        self.assertEqual(ir, it)
        self.assertIn("loop limit exceeded: 1 > GAMA_L_LOOP_LIMIT=-1", ir)


class OutputLimitTests(unittest.TestCase):
    def test_output_cap_matches_interpreter(self):
        for value in (0, 1, 5, 10):
            with self.subTest(limit=value):
                env = _with(_WIDE, GAMA_L_MAX_OUTPUT_CHARS=str(value))
                it, ir = _both_errors('print "abcdefghijklmnop"\n', env)
                self.assertEqual(ir, it)
                self.assertIn("output limit exceeded", ir)
                self.assertIn("GAMA_L_MAX_OUTPUT_CHARS=%d" % value, ir)

    def test_output_cap_is_cumulative(self):
        env = _with(_WIDE, GAMA_L_MAX_OUTPUT_CHARS="10")
        it, ir = _both_errors('print "12345"\nprint "67890"\nprint "!"\n', env)
        self.assertEqual(ir, it)

    def test_verify_output_is_capped(self):
        env = _with(_WIDE, GAMA_L_MAX_OUTPUT_CHARS="0")
        it, ir = _both_errors("prove p = { a: 1 }\nverify p\n", env)
        self.assertEqual(ir, it)

    def test_normal_output_tracks_chars(self):
        env = _with(_WIDE, GAMA_L_MAX_OUTPUT_CHARS="1000")
        ir = _run_ir('print "hello"\nprint 42\n', env)
        self.assertEqual(ir.out, ["hello", "42"])
        self.assertEqual(ir.out_chars, len("hello") + len("42"))


class CallDepthLimitTests(unittest.TestCase):
    def test_depth_limit_matches_interpreter(self):
        for value in (0, 1, 3, 5):
            with self.subTest(limit=value):
                env = _with(_WIDE, GAMA_L_MAX_CALL_DEPTH=str(value))
                it, ir = _both_errors(_RECURSION, env)
                self.assertEqual(ir, it)
                self.assertIn("call depth limit exceeded", ir)
                self.assertIn("GAMA_L_MAX_CALL_DEPTH=%d" % value, ir)
                self.assertIn("effective cap", ir)

    def test_normal_recursion_under_limit(self):
        env = _with(_WIDE, GAMA_L_MAX_CALL_DEPTH="50")
        src = ("fn fact(n: Int) -> Int { if n <= 1 { return 1 } "
               "return n * fact(n - 1) }\nprint fact(5)\n")
        self.assertEqual(_run_ir(src, env).out, ["120"])

    def test_high_value_is_clamped_and_never_leaks_recursion_error(self):
        # Both runtimes clamp to (recursionlimit - 100) // 4.
        env = _with(_WIDE, GAMA_L_MAX_CALL_DEPTH="100000")
        src = ("fn down(n: Int) -> Int { if n <= 0 { return 0 } "
               "return down(n - 1) }\nprint down(500)\n")
        it, ir = _both_errors(src, env)
        self.assertEqual(ir, it)
        self.assertIn("effective cap", ir)

    def test_ir_raises_irruntimeerror_not_recursionerror(self):
        env = _with(_WIDE, GAMA_L_MAX_CALL_DEPTH="100000")
        with _env(**env):
            with self.assertRaises(IR.IRRuntimeError) as ctx:
                IR.run_program(IR.compile_program(G.check(
                    "fn down(n: Int) -> Int { if n <= 0 { return 0 } "
                    "return down(n - 1) }\nprint down(500)\n")))
        self.assertNotIsInstance(ctx.exception, RecursionError)
        self.assertIsInstance(ctx.exception, G.GamaError)


class DisabledAndMalformedEnvTests(unittest.TestCase):
    def test_step_and_expr_caps_are_disabled_by_default(self):
        it = _run_it(_EXPR_HEAVY, _WIDE)
        ir = _run_ir(_EXPR_HEAVY, _WIDE)
        self.assertEqual(ir.out, it.out)
        self.assertEqual(ir.steps, it.steps)
        self.assertEqual(ir.expr_steps, it.expr_steps)

    def test_malformed_step_caps_are_disabled(self):
        for key in ("GAMA_L_MAX_STEPS", "GAMA_L_MAX_EXPR_STEPS"):
            for bad in ("abc", "10x", "-1", ""):
                with self.subTest(key=key, value=bad):
                    env = _with(_WIDE)
                    env[key] = bad
                    it = _run_it("print 1\n", env)
                    ir = _run_ir("print 1\n", env)
                    self.assertEqual(ir.out, it.out)
                    self.assertEqual(ir.steps, it.steps)
                    self.assertEqual(ir.expr_steps, it.expr_steps)

    def test_malformed_output_cap_falls_back_to_default(self):
        env = _with(_WIDE, GAMA_L_MAX_OUTPUT_CHARS="abc")
        self.assertEqual(_run_ir('print "ok"\n', env).out, ["ok"])
        self.assertEqual(_run_it('print "ok"\n', env).out, ["ok"])

    def test_malformed_call_depth_falls_back_to_default(self):
        env = _with(_WIDE, GAMA_L_MAX_CALL_DEPTH="abc")
        self.assertEqual(_run_ir("print 1\n", env).out, ["1"])
        self.assertEqual(_run_it("print 1\n", env).out, ["1"])

    def test_negative_output_cap_fires_on_first_output(self):
        env = _with(_WIDE, GAMA_L_MAX_OUTPUT_CHARS="-1")
        it, ir = _both_errors('print "x"\n', env)
        self.assertEqual(ir, it)


class LimitInteractionTests(unittest.TestCase):
    def test_step_limit_preempts_a_higher_loop_limit(self):
        env = _with(_WIDE, GAMA_L_MAX_STEPS="5", GAMA_L_LOOP_LIMIT="1000000")
        it, ir = _both_errors("while true { }\n", env)
        self.assertEqual(ir, it)
        self.assertIn("step limit exceeded", ir)

    def test_loop_limit_preempts_a_higher_step_limit(self):
        env = _with(_WIDE, GAMA_L_MAX_STEPS="1000000", GAMA_L_LOOP_LIMIT="3")
        it, ir = _both_errors("while true { }\n", env)
        self.assertEqual(ir, it)
        self.assertIn("loop limit exceeded", ir)

    def test_expression_limit_fires_independently(self):
        env = _with(_WIDE, GAMA_L_MAX_EXPR_STEPS="5",
                    GAMA_L_MAX_STEPS="1000000", GAMA_L_LOOP_LIMIT="1000000")
        it, ir = _both_errors("while true { }\n", env)
        self.assertEqual(ir, it)
        self.assertIn("expression step limit exceeded", ir)
        self.assertNotIn("step limit exceeded:", ir.replace(
            "expression step limit exceeded", ""))

    def test_step_limit_fires_independently_of_expression_limit(self):
        env = _with(_WIDE, GAMA_L_MAX_EXPR_STEPS="1000000",
                    GAMA_L_MAX_STEPS="5")
        it, ir = _both_errors("while true { }\n", env)
        self.assertEqual(ir, it)
        self.assertIn("step limit exceeded", ir)

    def test_output_and_step_limits_are_independent(self):
        env = _with(_WIDE, GAMA_L_MAX_STEPS="100000",
                    GAMA_L_MAX_OUTPUT_CHARS="10")
        it, ir = _both_errors('print "abcdefghijklmnop"\n', env)
        self.assertEqual(ir, it)
        self.assertIn("output limit exceeded", ir)

    def test_call_depth_and_output_limits_are_independent(self):
        env = _with(_WIDE, GAMA_L_MAX_CALL_DEPTH="5",
                    GAMA_L_MAX_OUTPUT_CHARS="10")
        it, ir = _both_errors(_RECURSION, env)
        self.assertEqual(ir, it)
        self.assertIn("call depth limit exceeded", ir)


if __name__ == "__main__":
    unittest.main()
