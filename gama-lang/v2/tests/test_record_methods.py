"""Record methods: parsing, checking, binding and IR parity.

Semantics under test:

* A record body may contain fields and ``fn`` methods.  Every method must
  declare ``self`` as its first parameter and its type must be the record.
* ``p.m(args)`` binds ``self`` to the receiver.  Methods can read fields, call
  other methods of the same nominal record, mutate the receiver in place and
  recurse through ``self.m(...)``.
* Method lookup is per nominal record: another record with the same field
  layout does not gain the method.
* The checker owns the method table and reports unknown methods, wrong arity,
  wrong argument types, bad ``self`` declarations and duplicate method names
  as ``GamaTypeError``.
* Methods on namespaced records (``geo.Point`` via an aliased import) work.
* The bytecode IR compiles record declarations that carry methods and produces
  the same observable behaviour as the interpreter.
* The formatter round-trips method declarations.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


class RecordMethodsRun(unittest.TestCase):
    def run_out(self, src):
        return G.run(src).out

    def test_method_call_binds_receiver(self):
        self.assertEqual(
            self.run_out(
                'record Point {\n'
                '  x: Int\n'
                '  y: Int\n'
                '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
                '}\n'
                'let p: Point = Point(1, 2)\n'
                'print p.sum()\n'),
            ["3"])

    def test_method_reads_fields(self):
        self.assertEqual(
            self.run_out(
                'record Point {\n'
                '  x: Int\n'
                '  y: Int\n'
                '  fn scaled(self: Point, k: Int) -> Int { return self.x * k + self.y }\n'
                '}\n'
                'let p = Point(3, 4)\n'
                'print p.scaled(2)\n'),
            ["10"])

    def test_method_calls_other_methods(self):
        got = self.run_out(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '  fn total(self: Point) -> Int { return self.sum() + 1 }\n'
            '}\n'
            'let p = Point(1, 2)\n'
            'print p.sum()\n'
            'print p.total()\n')
        self.assertEqual(got, ["3", "4"])

    def test_method_mutates_receiver_in_place(self):
        got = self.run_out(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn bump(self: Point) -> Point {\n'
            '    self.x := self.x + 1\n'
            '    return self\n'
            '  }\n'
            '}\n'
            'let p = Point(1, 2)\n'
            'print p.bump()\n'
            'print p\n'
            'print p.x\n')
        self.assertEqual(
            got, ["Point { x: 2, y: 2 }", "Point { x: 2, y: 2 }", "2"])

    def test_method_mutation_through_record_parameter(self):
        got = self.run_out(
            'record Point { x: Int }\n'
            'fn bump(p: Point) -> Int {\n'
            '  p.x := p.x + 5\n'
            '  return p.x\n'
            '}\n'
            'let p = Point(1)\n'
            'print bump(p)\n'
            'print p.x\n')
        self.assertEqual(got, ["6", "6"])

    def test_method_recursion_via_self(self):
        got = self.run_out(
            'record Math {\n'
            '  base: Int\n'
            '  fn fact(self: Math, n: Int) -> Int {\n'
            '    if n <= 1 { return 1 }\n'
            '    return n * self.fact(n - 1)\n'
            '  }\n'
            '}\n'
            'let m = Math(10)\n'
            'print m.fact(5)\n')
        self.assertEqual(got, ["120"])

    def test_nested_receiver_method_call(self):
        got = self.run_out(
            'record Point {\n'
            '  x: Int\n'
            '  fn sum(self: Point) -> Int { return self.x }\n'
            '}\n'
            'record Box { inner: Point }\n'
            'let b = Box(Point(7))\n'
            'print b.inner.sum()\n')
        self.assertEqual(got, ["7"])

    def test_method_default_argument(self):
        got = self.run_out(
            'record Point {\n'
            '  x: Int\n'
            '  fn plus(self: Point, n: Int = 2) -> Int { return self.x + n }\n'
            '}\n'
            'let p = Point(1)\n'
            'print p.plus()\n'
            'print p.plus(10)\n')
        self.assertEqual(got, ["3", "11"])

    def test_method_names_are_per_nominal_record(self):
        got = self.run_out(
            'record A { x: Int fn sum(self: A) -> Int { return self.x } }\n'
            'record B { x: Int }\n'
            'let a = A(5)\n'
            'print a.sum()\n')
        self.assertEqual(got, ["5"])
        with self.assertRaisesRegex(
                G.GamaTypeError, "unknown method 'sum' on B"):
            G.run(
                'record A { x: Int fn sum(self: A) -> Int { return self.x } }\n'
                'record B { x: Int }\n'
                'let b = B(5)\n'
                'print b.sum()\n')

    def test_method_on_record_without_fields(self):
        got = self.run_out(
            'record Unit {\n'
            '  fn one(self: Unit) -> Int { return 1 }\n'
            '}\n'
            'let u = Unit()\n'
            'print u.one()\n')
        self.assertEqual(got, ["1"])

    def test_constructor_and_literal_are_unchanged_by_methods(self):
        got = self.run_out(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '}\n'
            'let a = Point(1, 2)\n'
            'let b = Point { y: 4, x: 3 }\n'
            'print a.sum()\n'
            'print b.sum()\n'
            'print a == Point(1, 2)\n')
        self.assertEqual(got, ["3", "7", "True"])


class RecordMethodsChecker(unittest.TestCase):
    def assert_type_error(self, src, pattern):
        with self.assertRaisesRegex(G.GamaTypeError, pattern):
            G.check(src)

    def test_unknown_method(self):
        self.assert_type_error(
            'record Point { x: Int fn sum(self: Point) -> Int { return self.x } }\n'
            'let p = Point(1)\n'
            'print p.nope()\n',
            r"unknown method 'nope' on Point")

    def test_wrong_arity_too_few_and_too_many(self):
        self.assert_type_error(
            'record P { x: Int fn f(self: P, a: Int, b: Int) -> Int { return a + b } }\n'
            'let p = P(1)\n'
            'print p.f(1)\n',
            r"P\.f expects 2\.\.2 args, got 1")
        self.assert_type_error(
            'record P { x: Int fn f(self: P) -> Int { return self.x } }\n'
            'let p = P(1)\n'
            'print p.f(1)\n',
            r"P\.f expects 0\.\.0 args, got 1")

    def test_wrong_argument_type(self):
        self.assert_type_error(
            'record P { x: Int fn f(self: P, n: Int) -> Int { return self.x + n } }\n'
            'let p = P(1)\n'
            'print p.f("x")\n',
            r"P\.f: n Text != Int")

    def test_self_must_be_first_parameter(self):
        self.assert_type_error(
            'record P { x: Int fn f(p: P) -> Int { return 1 } }\n',
            r"method P\.f must declare self as its first parameter")

    def test_self_type_must_match_record(self):
        self.assert_type_error(
            'record P { x: Int fn f(self: Int) -> Int { return 1 } }\n',
            r"method P\.f: self type Int != P")

    def test_self_only_allowed_as_first_parameter(self):
        self.assert_type_error(
            'record P { x: Int fn f(self: P, self: Int) -> Int { return 1 } }\n',
            r"method P\.f: self is only allowed as the first parameter")

    def test_duplicate_method_name(self):
        self.assert_type_error(
            'record P {\n'
            '  x: Int\n'
            '  fn f(self: P) -> Int { return 1 }\n'
            '  fn f(self: P) -> Int { return 2 }\n'
            '}\n',
            r"record P: duplicate method f")

    def test_method_name_collides_with_field(self):
        self.assert_type_error(
            'record P { x: Int fn x(self: P) -> Int { return 1 } }\n',
            r"record P: method x collides with a field")

    def test_method_body_return_type_is_checked(self):
        self.assert_type_error(
            'record P { x: Int fn f(self: P) -> Int { return "no" } }\n',
            r"return Text != Int")

    def test_method_body_unknown_field(self):
        self.assert_type_error(
            'record P { x: Int fn f(self: P) -> Int { return self.z } }\n',
            r"unknown field 'z' on P")

    def test_unknown_method_on_self_inside_method(self):
        self.assert_type_error(
            'record P {\n'
            '  x: Int\n'
            '  fn f(self: P) -> Int { return self.missing() }\n'
            '}\n',
            r"unknown method 'missing' on P")

    def test_duplicate_methods_across_records_are_allowed(self):
        G.check(
            'record A { fn f(self: A) -> Int { return 1 } }\n'
            'record B { fn f(self: B) -> Int { return 2 } }\n')


class RecordMethodsNamespaced(unittest.TestCase):
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

    def test_namespaced_record_methods(self):
        self.write(
            "geo.gm",
            'export record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '  fn bump(self: Point) -> Point {\n'
            '    self.x := self.x + 1\n'
            '    return self\n'
            '  }\n'
            '}\n')
        main = self.write(
            "main.gm",
            'import "geo.gm" as geo\n'
            'let p: geo.Point = geo.Point(1, 2)\n'
            'print p.sum()\n'
            'print p.bump()\n'
            'print p.sum()\n')
        self.assertEqual(
            G.run_file(str(main)).out,
            ["3", "geo.Point { x: 2, y: 2 }", "4"])

    def test_namespaced_method_self_type_is_rewritten(self):
        self.write(
            "geo.gm",
            'export record Point {\n'
            '  x: Int\n'
            '  fn dup(self: Point) -> geo.Point { return Point(self.x) }\n'
            '}\n')
        main = self.write(
            "main.gm",
            'import "geo.gm" as geo\n'
            'let p = geo.Point(5)\n'
            'print p.dup()\n')
        self.assertEqual(G.run_file(str(main)).out, ["geo.Point { x: 5 }"])


class RecordMethodsFormatter(unittest.TestCase):
    def test_formatter_round_trips_method_declarations(self):
        src = (
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '}\n'
            'let p = Point(1, 2)\n'
            'print p.sum()\n')
        formatted = G.fmt_source(src)
        self.assertIn("fn sum(self: Point) -> Int", formatted)
        self.assertEqual(G.fmt_source(formatted), formatted)
        self.assertEqual(G.run(formatted).out, ["3"])

    def test_formatter_expands_multiline_method_body(self):
        src = (
            'record M {\n'
            '  n: Int\n'
            '  fn fact(self: M, k: Int) -> Int { if k <= 1 { return 1 } '
            'return k * self.fact(k - 1) }\n'
            '}\n'
            'let m = M(0)\n'
            'print m.fact(3)\n')
        formatted = G.fmt_source(src)
        self.assertEqual(G.fmt_source(formatted), formatted)
        self.assertEqual(G.run(formatted).out, ["6"])


class RecordMethodsIR(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def test_ir_runs_record_methods(self):
        self.assert_same(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '}\n'
            'let p = Point(1, 2)\n'
            'print p.sum()\n')

    def test_ir_registers_methods_before_their_declaration_runs(self):
        # The interpreter registers top-level records (and their methods) up
        # front, so a call before the declaration statement still works.
        self.assert_same(
            'let p = Point(2, 3)\n'
            'print p.sum()\n'
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '}\n')


if __name__ == "__main__":
    unittest.main()
