"""IR-vs-interpreter comparison tests for pipe expressions.

``gama_ir`` compiles the six documented stages (``filter``, ``select``,
``sum``, ``limit``, ``count``, ``map``) into a single ``PIPE`` instruction whose
filter/limit sub-expressions are compiled to their own ``Code`` objects and run
with the current row bound.  Nothing here instantiates the interpreter at IR
run time; the reference interpreter is only used to produce expected values and
messages.

The reference checker only permits column stages on ``Table``/``Any`` values.
A runtime list of dicts is a valid table, so tests that exercise column stages
use parsed (unchecked) source -- the same approach ``test_ir_subset`` already
uses for runtime-error parity.  Tests on ``List``/``Text`` bases that the
checker accepts use the normal checked path.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from decimal import InvalidOperation

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"


class IRPipeMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected, src)

    def assert_same_unchecked(self, src):
        expected = G.Interpreter().run(G.parse(src)).out
        got = IR.run_program(G.parse(src)).out
        self.assertEqual(got, expected, src)

    def assert_same_runtime_error(self, src):
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(G.GamaError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(ref.exception), src)
        self.assertIsInstance(got.exception, IR.IRError)

    # -- checked list/text bases -----------------------------------------
    def test_list_count(self):
        self.assert_same('print [1, 2, 3, 4] | count\n')

    def test_list_filter_constant(self):
        self.assert_same('print [1, 2, 3, 4] | filter true | count\n')
        self.assert_same('print [1, 2, 3, 4] | filter false | count\n')

    def test_list_limit_and_chained(self):
        self.assert_same('print [1, 2, 3, 4] | limit 2 | count\n')
        self.assert_same('let xs = [1, 2, 3] | limit 2\nprint xs\n')

    def test_text_count_limit(self):
        # The checker rejects Text pipe bases ("pipe needs List/Table"); the
        # reference runtime still handles count/limit over text, so compare
        # through parsed (unchecked) source.
        self.assert_same_unchecked('print "abcd" | count\n')
        self.assert_same_unchecked('print "abcd" | limit 2\n')
        self.assert_same_unchecked('print "abcd" | filter true | count\n')

    def test_count_is_int_and_chains(self):
        self.assert_same('print ([1, 2, 3] | count) + 1\n')

    def test_pipe_inside_function_and_loop(self):
        self.assert_same(
            'fn f(a: Int) -> Int { return ([1, 2, 3] | count) + a }\n'
            'print f(10)\n')
        self.assert_same(
            'let total = 0\n'
            'for x in [1, 2] { total := total + ([x] | count) }\n'
            'print total\n')

    # -- table-shaped rows (unchecked: runtime list of dicts) -------------
    def test_filter_select_sum(self):
        self.assert_same_unchecked(
            'print [{"n": 1}, {"n": 2}, {"n": 3}]'
            ' | filter n >= 2 | select n | sum n\n')

    def test_map_and_count(self):
        self.assert_same_unchecked(
            'print [{"n": 1}, {"n": 2}] | map n\n')
        self.assert_same_unchecked(
            'print [{"n": 1}, {"n": 2}, {"n": 3}] | map n | count\n')

    def test_chained_stages_preserve_order(self):
        self.assert_same_unchecked(
            'print [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}]'
            ' | filter n > 1 | limit 2 | map n\n')
        self.assert_same_unchecked(
            'print [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}]'
            ' | filter n > 1 | limit 2 | select n | sum n\n')

    def test_select_then_sum(self):
        self.assert_same_unchecked(
            'print [{"a": 1, "b": 10}, {"a": 2, "b": 20}]'
            ' | select a, b | sum a\n')

    def test_filter_uses_truthiness(self):
        self.assert_same_unchecked(
            'print [{"b": true}, {"b": false}, {"b": 0}]'
            ' | filter b | count\n')

    def test_row_shadows_outer_binding(self):
        self.assert_same_unchecked(
            'let n = 100\nprint [{"n": 1}] | filter n == 1 | count\n')

    def test_filter_reads_outer_binding(self):
        self.assert_same_unchecked(
            'let floor = 2\n'
            'print [{"n": 1}, {"n": 2}, {"n": 3}] | filter n >= floor | count\n')

    def test_filter_calls_user_function(self):
        self.assert_same_unchecked(
            'fn keep(v: Int) -> Bool { return v >= 2 }\n'
            'print [{"n": 1}, {"n": 2}, {"n": 3}] | filter keep(n) | count\n')

    def test_decimal_sum(self):
        self.assert_same_unchecked(
            'print [{"v": 1.50}, {"v": 2.25}] | sum v\n')

    def test_sum_over_empty_list(self):
        self.assert_same_unchecked('print [] | sum v\n')

    # -- errors ----------------------------------------------------------
    def test_sum_missing_column(self):
        self.assert_same_runtime_error('print [{"a": 1}] | sum n\n')

    def test_map_missing_column(self):
        self.assert_same_runtime_error('print [{"a": 1}] | map n\n')

    def test_filter_unknown_variable(self):
        self.assert_same_runtime_error('print [{"a": 1}] | filter nope > 0\n')

    def test_limit_negative(self):
        self.assert_same_runtime_error('print [1, 2] | limit -1\n')

    def test_select_missing_column_leaks_key_error(self):
        # The interpreter does not catch ``select`` KeyError; the IR must not
        # wrap it either, or callers would see a different exception family.
        src = 'print [{"a": 1}] | select n\n'
        with self.assertRaises(KeyError):
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(KeyError):
            IR.run_program(G.parse(src))

    def test_sum_non_numeric_leaks_decimal_error(self):
        src = 'print [{"a": "x"}] | sum a\n'
        with self.assertRaises(InvalidOperation):
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(InvalidOperation):
            IR.run_program(G.parse(src))


class IRPipeUnsupported(unittest.TestCase):
    def test_unknown_stage_is_rejected_loudly(self):
        node = G.Pipe(G.Literal([1, 2], 1, 1), [("explode", [])], 1, 1)
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program([G.ExprStmt(node, 1, 1)])
        self.assertIn("IR does not support pipe stage", str(ctx.exception))

    def test_missing_stage_argument_is_rejected(self):
        node = G.Pipe(G.Literal([1, 2], 1, 1), [("filter", [])], 1, 1)
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program([G.ExprStmt(node, 1, 1)])
        self.assertIn("IR does not support pipe stage", str(ctx.exception))

    def test_result_propagation_inside_stage_is_rejected(self):
        # ``?`` in a filter/limit expression would change which frame unwinds;
        # the IR has no stage-level propagation, so it must fail loudly rather
        # than evaluate it with different semantics.
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(G.parse('print [{"r": Ok(1)}] | filter r? == 1\n'))
        self.assertIn("IR does not support Try", str(ctx.exception))


class IRPipeCompileFileAndCli(unittest.TestCase):
    SRC = ('let xs = [1, 2, 3, 4]\n'
           'print xs | filter true | limit 2 | count\n')

    def test_compile_file_lists_pipe_and_runs_like_interpreter(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "pipes.gm"
            path.write_text(self.SRC, encoding="utf-8")
            program = IR.compile_file(str(path))
            self.assertIn("PIPE", program.disassemble())
            self.assertEqual(
                IR.run_file(str(path)).out,
                G.run_file(str(path)).out)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(GAMAL2), *args],
            cwd=str(V2), capture_output=True, text=True, timeout=30,
        )

    def test_cli_ir_run_matches_default_run(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "pipes.gm"
            path.write_text(self.SRC, encoding="utf-8")
            ref = self.run_cli("run", str(path))
            got = self.run_cli("ir", "run", str(path))
            compiled = self.run_cli("ir", "compile", str(path))
        self.assertEqual(ref.returncode, 0, ref.stderr + ref.stdout)
        self.assertEqual(got.returncode, 0, got.stderr + got.stdout)
        self.assertEqual(got.stdout, ref.stdout)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertIn("PIPE", compiled.stdout)


if __name__ == '__main__':
    unittest.main()
