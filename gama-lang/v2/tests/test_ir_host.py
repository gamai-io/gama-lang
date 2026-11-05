# -*- coding: utf-8 -*-
"""Host-injected Python callables in the standalone IR backend.

``run_program(ast, env={...})`` (and ``run_file``) may seed the global frame
with plain Python callables.  A call to such a name evaluates its arguments on
the VM, invokes the Python callable and returns its result unchanged as an IR
value.  The callable is a normal value: it can be aliased, passed as a
``Function`` argument, returned from an IR function and called through a
variable or an expression callee.

Covered here:

* basic host calls and use of their results in IR arithmetic;
* higher-order use (host callable passed to an IR function, returned from one,
  reached through an expression callee);
* positional-arity checks from ``inspect.signature`` before arguments run;
* wrapping of host exceptions (``ValueError``/``TypeError``) into
  :class:`IRRuntimeError` with the call site's line/col;
* non-callable bindings staying errors (with interpreter parity where the
  interpreter can express the same program);
* interaction with IR limits (host calls count against
  ``GAMA_L_MAX_CALL_DEPTH``; a host call does not bypass step/output caps) and
  proof that no ``gamal2.Interpreter`` is instantiated;
* ``compile_file`` + ``run_file`` with a host environment.

Programs that reference host-only names use ``gamal2.parse`` (no checker): the
type checker has no declaration for an environment-injected binding.  Checked
programs are still used for the interpreter-equivalence checks.
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


def _clean_env(**kw):
    """Deterministic GAMA-L environment with only the named limits."""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith("GAMA_L_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


def add(a, b):
    return a + b


def greet(name):
    return "hello " + name


class HostCallableBasics(unittest.TestCase):
    def test_host_add_and_greet(self):
        src = 'print add(2, 3)\nprint greet("bob")\n'
        res = IR.run_program(G.parse(src), env={"add": add, "greet": greet})
        self.assertEqual(res.out, ["5", "hello bob"])

    def test_host_result_used_in_ir_expression(self):
        src = 'print add(2, 3) * 2\n'
        res = IR.run_program(G.parse(src), env={"add": add})
        self.assertEqual(res.out, ["10"])

    def test_host_container_result_renders(self):
        src = 'print rows()\n'
        res = IR.run_program(
            G.parse(src), env={"rows": lambda: [{"a": 1}, {"a": 2}]})
        self.assertEqual(res.out, ['[{"a": 1}, {"a": 2}]'])

    def test_host_varargs(self):
        src = 'print total(1, 2, 3, 4)\n'
        res = IR.run_program(
            G.parse(src), env={"total": lambda *xs: sum(xs)})
        self.assertEqual(res.out, ["10"])

    def test_host_default_arguments_accepted(self):
        def scaled(x, factor=10):
            return x * factor

        src = 'print scaled(3)\nprint scaled(3, 2)\n'
        res = IR.run_program(G.parse(src), env={"scaled": scaled})
        self.assertEqual(res.out, ["30", "6"])

    def test_host_call_on_dotted_env_name(self):
        src = 'print math.add(4, 5)\n'
        res = IR.run_program(G.parse(src), env={"math.add": add})
        self.assertEqual(res.out, ["9"])


class HostCallableFirstClass(unittest.TestCase):
    def test_alias_and_expression_callee(self):
        src = (
            'let f = add\n'
            'print f(1, 2)\n'
            'print (add)(3, 4)\n'
        )
        res = IR.run_program(G.parse(src), env={"add": add})
        self.assertEqual(res.out, ["3", "7"])

    def test_host_passed_as_function_parameter(self):
        src = (
            'fn apply(f: Function, x: Int) -> Int { return f(x, 10) }\n'
            'print apply(add, 5)\n'
        )
        res = IR.run_program(G.parse(src), env={"add": add})
        self.assertEqual(res.out, ["15"])

    def test_host_returned_from_ir_function(self):
        src = (
            'fn pick() -> Function { return add }\n'
            'print pick()(20, 22)\n'
        )
        res = IR.run_program(G.parse(src), env={"add": add})
        self.assertEqual(res.out, ["42"])

    def test_host_returning_another_host_callable(self):
        def mk(prefix):
            return lambda name: prefix + name

        src = 'print mk("hi " )("joe")\n'
        res = IR.run_program(G.parse(src), env={"mk": mk})
        self.assertEqual(res.out, ["hi joe"])


class HostCallableErrors(unittest.TestCase):
    def test_too_few_arguments(self):
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse('print add(1)\n'), env={"add": add})
        self.assertIn("add expects 2..2 args, got 1", str(got.exception))
        self.assertEqual((got.exception.line, got.exception.col), (1, 7))

    def test_too_many_arguments(self):
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse('print add(1, 2, 3)\n'), env={"add": add})
        self.assertIn("add expects 2..2 args, got 3", str(got.exception))

    def test_varargs_arity_has_no_upper_bound(self):
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(
                G.parse('print total()\n'),
                env={"total": lambda a, *rest: a + sum(rest)})
        # ``a`` is required even though *rest makes the upper bound unbounded.
        self.assertIn("total expects at least 1 args, got 0",
                      str(got.exception))

    def test_host_value_error_is_wrapped_with_line_col(self):
        def boom(x):
            raise ValueError("nope")

        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse('print boom(1)\n'), env={"boom": boom})
        exc = got.exception
        self.assertIn("host callable boom raised ValueError: nope", str(exc))
        self.assertEqual((exc.line, exc.col), (1, 7))
        # Wrapped errors stay catchable as the language error family too.
        self.assertIsInstance(exc, G.GamaError)
        self.assertIsInstance(exc, IR.IRError)

    def test_host_type_error_is_wrapped(self):
        def bad(x):
            return x + "text"  # str + int -> TypeError

        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse('print bad(1)\n'), env={"bad": bad})
        self.assertIn("host callable bad raised TypeError:", str(got.exception))

    def test_host_ir_runtime_error_is_not_double_wrapped(self):
        def host():
            raise IR.IRRuntimeError("lang error", 3, 4)

        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse('host()\n'), env={"host": host})
        self.assertEqual(str(got.exception), "lang error (line 3, col 4)")

    def test_non_callable_binding(self):
        src = 'print x(1)\n'
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse(src), env={"x": 5})
        self.assertEqual(str(got.exception), "x is not callable (Int) "
                                            "(line 1, col 7)")
        # Interpreter parity for the same environment shape.
        interp = G.Interpreter()
        interp.env.set("x", 5)
        with self.assertRaises(G.GamaRuntimeError) as want:
            interp.run(G.parse(src))
        self.assertEqual(str(want.exception), str(got.exception))

    def test_non_callable_expression(self):
        src = 'print (x)(1)\n'
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse(src), env={"x": 5})
        self.assertEqual(str(got.exception),
                         "expression is not callable (Int) (line 1, col 10)")

    def test_host_error_does_not_leak_raw_exception(self):
        def boom():
            raise KeyError("k")

        # The call site must not surface KeyError itself.
        try:
            IR.run_program(G.parse('boom()\n'), env={"boom": boom})
        except KeyError:  # pragma: no cover - regression guard
            self.fail("raw KeyError leaked from the IR")
        except IR.IRRuntimeError as exc:
            self.assertIn("KeyError", str(exc))


class HostCallableLimits(unittest.TestCase):
    def test_host_call_counts_towards_call_depth(self):
        def h(x):
            return x + 1

        src = (
            'fn a(n: Int) -> Int { return b(n) }\n'
            'fn b(n: Int) -> Int { return h(n) }\n'
            'print a(1)\n'
        )
        with _clean_env(GAMA_L_MAX_CALL_DEPTH="2"):
            with self.assertRaises(IR.IRRuntimeError) as got:
                IR.run_program(G.parse(src), env={"h": h})
        self.assertIn("call depth limit exceeded", str(got.exception))
        self.assertIn("GAMA_L_MAX_CALL_DEPTH=2", str(got.exception))

    def test_host_call_under_generous_depth_runs(self):
        def h(x):
            return x + 1

        src = (
            'fn a(n: Int) -> Int { return b(n) }\n'
            'fn b(n: Int) -> Int { return h(n) }\n'
            'print a(1)\n'
        )
        with _clean_env(GAMA_L_MAX_CALL_DEPTH="10"):
            res = IR.run_program(G.parse(src), env={"h": h})
        self.assertEqual(res.out, ["2"])

    def test_host_call_does_not_bypass_step_limit(self):
        calls = []

        def h():
            calls.append(1)
            return 0

        src = (
            'let i = 0\n'
            'while i < 1000 {\n'
            ' h()\n'
            ' i := i + 1\n'
            '}\n'
        )
        with _clean_env(GAMA_L_MAX_STEPS="5"):
            with self.assertRaises(IR.IRRuntimeError) as got:
                IR.run_program(G.parse(src), env={"h": h})
        self.assertIn("step limit exceeded", str(got.exception))
        self.assertTrue(calls, "host callable should run before the cap hits")

    def test_no_interpreter_is_instantiated(self):
        def host():
            return 7

        with mock.patch.object(
                G, "Interpreter",
                side_effect=AssertionError("interpreter used")):
            res = IR.run_program(G.parse('print host()\n'), env={"host": host})
        self.assertEqual(res.out, ["7"])


class HostCallableFilesAndParity(unittest.TestCase):
    def test_run_file_with_host_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "host.gm"
            path.write_text(
                'fn apply(f: Function, x: Int) -> Int { return f(x, 1) }\n'
                'print apply(add, 41)\n',
                encoding="utf-8")
            program = IR.compile_file(path, env={"add": add})
            res = IR.run_file(path, env={"add": add})
        self.assertEqual(res.out, ["42"])
        self.assertIsInstance(program, IR.Program)

    def test_interpreter_equivalent_ir_function(self):
        # The same observable result as a host call, expressed in the language:
        # this pins that host dispatch does not change normal call semantics.
        host_src = 'print add(2, 3)\n'
        ir_src = 'fn add(a: Int, b: Int) -> Int { return a + b }\n' \
                 'print add(2, 3)\n'
        expected = G.run(ir_src).out
        got = IR.run_program(G.parse(host_src), env={"add": add}).out
        self.assertEqual(got, expected)

    def test_frame_env_seed(self):
        frame = IR.Frame()
        frame.define("add", add)
        res = IR.run_program(G.parse('print add(4, 6)\n'), env=frame)
        self.assertEqual(res.out, ["10"])


if __name__ == "__main__":
    unittest.main()
