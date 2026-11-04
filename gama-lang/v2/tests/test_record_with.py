"""Record copy/update syntax: ``receiver with { field = expr, ... }``.

Semantics under test:

* ``p with { x = 3 }`` evaluates to a *new* record of the receiver's nominal
  type; the receiver and every alias of it stay byte-for-byte unchanged.
* Fields that are not listed keep their value. Listed fields are evaluated in
  the receiver's scope before the copy is built, so ``p with { x = p.y }``
  reads the original value.
* Copies are **shallow**: the top-level field dict is new, but unchanged nested
  record/container values are shared with the receiver. Mutating a shared nested
  value through the copy is therefore visible through the receiver, while a
  field that was replaced by the update is independent.
* The checker enforces a record receiver, known fields, no duplicates and
  compatible value types (``GamaTypeError``); the IR backend rejects the
  construct with ``IRUnsupported`` instead of silently dropping updates.
* Namespaced records work through aliased imports when constructed with
  ``geo.Point(...)`` or when updating an exported constant; named construction
  with a dotted name (``geo.Point { ... }``) remains parser-unsupported.
"""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt


class RecordWithTests(unittest.TestCase):
    def test_single_field_update_copies_only_that_field(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p: Point = Point(1, 2)\n'
            'let q: Point = p with { x = 3 }\n'
            'print p.x\n'
            'print q.x\n'
            'print q.y\n'
        )
        self.assertEqual(it.out, ["1", "3", "2"])

    def test_multiple_fields_and_result_type(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q: Point = p with { x = 3, y = 4 }\n'
            'print q\n'
            'print q == Point(3, 4)\n'
        )
        self.assertEqual(it.out, ["Point { x: 3, y: 4 }", "True"])

    def test_receiver_and_aliases_are_unchanged(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let alias = p\n'
            'let q = p with { x = 3 }\n'
            'print p.x\n'
            'print alias.x\n'
            'print q.x\n'
            'print p == alias\n'
            'print p == q\n'
        )
        # No in-place mutation: the receiver and its alias keep x == 1.
        self.assertEqual(it.out, ["1", "1", "3", "True", "False"])

    def test_updated_fields_read_the_receiver_before_copy(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p with { x = p.y, y = p.x }\n'
            'print q\n'
            'print p\n'
        )
        self.assertEqual(
            it.out, ["Point { x: 2, y: 1 }", "Point { x: 1, y: 2 }"])

    def test_empty_update_still_returns_a_new_value(self):
        it = G.run(
            'record P { x: Int }\n'
            'let p = P(1)\n'
            'let q = p with { }\n'
            'q.x := 9\n'
            'print p.x\n'
            'print q.x\n'
            'print p == q\n'
        )
        # Equality is by value, but the copy is a distinct value: updating q
        # does not touch p.
        self.assertEqual(it.out, ["1", "9", "False"])

    def test_nested_values_are_shallow_copied(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l = Line(Point(1, 2), Point(3, 4))\n'
            'let m = l with { b = Point(9, 9) }\n'
            'm.a.x := 7\n'
            'print l.a.x\n'
            'print l.b.x\n'
            'print m.b.x\n'
            'let n = l with { a = Point(5, 5) }\n'
            'n.a.x := 8\n'
            'print l.a.x\n'
            'print n.a.x\n'
        )
        # Unchanged `a` is shared: mutating m.a is visible through l.a.
        # The replaced `b` is independent of l.b. A later copy that replaces
        # `a` gets its own nested record, so n.a.x does not reach l.a.
        self.assertEqual(it.out, ["7", "3", "9", "7", "8"])

    def test_field_order_follows_the_declaration(self):
        it = G.run(
            'record Money { amount: Decimal, label: Text }\n'
            'let m = Money(1.50, "net")\n'
            'let n = m with { label = "gross" }\n'
            'print n\n'
        )
        self.assertEqual(it.out, ['Money { amount: 1.50, label: "gross" }'])

    def test_named_record_literal_receiver(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let q = Point { x: 5, y: 6 } with { x = 7 }\n'
            'print q\n'
        )
        self.assertEqual(it.out, ["Point { x: 7, y: 6 }"])

    def test_blocks_and_record_literals_stay_unambiguous(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let total = 0\n'
            'let i = 0\n'
            'while i < 2 {\n'
            '  let step = p with { x = i }\n'
            '  total := total + step.x\n'
            '  i := i + 1\n'
            '}\n'
            'if total == 1 {\n'
            '  let q = p with { y = 9 }\n'
            '  print q\n'
            '}\n'
            'print total\n'
        )
        self.assertEqual(it.out, ["Point { x: 1, y: 9 }", "1"])

    def test_wrong_field_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"P: unknown field z in with"):
            G.run(
                'record P { x: Int }\n'
                'let p = P(1)\n'
                'let q = p with { z = 2 }\n'
            )

    def test_wrong_value_type_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"P\.x: Text != Int in with"):
            G.run(
                'record P { x: Int }\n'
                'let p = P(1)\n'
                'let q = p with { x = "bad" }\n'
            )

    def test_duplicate_field_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"P: duplicate field x in with"):
            G.run(
                'record P { x: Int }\n'
                'let p = P(1)\n'
                'let q = p with { x = 2, x = 3 }\n'
            )

    def test_non_record_receiver_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"with expects a record receiver, got Int"):
            G.run(
                'let n = 5\n'
                'let q = n with { x = 2 }\n'
            )

    def _write(self, root, name, text):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_namespaced_record_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(
                root, "geo.gm",
                'export record Point { x: Int, y: Int }\n'
                'export fn bump(p: Point) -> Point {\n'
                '  return p with { x = p.x + 1 }\n'
                '}\n'
                'export let origin: Point = Point(0, 0)\n')
            self._write(
                root, "main.gm",
                'import "geo.gm" as geo\n'
                'let p: geo.Point = geo.Point(1, 2)\n'
                'let q: geo.Point = p with { x = 3 }\n'
                'print p.x\n'
                'print q.x\n'
                'print q.y\n'
                'let b = geo.bump(p)\n'
                'print b.x\n'
                'let r = geo.origin with { y = 9 }\n'
                'print r\n')
            it = G.run_file(str(root / "main.gm"))
            self.assertEqual(
                it.out,
                ["1", "3", "2", "2", "geo.Point { x: 0, y: 9 }"])

    def test_formatter_canonical_short_update(self):
        src = (
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p with {x=3,y=4}\n'
            'print q\n')
        out = gama_fmt.format_source(src)
        self.assertEqual(
            out,
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p with { x = 3, y = 4 }\n'
            'print q\n')

    def test_formatter_wraps_long_update_and_preserves_semantics(self):
        src = (
            'record Point { x: Int, y: Int, z: Int }\n'
            'let p = Point(1, 2, 3)\n'
            'let q = p with { x = 111111111111111111, '
            'y = 222222222222222222, z = 333333333333333333 }\n'
            'print q\n')
        out = gama_fmt.format_source(src)
        self.assertEqual(
            out,
            'record Point { x: Int, y: Int, z: Int }\n'
            'let p = Point(1, 2, 3)\n'
            'let q = p with {\n'
            '  x = 111111111111111111,\n'
            '  y = 222222222222222222,\n'
            '  z = 333333333333333333\n'
            '}\n'
            'print q\n')
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertEqual(G.run(out).out, G.run(src).out)

    def test_ir_rejects_record_with(self):
        import gama_ir as IR
        ast = G.check(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p with { x = 3 }\n'
            'print q\n')
        with self.assertRaisesRegex(
                IR.IRUnsupported, r"record copy/with"):
            IR.compile_program(ast)
        with self.assertRaisesRegex(
                IR.IRUnsupported, r"record copy/with"):
            IR.run_program(ast)


if __name__ == "__main__":
    unittest.main()
