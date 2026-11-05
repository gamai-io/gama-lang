"""Record static (associated) functions: parsing, checking, runtime, IR, fmt.

Semantics under test:

* A top-level ``fn RecordName.method(...)`` associates the function with a
  declared record.  Calls use ``RecordName.method(...)``; there is no receiver
  or ``self`` binding.
* Static functions live beside (and never replace) record value methods:
  ``p.method()`` keeps binding ``self`` while ``Point.method(...)`` is static.
* The checker requires the record to exist, rejects a static name that
  collides with a value method or field, enforces arity/types and rejects a
  ``self`` parameter.
* Namespaced records work, both directly (``record geo.Point { ... }``) and
  through an aliased import (``geo.Point.of``).
* The interpreter registers the callable under its dotted name and resolves
  static calls; the formatter round-trips the declaration; the bytecode IR
  compiles static calls so direct-call programs match the interpreter.
* First-class static function values follow the existing named-function rule:
  the interpreter supports ``let f = Point.of``; the IR does not support
  first-class function values in general.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


POINT_SRC = (
    'record Point { x: Int, y: Int }\n'
    'fn Point.origin() -> Point { return Point(0, 0) }\n'
    'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n')


class RecordStaticRun(unittest.TestCase):
    def run_out(self, src):
        return G.run(src).out

    def test_origin_and_of(self):
        self.assertEqual(
            self.run_out(
                POINT_SRC +
                'let a: Point = Point.origin()\n'
                'let b: Point = Point.of(5)\n'
                'print a\n'
                'print b\n'),
            ["Point { x: 0, y: 0 }", "Point { x: 5, y: 0 }"])

    def test_no_receiver_binding(self):
        self.assertEqual(
            self.run_out(
                'record Counter { n: Int }\n'
                'fn Counter.zero() -> Counter { return Counter(0) }\n'
                'fn Counter.bump() -> Counter { return Counter(1) }\n'
                'print Counter.zero()\n'
                'print Counter.bump()\n'),
            ["Counter { n: 0 }", "Counter { n: 1 }"])

    def test_static_calls_other_static(self):
        self.assertEqual(
            self.run_out(
                'record Point { x: Int, y: Int }\n'
                'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n'
                'fn Point.unit() -> Point { return Point.of(1) }\n'
                'print Point.unit()\n'),
            ["Point { x: 1, y: 0 }"])

    def test_static_and_value_methods_coexist(self):
        self.assertEqual(
            self.run_out(
                'record Point { x: Int, y: Int\n'
                '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
                '}\n'
                'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n'
                'let p = Point.of(2)\n'
                'let q = Point.of(3)\n'
                'print p.sum()\n'
                'print q.sum()\n'),
            ["2", "3"])

    def test_static_function_value_is_first_class_in_interpreter(self):
        # Named functions (including static ones) are already first-class in
        # the reference interpreter; this mirrors that rule.
        self.assertEqual(
            self.run_out(
                'record Point { x: Int, y: Int }\n'
                'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n'
                'let f = Point.of\n'
                'print f(9)\n'),
            ["Point { x: 9, y: 0 }"])


class RecordStaticChecker(unittest.TestCase):
    def assert_type_error(self, src, pattern):
        with self.assertRaisesRegex(G.GamaTypeError, pattern):
            G.check(src)

    def test_unknown_record(self):
        self.assert_type_error(
            'record Point { x: Int }\n'
            'fn Nope.of() -> Int { return 1 }\n',
            r"static function Nope\.of: unknown record Nope")

    def test_duplicate_static_function(self):
        self.assert_type_error(
            'record Point { x: Int }\n'
            'fn Point.of() -> Point { return Point(1) }\n'
            'fn Point.of() -> Point { return Point(2) }\n',
            r"duplicate function")

    def test_static_collides_with_value_method(self):
        self.assert_type_error(
            'record Point { x: Int\n'
            '  fn of(self: Point) -> Int { return self.x }\n'
            '}\n'
            'fn Point.of() -> Int { return 0 }\n',
            r"static function Point\.of collides with method Point\.of")

    def test_static_collides_with_field(self):
        self.assert_type_error(
            'record Point { x: Int }\n'
            'fn Point.x() -> Int { return 0 }\n',
            r"static function Point\.x collides with field Point\.x")

    def test_static_rejects_self_parameter(self):
        self.assert_type_error(
            'record Point { x: Int }\n'
            'fn Point.of(self: Point) -> Int { return self.x }\n',
            r"static function Point\.of must not declare self")

    def test_method_name_must_be_simple(self):
        self.assert_type_error(
            'record Point { x: Int }\n'
            'fn Point.a.b() -> Int { return 0 }\n',
            r"static function Point\.a\.b: method name must be a simple "
            r"identifier")

    def test_wrong_arity(self):
        self.assert_type_error(
            POINT_SRC + 'let p = Point.of()\n',
            r"Point\.of expects 1\.\.1 args")
        self.assert_type_error(
            POINT_SRC + 'let p = Point.of(1, 2)\n',
            r"Point\.of expects 1\.\.1 args")

    def test_wrong_argument_type(self):
        self.assert_type_error(
            POINT_SRC + 'let p = Point.of("x")\n',
            r"Point\.of: x Text != Int")

    def test_unknown_static_function(self):
        self.assert_type_error(
            POINT_SRC + 'let p = Point.nope()\n',
            r"unknown function Point\.nope")

    def test_value_method_still_checked_when_no_static_exists(self):
        self.assert_type_error(
            'record Point { x: Int }\n'
            'let p = Point(1)\n'
            'print p.nope()\n',
            r"unknown method 'nope' on Point")


class RecordStaticNamespaced(unittest.TestCase):
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

    def test_direct_namespaced_record(self):
        got = G.run(
            'record geo.Point { x: Int, y: Int }\n'
            'fn geo.Point.of(x: Int) -> geo.Point {\n'
            '  return geo.Point(x, 0)\n'
            '}\n'
            'let p: geo.Point = geo.Point.of(3)\n'
            'print p\n').out
        self.assertEqual(got, ["geo.Point { x: 3, y: 0 }"])

    def test_aliased_import_namespaced_record(self):
        self.write(
            "lib.gm",
            'record Point { x: Int, y: Int }\n'
            'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n')
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'let p: geo.Point = geo.Point.of(4)\n'
            'print p\n')
        self.assertEqual(G.run_file(str(main)).out,
                         ["geo.Point { x: 4, y: 0 }"])

    def test_aliased_plain_function_is_not_a_static_function(self):
        self.write("lib.gm", "fn add(a: Int, b: Int) -> Int { return a + b }\n")
        main = self.write("main.gm", 'import "lib.gm" as math\nprint math.add(2, 3)\n')
        self.assertEqual(G.run_file(str(main)).out, ["5"])

    def test_exported_static_function_is_reachable_through_alias(self):
        self.write(
            "lib.gm",
            'export record Point { x: Int, y: Int }\n'
            'export fn Point.of(x: Int) -> Point { return Point(x, 0) }\n')
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'print geo.Point.of(7)\n')
        self.assertEqual(G.run_file(str(main)).out,
                         ["geo.Point { x: 7, y: 0 }"])

    def test_private_static_on_exported_record_stays_callable_inside(self):
        self.write(
            "lib.gm",
            'export record Point { x: Int, y: Int }\n'
            'fn Point.origin() -> Point { return Point(0, 0) }\n'
            'export fn build() -> Point { return Point.origin() }\n')
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'print geo.build()\n')
        self.assertEqual(G.run_file(str(main)).out,
                         ["geo.Point { x: 0, y: 0 }"])


class RecordStaticFormatter(unittest.TestCase):
    def test_formatter_round_trips_static_declarations(self):
        src = (
            'record Point { x: Int, y: Int }\n'
            'fn Point.origin() -> Point { return Point(0, 0) }\n'
            'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n'
            'let p = Point.of(6)\n'
            'print p\n')
        formatted = G.fmt_source(src)
        self.assertIn("fn Point.origin() -> Point", formatted)
        self.assertIn("fn Point.of(x: Int) -> Point", formatted)
        self.assertEqual(G.fmt_source(formatted), formatted)
        self.assertEqual(G.run(formatted).out, ["Point { x: 6, y: 0 }"])


class RecordStaticIR(unittest.TestCase):
    def assert_parity(self, src):
        ref = G.run(src)
        got = IR.run_program(G.check(src))
        self.assertEqual(got.out, ref.out)
        return ref.out

    def test_static_calls_match_interpreter(self):
        out = self.assert_parity(
            POINT_SRC +
            'let a = Point.origin()\n'
            'let b = Point.of(5)\n'
            'print a\n'
            'print b\n')
        self.assertEqual(out, ["Point { x: 0, y: 0 }", "Point { x: 5, y: 0 }"])

    def test_direct_namespaced_static_calls_match_interpreter(self):
        out = self.assert_parity(
            'record geo.Point { x: Int, y: Int }\n'
            'fn geo.Point.of(x: Int) -> geo.Point {\n'
            '  return geo.Point(x, 0)\n'
            '}\n'
            'let p = geo.Point.of(3)\n'
            'print p\n')
        self.assertEqual(out, ["geo.Point { x: 3, y: 0 }"])

    def test_aliased_static_calls_match_interpreter(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "lib.gm").write_text(
                'record Point { x: Int, y: Int }\n'
                'fn Point.of(x: Int) -> Point { return Point(x, 0) }\n',
                encoding="utf-8")
            main = root / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let p: geo.Point = geo.Point.of(4)\n'
                'print p\n', encoding="utf-8")
            ref = G.run_file(str(main))
            got = IR.run_file(str(main))
            self.assertEqual(got.out, ref.out)
            self.assertEqual(got.out, ["geo.Point { x: 4, y: 0 }"])


if __name__ == "__main__":
    unittest.main()
