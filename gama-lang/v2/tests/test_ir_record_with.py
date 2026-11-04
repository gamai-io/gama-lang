"""IR-vs-interpreter parity for record copy/``with`` updates.

Semantics under test (mirrored from ``tests/test_record_with.py``):

* ``p with { x = 3 }`` evaluates to a *new* value of the receiver's nominal
  type with declaration-ordered fields; the receiver and its aliases stay
  unchanged.
* Listed fields are evaluated, in source order, after the receiver expression,
  so ``p with { x = p.y, y = p.x }`` reads the original values.
* Copies are shallow: the top-level field mapping is new, but unchanged nested
  record/container values are shared with the receiver; a replaced field is
  independent.
* Unknown/duplicate update fields and non-record receivers raise
  ``IRRuntimeError`` with the interpreter's wording when the AST is unchecked.
* Namespaced records (``geo.Point`` / ``geo.origin``) work through the core
  loader's flattened aliased imports.
* In-place field assignment ``p.x := 3`` stays out of the IR subset and is
  rejected explicitly (``IRUnsupported``), never silently shadowed.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


class IRRecordWithMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)
        return got

    def assert_same_unchecked(self, src):
        expected = G.Interpreter().run(G.parse(src)).out
        got = IR.run_program(G.parse(src)).out
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

    # -- single / multiple / empty updates -------------------------------
    def test_single_field_update(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let p: Point = Point(1, 2)\n'
            'let q: Point = p with { x = 3 }\n'
            'print p\nprint q\nprint q.y\n')

    def test_multiple_fields_and_declaration_order(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p with { y = 4, x = 3 }\n'
            'print q\n'
            'print q == Point(3, 4)\n'
            'print q != p\n')

    def test_empty_update_returns_a_new_equal_value(self):
        src = (
            'record P { x: Int }\n'
            'let p = P(1)\n'
            'let q = p with { }\n'
            'print p\nprint q\nprint p == q\n')
        got = self.assert_same(src)
        self.assertEqual(got, ["P { x: 1 }", "P { x: 1 }", "True"])
        result = IR.run_program(G.check(src))
        self.assertIsNot(result.globals["q"], result.globals["p"])
        self.assertEqual(result.globals["q"], result.globals["p"])

    def test_updated_fields_read_the_receiver_before_copy(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p with { x = p.y, y = p.x }\n'
            'print q\nprint p\n')

    def test_receiver_and_aliases_are_unchanged(self):
        src = (
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let alias = p\n'
            'let q = p with { x = 3 }\n'
            'print p\nprint alias\nprint q\nprint p == alias\nprint p == q\n')
        got = self.assert_same(src)
        self.assertEqual(
            got,
            ["Point { x: 1, y: 2 }", "Point { x: 1, y: 2 }",
             "Point { x: 3, y: 2 }", "True", "False"])
        result = IR.run_program(G.check(src))
        self.assertIs(result.globals["alias"], result.globals["p"])
        self.assertIsNot(result.globals["q"], result.globals["p"])

    # -- nested / shallow ------------------------------------------------
    def test_nested_values_are_shallow_copied(self):
        src = (
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l = Line(Point(1, 2), Point(3, 4))\n'
            'let m = l with { b = Point(9, 9) }\n'
            'print m\nprint l\n')
        self.assert_same(src)
        result = IR.run_program(G.check(src))
        l = result.globals["l"]
        m = result.globals["m"]
        # Unchanged nested record shared; replaced field independent.
        self.assertIs(m.fields["a"], l.fields["a"])
        self.assertIsNot(m.fields["b"], l.fields["b"])

    def test_nested_record_field_update_receiver(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l = Line(Point(1, 2), Point(3, 4))\n'
            'let q = l.a with { x = 9 }\n'
            'print q\nprint l.a\n')

    def test_named_record_literal_receiver(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let q = Point { x: 5, y: 6 } with { x = 7 }\n'
            'print q\n')

    def test_update_inside_blocks_and_functions(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'fn bump(p: Point) -> Point { return p with { x = p.x + 1 } }\n'
            'let p = Point(1, 2)\n'
            'let i = 0\n'
            'while i < 2 {\n'
            '  let step = bump(p)\n'
            '  print step\n'
            '  i := i + 1\n'
            '}\n')

    # -- runtime validation on unchecked ASTs ----------------------------
    def test_non_record_receiver_runtime_error(self):
        msg = self.assert_same_runtime_error(
            'let n = 5\n'
            'let q = n with { x = 2 }\n')
        self.assertIn("with expects a record value", msg)

    def test_unknown_field_runtime_error(self):
        msg = self.assert_same_runtime_error(
            'record P { x: Int }\n'
            'let p = P(1)\n'
            'let q = p with { z = 2 }\n')
        self.assertIn("P: unknown field z in with", msg)

    def test_duplicate_field_runtime_error(self):
        msg = self.assert_same_runtime_error(
            'record P { x: Int }\n'
            'let p = P(1)\n'
            'let q = p with { x = 2, x = 3 }\n')
        self.assertIn("P: duplicate field x in with", msg)

    def test_unknown_record_type_runtime_error(self):
        # A hand-seeded record whose nominal type was never declared: both the
        # IR and the interpreter must refuse it instead of guessing fields.
        foreign = G.RecordVal("Ghost", {"x": 1})
        interp = G.Interpreter()
        interp.env.set("g", foreign)
        with self.assertRaises(G.GamaRuntimeError) as want:
            interp.run(G.parse('print g with { x = 2 }\n'))
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse('print g with { x = 2 }\n'),
                           env={"g": foreign})
        self.assertEqual(str(got.exception), str(want.exception))
        self.assertIn("unknown record Ghost", str(got.exception))

    # -- explicit limits --------------------------------------------------
    def test_in_place_field_assignment_stays_unsupported(self):
        for src in (
                'record Point { x: Int, y: Int }\n'
                'let p = Point(1, 2)\n'
                'p.x := 3\n',
                'record Point { x: Int, y: Int }\n'
                'record Line { a: Point, b: Point }\n'
                'let l = Line(Point(1, 2), Point(3, 4))\n'
                'l.a.x := 5\n',
        ):
            with self.assertRaisesRegex(
                    IR.IRUnsupported, r"record field assignment"):
                IR.compile_program(G.parse(src))


class IRRecordWithNamespaced(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, src):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        return p

    def test_namespaced_record_and_constant_updates(self):
        self.write(
            "geo.gm",
            'export record Point { x: Int, y: Int }\n'
            'export fn bump(p: Point) -> Point {\n'
            '  return p with { x = p.x + 1 }\n'
            '}\n'
            'export let origin: Point = Point(0, 0)\n')
        main = self.write(
            "main.gm",
            'import "geo.gm" as geo\n'
            'let p: geo.Point = geo.Point(1, 2)\n'
            'let q: geo.Point = p with { x = 3 }\n'
            'print p\nprint q\nprint q.y\n'
            'let b = geo.bump(p)\nprint b\n'
            'let r = geo.origin with { y = 9 }\nprint r\n'
            'print r == geo.Point(0, 9)\n')
        ref = G.run_file(str(main)).out
        got = IR.run_file(str(main)).out
        self.assertEqual(got, ref)
        self.assertEqual(
            got,
            ["geo.Point { x: 1, y: 2 }", "geo.Point { x: 3, y: 2 }", "2",
             "geo.Point { x: 2, y: 2 }", "geo.Point { x: 0, y: 9 }", "True"])

    def test_namespaced_update_keeps_the_namespaced_nominal_type(self):
        self.write(
            "geo.gm",
            'export record Point { x: Int, y: Int }\n'
            'export let origin: Point = Point(0, 0)\n')
        main = self.write(
            "main.gm",
            'import "geo.gm" as geo\n'
            'let r = geo.origin with { x = 1 }\n'
            'print r\n')
        result = IR.run_program(IR.compile_file(str(main)))
        self.assertEqual(result.out, ["geo.Point { x: 1, y: 0 }"])
        self.assertEqual(result.globals["r"].type_name, "geo.Point")


if __name__ == "__main__":
    unittest.main()
