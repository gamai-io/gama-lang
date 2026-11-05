"""IR-vs-interpreter parity for record methods.

The reference interpreter binds the receiver to ``self``, evaluates the
arguments in the caller's frame, supports defaults, method-to-method calls,
recursion and in-place mutation.  The IR mirrors all of that: methods compile
to ordinary function prototypes (with ``self`` dropped from the caller-visible
parameters), records carry a per-nominal method table, and a dotted call on a
record receiver resolves after named functions and enum constructors, exactly
like ``Interpreter.method_call``.

Coverage here:

* basics: ``self`` binding, extra parameters, field reads;
* top-level records/methods are usable before their declaration statement runs;
* defaults (omitted and supplied);
* method-to-method calls and recursion through ``self.m(...)``;
* in-place mutation observed by aliases;
* methods returning records and first-class functions;
* nested receivers (``o.inner.m()``) and namespaced records (``geo.Point``
  through ``compile_file``);
* runtime errors on unchecked ASTs (unknown method, bad arity, non-record
  receiver) match the interpreter's message exactly.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


class IRRecordMethodsMatchInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)
        return got

    def assert_same_unchecked_error(self, src):
        interp = G.Interpreter()
        with self.assertRaises(G.GamaRuntimeError) as want:
            interp.run(G.parse(src))
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(want.exception))
        return str(got.exception)

    # -- basics ----------------------------------------------------------
    def test_self_binding_and_field_reads(self):
        self.assert_same(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '}\n'
            'let p: Point = Point(1, 2)\n'
            'print p.sum()\n'
            'print p\n')

    def test_method_parameters_and_expression_statement(self):
        self.assert_same(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn scaled(self: Point, k: Int) -> Int '
            '{ return self.x * k + self.y }\n'
            '}\n'
            'let p = Point(3, 4)\n'
            'print p.scaled(2)\n'
            'p.scaled(1)\n')

    def test_top_level_methods_are_registered_before_declaration(self):
        # The interpreter registers top-level records and methods up front, so
        # this call works even though the declaration statement comes later.
        self.assert_same(
            'let p = Point(2, 3)\n'
            'print p.sum()\n'
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '}\n')

    # -- defaults / method-to-method / recursion -------------------------
    def test_default_arguments(self):
        self.assert_same(
            'record Point {\n'
            '  x: Int\n'
            '  fn add(self: Point, k: Int = 4) -> Int { return self.x + k }\n'
            '}\n'
            'let p = Point(1)\n'
            'print p.add()\n'
            'print p.add(10)\n')

    def test_method_calls_another_method(self):
        self.assert_same(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '  fn total(self: Point) -> Int { return self.sum() + 1 }\n'
            '}\n'
            'let p = Point(1, 2)\n'
            'print p.sum()\n'
            'print p.total()\n')

    def test_recursion_through_self(self):
        self.assert_same(
            'record M {\n'
            '  n: Int\n'
            '  fn fact(self: M, k: Int) -> Int {\n'
            '    if k <= 1 { return 1 }\n'
            '    return k * self.fact(k - 1)\n'
            '  }\n'
            '}\n'
            'let m = M(0)\n'
            'print m.fact(5)\n')

    # -- mutation --------------------------------------------------------
    def test_method_mutates_receiver_and_aliases_observe(self):
        self.assert_same(
            'record Q {\n'
            '  x: Int\n'
            '  fn bump(self: Q) -> Q {\n'
            '    self.x := self.x + 1\n'
            '    return self\n'
            '  }\n'
            '}\n'
            'let q = Q(1)\n'
            'let r = q\n'
            'print q.bump()\n'
            'print r.x\n'
            'print q\n')

    # -- return values ---------------------------------------------------
    def test_method_returns_record(self):
        self.assert_same(
            'record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn make(self: Point, k: Int = 1) -> Point '
            '{ return Point(self.x + k, self.y) }\n'
            '}\n'
            'let p = Point(1, 2)\n'
            'print p.make()\n'
            'print p.make(5)\n')

    def test_method_returns_first_class_function(self):
        self.assert_same(
            'record P {\n'
            '  x: Int\n'
            '  fn mk(self: P) -> Function {\n'
            '    return fn(y: Int) -> Int { return self.x + y }\n'
            '  }\n'
            '}\n'
            'let p = P(10)\n'
            'let f = p.mk()\n'
            'print f(5)\n')

    # -- nested and namespaced receivers ---------------------------------
    def test_nested_receiver(self):
        self.assert_same(
            'record Inner {\n'
            '  v: Int\n'
            '  fn double(self: Inner) -> Int { return self.v * 2 }\n'
            '}\n'
            'record Outer { inner: Inner }\n'
            'let o = Outer(Inner(5))\n'
            'print o.inner.double()\n')

    def test_namespaced_record_methods(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "geo.gm").write_text(
            'export record Point {\n'
            '  x: Int\n'
            '  y: Int\n'
            '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
            '  fn total(self: Point) -> Int { return self.sum() + 1 }\n'
            '  fn bump(self: Point) -> Point {\n'
            '    self.x := self.x + 1\n'
            '    return self\n'
            '  }\n'
            '}\n', encoding="utf-8")
        main = root / "main.gm"
        main.write_text(
            'import "geo.gm" as geo\n'
            'let p: geo.Point = geo.Point(1, 2)\n'
            'print p.sum()\n'
            'print p.total()\n'
            'print p.bump()\n'
            'print p.sum()\n', encoding="utf-8")
        self.assertEqual(
            IR.run_file(str(main)).out, G.run_file(str(main)).out)

    # -- errors on unchecked ASTs ----------------------------------------
    def test_unknown_method_error(self):
        self.assert_same_unchecked_error(
            'record P { x: Int }\n'
            'let p = P(1)\n'
            'print p.nope()\n')

    def test_unknown_method_on_self(self):
        self.assert_same_unchecked_error(
            'record P { x: Int fn f(self: P) -> Int '
            '{ return self.missing() } }\n'
            'let p = P(1)\n'
            'print p.f()\n')

    def test_arity_errors(self):
        self.assert_same_unchecked_error(
            'record P { x: Int fn f(self: P) -> Int { return self.x } }\n'
            'let p = P(1)\n'
            'print p.f(9)\n')
        self.assert_same_unchecked_error(
            'record P { x: Int fn f(self: P, a: Int) -> Int { return a } }\n'
            'let p = P(1)\n'
            'print p.f()\n')

    def test_receiver_is_not_a_record(self):
        self.assert_same_unchecked_error(
            'let p = 1\n'
            'print p.m()\n')

    # -- compiled tables -------------------------------------------------
    def test_program_carries_method_table(self):
        program = IR.compile_program(G.check(
            'record Point {\n'
            '  x: Int\n'
            '  fn get(self: Point) -> Int { return self.x }\n'
            '}\n'))
        self.assertEqual(program.records, {"Point": ("x",)})
        self.assertIn("get", program.methods["Point"])
        self.assertEqual(program.methods["Point"]["get"].name, "Point.get")


if __name__ == "__main__":
    unittest.main()
