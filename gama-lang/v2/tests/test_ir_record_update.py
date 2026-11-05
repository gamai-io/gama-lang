"""IR-vs-interpreter parity for in-place record field assignment.

Semantics under test (mirrored from ``tests/test_record_update.py``):

* ``p.x := value`` mutates the receiver :class:`gamal2.RecordVal` in place, so
  reads, printing and equality reflect the new value immediately.
* Nested targets (``l.a.x := value``) descend through record fields and mutate
  the innermost record.
* Aliases observe the update because no copy-on-write is performed.
* The value expression is evaluated *before* the receiver is resolved, matching
  ``Interpreter.stmt`` (a value that rebinds the receiver changes the target).
* Namespaced records (``geo.origin.x := 7`` and ``p: geo.Point``) work through
  the core loader's flattened aliased imports.
* Runtime failures on unchecked ASTs (non-record receiver, unknown field,
  unknown base variable) raise ``IRRuntimeError`` with the interpreter's exact
  message, including inside loops and functions.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


class IRRecordUpdateMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)
        return got

    def assert_same_runtime_error(self, src):
        interp = G.Interpreter()
        with self.assertRaises(G.GamaRuntimeError) as want:
            interp.run(G.parse(src))
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(want.exception))
        return str(got.exception)

    # -- simple / nested / alias -----------------------------------------
    def test_simple_update_and_readback(self):
        got = self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'p.x := 3\n'
            'print p.x\nprint p.y\nprint p\n'
            'print p == Point(3, 2)\n')
        self.assertEqual(got, ["3", "2", "Point { x: 3, y: 2 }", "True"])

    def test_nested_update(self):
        got = self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l = Line(Point(1, 2), Point(3, 4))\n'
            'l.a.x := 5\n'
            'print l.a.x\nprint l.b.y\nprint l\n'
            'print l == Line(Point(5, 2), Point(3, 4))\n')
        self.assertEqual(
            got,
            ["5", "4",
             "Line { a: Point { x: 5, y: 2 }, b: Point { x: 3, y: 4 } }",
             "True"])

    def test_alias_observes_update(self):
        # Both a flat alias and a nested alias share the mutated record.
        got = self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let p = Point(1, 2)\n'
            'let alias = p\n'
            'alias.x := 9\n'
            'let l = Line(Point(1, 2), Point(3, 4))\n'
            'let la = l.a\n'
            'l.a.y := 8\n'
            'print p\nprint alias\nprint p == alias\n'
            'print la\nprint la.y\n')
        self.assertEqual(
            got,
            ["Point { x: 9, y: 2 }", "Point { x: 9, y: 2 }", "True",
             "Point { x: 1, y: 8 }", "8"])

    def test_value_is_evaluated_before_receiver_resolution(self):
        # ``rebind`` changes the global ``p`` to point at ``q``; because the
        # value is evaluated first, the assignment then mutates the *new* p
        # (i.e. q), exactly like the interpreter.
        got = self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = Point(9, 9)\n'
            'fn rebind() -> Int {\n'
            '  p := q\n'
            '  return 0\n'
            '}\n'
            'p.x := rebind()\n'
            'print p.x\nprint q.x\nprint p\nprint q\n')
        self.assertEqual(
            got,
            ["0", "0", "Point { x: 0, y: 9 }", "Point { x: 0, y: 9 }"])

    # -- namespaced records ----------------------------------------------
    def _write(self, root, name, text):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_namespaced_constant_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(
                root, "geo.gm",
                'record Point { x: Int, y: Int }\n'
                'let origin: Point = Point(0, 0)\n')
            main = self._write(
                root, "main.gm",
                'import "geo.gm" as geo\n'
                'geo.origin.x := 7\n'
                'print geo.origin\n'
                'print geo.origin.x\n')
            self.assertEqual(
                IR.run_file(str(main)).out,
                G.run_file(str(main)).out,
            )
            self.assertEqual(
                IR.run_file(str(main)).out,
                ["geo.Point { x: 7, y: 0 }", "7"])

    def test_namespaced_typed_variable_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(
                root, "geo.gm",
                'record Point { x: Int, y: Int }\n')
            main = self._write(
                root, "main.gm",
                'import "geo.gm" as geo\n'
                'let p: geo.Point = geo.Point(1, 2)\n'
                'p.y := 9\n'
                'print p\n'
                'print p == geo.Point(1, 9)\n')
            result = IR.run_program(IR.compile_file(str(main)))
            self.assertEqual(result.out, G.run_file(str(main)).out)
            self.assertEqual(
                result.out,
                ["geo.Point { x: 1, y: 9 }", "True"])
            self.assertEqual(result.globals["p"].type_name, "geo.Point")

    # -- runtime validation on unchecked ASTs ----------------------------
    def test_non_record_receiver_runtime_error(self):
        msg = self.assert_same_runtime_error(
            'let n = 5\n'
            'n.x := 1\n')
        self.assertIn("cannot assign field 'x' on non-record value", msg)

    def test_nested_non_record_receiver_runtime_error(self):
        msg = self.assert_same_runtime_error(
            'record Point { x: Int }\n'
            'let p = Point(1)\n'
            'p.x.y := 1\n')
        # ``p.x`` resolves to an Int, so the *final* receiver (``y``) is the
        # non-record one — the interpreter reports the last path segment.
        self.assertIn("cannot assign field 'y' on non-record value", msg)

    def test_unknown_field_runtime_error(self):
        msg = self.assert_same_runtime_error(
            'record Point { x: Int }\n'
            'let p = Point(1)\n'
            'p.z := 2\n')
        self.assertIn("unknown field p.z", msg)

    def test_unknown_base_variable_runtime_error(self):
        msg = self.assert_same_runtime_error('q.x := 1\n')
        self.assertIn("unknown variable q in assignment target q.x", msg)

    def test_error_inside_loop(self):
        msg = self.assert_same_runtime_error(
            'record Point { x: Int }\n'
            'let p = Point(1)\n'
            'let i = 0\n'
            'while i < 1 {\n'
            '  p.z := i\n'
            '  i := i + 1\n'
            '}\n')
        self.assertIn("unknown field p.z", msg)

    def test_error_inside_function(self):
        msg = self.assert_same_runtime_error(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'fn bad() -> Int {\n'
            '  p.q := 1\n'
            '  return 0\n'
            '}\n'
            'bad()\n')
        self.assertIn("unknown field p.q", msg)


if __name__ == "__main__":
    unittest.main()
