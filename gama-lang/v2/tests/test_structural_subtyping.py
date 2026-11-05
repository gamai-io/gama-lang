"""Structural record compatibility in the static checker.

Semantics under test:

* A record ``S`` is structurally compatible with a target record ``T`` when
  ``S`` declares every field of ``T`` with a compatible type.  Extra fields on
  ``S`` are allowed; a missing field or a wrong field type is rejected.
* The rule applies wherever the checker already uses nominal compatibility:
  ``let`` annotations, function arguments and returns, record field values
  (positional and named construction), ``with`` updates and field assignment.
* Field types recurse, so nested records compose, and cyclic record graphs are
  coinductive (a repeated pair is assumed compatible).
* Runtime stays nominal: values keep their record type for printing and
  equality, while field access and method dispatch use the static target type.
  ``Point3 == Point`` remains a static type error.
* The bytecode IR performs no type checking of its own: it accepts programs
  the checker allows and preserves the same field/method behavior at runtime.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


SRC = (
    'record Point { x: Int, y: Int }\n'
    'record Point3 { x: Int, y: Int, z: Int }\n'
    'fn take(p: Point) -> Int { return p.x + p.y }\n')


class StructuralAccepted(unittest.TestCase):
    def test_let_annotation_accepts_extra_fields(self):
        self.assertEqual(
            G.run(
                SRC +
                'let p3: Point3 = Point3(1, 2, 3)\n'
                'let p: Point = p3\n'
                'print p.x + p.y\n').out,
            ["3"])

    def test_function_argument_accepts_extra_fields(self):
        self.assertEqual(
            G.run(SRC + 'print take(Point3(4, 5, 6))\n').out,
            ["9"])

    def test_function_return_accepts_extra_fields(self):
        self.assertEqual(
            G.run(
                SRC +
                'fn build() -> Point { return Point3(7, 8, 9) }\n'
                'let p: Point = build()\n'
                'print p.x + p.y\n').out,
            ["15"])

    def test_positional_record_field_accepts_extra_fields(self):
        self.assertEqual(
            G.run(
                SRC +
                'record Holder { p: Point }\n'
                'let h = Holder(Point3(1, 2, 3))\n'
                'print h.p.x\n').out,
            ["1"])

    def test_named_record_field_accepts_extra_fields(self):
        self.assertEqual(
            G.run(
                SRC +
                'record Holder { p: Point }\n'
                'let h = Holder { p: Point3(2, 3, 4) }\n'
                'print h.p.y\n').out,
            ["3"])

    def test_with_update_accepts_extra_fields(self):
        self.assertEqual(
            G.run(
                SRC +
                'record Holder { p: Point }\n'
                'let h = Holder(Point(1, 2))\n'
                'let h2 = h with { p = Point3(5, 6, 7) }\n'
                'print h2.p.x\n').out,
            ["5"])

    def test_field_assignment_accepts_extra_fields(self):
        self.assertEqual(
            G.run(
                SRC +
                'record Holder { p: Point }\n'
                'let h = Holder(Point(1, 2))\n'
                'h.p := Point3(8, 9, 10)\n'
                'print h.p.x\n').out,
            ["8"])

    def test_nested_records_compose(self):
        self.assertEqual(
            G.run(
                'record Inner { n: Int }\n'
                'record Inner2 { n: Int, m: Int }\n'
                'record Outer { inner: Inner }\n'
                'record Outer2 { inner: Inner2 }\n'
                'let o: Outer = Outer2(Inner2(1, 2))\n'
                'print o.inner.n\n').out,
            ["1"])

    def test_extra_fields_keep_runtime_type(self):
        # The value is structurally accepted but still prints as Point3 and
        # still compares nominally.
        self.assertEqual(
            G.run(
                SRC +
                'let p3: Point3 = Point3(1, 2, 3)\n'
                'let p: Point = p3\n'
                'print p\n').out,
            ["Point3 { x: 1, y: 2, z: 3 }"])


class StructuralRejected(unittest.TestCase):
    def assert_type_error(self, src, pattern):
        with self.assertRaisesRegex(G.GamaTypeError, pattern):
            G.check(src)

    def test_missing_field_rejected_in_let(self):
        self.assert_type_error(
            'record Point { x: Int, y: Int }\n'
            'record OnlyX { x: Int }\n'
            'let p: Point = OnlyX(1)\n',
            r"p: OnlyX != Point")

    def test_wrong_field_type_rejected_in_let(self):
        self.assert_type_error(
            'record Point { x: Int, y: Int }\n'
            'record Bad { x: Text, y: Int }\n'
            'let p: Point = Bad("a", 1)\n',
            r"p: Bad != Point")

    def test_missing_field_rejected_in_argument(self):
        self.assert_type_error(
            'record Point { x: Int, y: Int }\n'
            'record OnlyX { x: Int }\n'
            'fn take(p: Point) -> Int { return p.x }\n'
            'let n = take(OnlyX(1))\n',
            r"take: p OnlyX != Point")

    def test_wrong_field_type_rejected_in_return(self):
        self.assert_type_error(
            'record Point { x: Int, y: Int }\n'
            'record Bad { x: Text, y: Int }\n'
            'fn build() -> Point { return Bad("a", 1) }\n',
            r"return Bad != Point")

    def test_missing_field_rejected_in_record_field(self):
        self.assert_type_error(
            'record Inner { n: Int }\n'
            'record BadInner { m: Int }\n'
            'record Outer { inner: Inner }\n'
            'let o = Outer(BadInner(1))\n',
            r"Outer\.inner: BadInner != Inner")

    def test_missing_nested_field_rejected(self):
        self.assert_type_error(
            'record Inner { n: Int }\n'
            'record InnerX { m: Int }\n'
            'record Outer { inner: Inner }\n'
            'record OuterX { inner: InnerX }\n'
            'let o: Outer = OuterX(InnerX(2))\n',
            r"o: OuterX != Outer")

    def test_unknown_target_rejected(self):
        self.assert_type_error(
            'record Point3 { x: Int, y: Int, z: Int }\n'
            'let p: Missing = Point3(1, 2, 3)\n',
            r"p: Point3 != Missing")

    def test_cyclic_records_are_coinductive_but_still_check_fields(self):
        self.assert_type_error(
            'record A { x: Int, a: A }\n'
            'record B { a: B }\n'
            'fn f(b: B) -> A { return b }\n',
            r"return B != A")

    def test_nominal_equality_unchanged(self):
        self.assert_type_error(
            'record Point { x: Int, y: Int }\n'
            'record Point3 { x: Int, y: Int, z: Int }\n'
            'let a: Point3 = Point3(1, 2, 3)\n'
            'let b: Point = a\n'
            'print a == b\n',
            r"== expects matching types")

    def test_unrelated_records_still_rejected(self):
        self.assert_type_error(
            'record Point { x: Int, y: Int }\n'
            'record Other { a: Int, b: Int }\n'
            'let p: Point = Other(1, 2)\n',
            r"p: Other != Point")


class StructuralMethods(unittest.TestCase):
    SUM = (
        'record Point { x: Int, y: Int\n'
        '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
        '}\n'
        'record Point3 { x: Int, y: Int, z: Int }\n')

    def test_method_dispatches_on_static_target(self):
        self.assertEqual(
            G.run(
                self.SUM +
                'let p3: Point3 = Point3(1, 2, 3)\n'
                'let p: Point = p3\n'
                'print p.sum()\n').out,
            ["3"])

    def test_method_on_parameter_uses_static_target(self):
        self.assertEqual(
            G.run(
                self.SUM +
                'fn takeSum(p: Point) -> Int { return p.sum() }\n'
                'print takeSum(Point3(4, 5, 6))\n').out,
            ["9"])

    def test_nominal_method_wins_for_nominal_binding(self):
        self.assertEqual(
            G.run(
                'record Point { x: Int, y: Int\n'
                '  fn label(self: Point) -> Text { return "point" }\n'
                '}\n'
                'record Point3 { x: Int, y: Int, z: Int\n'
                '  fn label(self: Point3) -> Text { return "point3" }\n'
                '}\n'
                'let p3: Point3 = Point3(1, 2, 3)\n'
                'let p: Point = p3\n'
                'print p.label()\n'
                'print p3.label()\n').out,
            ["point", "point3"])


class StructuralNamespaced(unittest.TestCase):
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

    def test_namespaced_records_are_structurally_compatible(self):
        self.write(
            "lib.gm",
            'export record Point { x: Int, y: Int }\n'
            'export record Point3 { x: Int, y: Int, z: Int }\n'
            'export fn take(p: Point) -> Int { return p.x + p.y }\n')
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'let p3: geo.Point3 = geo.Point3(1, 2, 3)\n'
            'let p: geo.Point = p3\n'
            'print p.x + p.y\n'
            'print geo.take(p3)\n')
        self.assertEqual(G.run_file(str(main)).out, ["3", "3"])

    def test_namespaced_missing_field_rejected(self):
        self.write(
            "lib.gm",
            'export record Point { x: Int, y: Int }\n'
            'export record OnlyX { x: Int }\n')
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'let p: geo.Point = geo.OnlyX(1)\n')
        with self.assertRaisesRegex(
                G.GamaTypeError, r"p: geo\.OnlyX != geo\.Point"):
            G.check_file(str(main))


class StructuralIR(unittest.TestCase):
    def assert_parity(self, src):
        ref = G.run(src)
        got = IR.run_program(G.check(src))
        self.assertEqual(got.out, ref.out)
        return ref.out

    def test_let_and_field_access_parity(self):
        self.assertEqual(
            self.assert_parity(
                SRC +
                'let p3: Point3 = Point3(1, 2, 3)\n'
                'let p: Point = p3\n'
                'print p.x + p.y\n'),
            ["3"])

    def test_function_argument_parity(self):
        self.assertEqual(
            self.assert_parity(SRC + 'print take(Point3(4, 5, 6))\n'),
            ["9"])

    def test_method_dispatch_parity(self):
        self.assertEqual(
            self.assert_parity(
                'record Point { x: Int, y: Int\n'
                '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
                '}\n'
                'record Point3 { x: Int, y: Int, z: Int }\n'
                'let p3: Point3 = Point3(1, 2, 3)\n'
                'let p: Point = p3\n'
                'print p.sum()\n'),
            ["3"])


if __name__ == "__main__":
    unittest.main()
