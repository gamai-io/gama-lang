"""IR-vs-interpreter parity for records and nullary enums.

Every successful program runs on the reference interpreter and on the IR; the
printed output must be byte-for-byte identical.  Runtime failures are compared
by full message (including line/col).  A few programs are intentionally parsed
but not checked so the IR's runtime validation can be compared with the
interpreter's (the checker would reject them first).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class IRRecordsAndEnumsMatchInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def assert_same_unchecked(self, src):
        expected = G.Interpreter().run(G.parse(src)).out
        got = IR.run_program(G.parse(src)).out
        self.assertEqual(got, expected)

    # -- records: construction and field access --------------------------
    def test_positional_construction_and_field_access(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let p: Point = Point(1, 2)\n'
            'print p.x\nprint p.y\nprint p\n')

    def test_named_construction_preserves_declaration_order(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let q: Point = Point { y: 4, x: 3 }\n'
            'print q.x\nprint q.y\nprint q\n')

    def test_record_printing_formats_fields(self):
        self.assert_same(
            'record Money { amount: Decimal, label: Text }\n'
            'print Money(1.50, "net")\n')

    def test_nested_records_field_chains_and_equality(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l: Line = Line(Point(1, 2), Point(3, 4))\n'
            'print l.a.x\nprint l.b.y\n'
            'print l == Line(Point(1, 2), Point(3, 4))\n')

    def test_nominal_value_equality(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'print Point(1, 2) == Point(1, 2)\n'
            'print Point(1, 2) == Point(2, 1)\n'
            'print Point(1, 2) != Point(2, 1)\n')

    def test_record_field_named_like_outer_variable(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let x = 99\n'
            'let p: Point = Point(1, 2)\n'
            'print p.x\nprint x\n')

    def test_record_as_function_argument_and_return(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'fn shift(p: Point, dx: Int, dy: Int) -> Point {\n'
            '  return Point(p.x + dx, p.y + dy)\n'
            '}\n'
            'print shift(Point(1, 2), 5, 6)\n')

    def test_function_declared_before_record(self):
        self.assert_same(
            'fn mk() -> Point { return Point(3, 4) }\n'
            'record Point { x: Int, y: Int }\n'
            'let p: Point = mk()\n'
            'print p\nprint p.x\n')

    def test_record_list_and_for_iteration(self):
        self.assert_same(
            'record Point { x: Int, y: Int }\n'
            'let xs = [Point(1, 2), Point(3, 4)]\n'
            'for p in xs { print p.x }\n'
            'print xs\n')

    def test_record_inside_ok(self):
        self.assert_same(
            'record Point { x: Int }\n'
            'print Ok(Point(9))\n')

    def test_record_declared_in_branch_activates_when_reached(self):
        # The checker does not pre-register nested records, so parse directly;
        # the interpreter registers it when the branch executes.
        self.assert_same_unchecked(
            'if true {\n'
            '  record Local { v: Int }\n'
            '  let q = Local(7)\n'
            '  print q.v\n'
            '  print q\n'
            '}\n')

    # -- enums -----------------------------------------------------------
    def test_enum_declaration_and_printing(self):
        self.assert_same(
            'enum Color { Red, Green, Blue }\n'
            'print Color.Red\nprint Color.Green\nprint Color.Blue\n')

    def test_enum_equality(self):
        self.assert_same(
            'enum Color { Red, Green }\n'
            'print Color.Red == Color.Red\n'
            'print Color.Red == Color.Green\n'
            'print Color.Red != Color.Green\n')

    def test_enum_variant_shadowing_of_local_name(self):
        self.assert_same(
            'enum Color { Red }\n'
            'let Red = 1\n'
            'let c: Color = Color.Red\n'
            'print c == Color.Red\nprint Red\n')

    def test_function_declared_before_enum(self):
        self.assert_same(
            'fn f() -> Color { return Color.Blue }\n'
            'enum Color { Red, Blue }\n'
            'print f()\n')

    def test_enum_match_variant_arm(self):
        self.assert_same(
            'enum Color { Red, Green }\n'
            'let c: Color = Color.Green\n'
            'match c { Color.Red { print "red" } Color.Green { print "green" } }\n')

    def test_enum_match_arrow_and_wildcard(self):
        self.assert_same(
            'enum Color { Red, Green }\n'
            'match Color.Green {\n'
            '  Color.Red => { print "red" },\n'
            '  _ => { print "other" }\n'
            '}\n')

    def test_enum_in_record_field(self):
        self.assert_same(
            'enum Color { Red, Green }\n'
            'record Paint { name: Text, color: Color }\n'
            'let p: Paint = Paint("base", Color.Green)\n'
            'print p.color\nprint p\n')

    # -- maps also support dotted field access ---------------------------
    def test_map_dotted_field_access(self):
        self.assert_same(
            'let m = {a: 1, b: 2}\n'
            'print m.a\nprint m.b\n')

    def test_nested_map_dotted_field_access(self):
        self.assert_same(
            'let m = {outer: {inner: 5}}\n'
            'print m.outer.inner\n')


class IRRecordEnumRuntimeErrorsMatchInterpreter(unittest.TestCase):
    def assert_same_runtime_error(self, src):
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(G.GamaError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(ref.exception))
        self.assertIsInstance(got.exception, IR.IRError)

    def test_unknown_enum_variant(self):
        self.assert_same_runtime_error('enum Color { Red }\nprint Color.Purple\n')

    def test_unknown_dotted_name(self):
        self.assert_same_runtime_error('print Missing.Variant\n')

    def test_unknown_field_on_record(self):
        self.assert_same_runtime_error(
            'record Point { x: Int }\nlet p = Point(1)\nprint p.z\n')

    def test_positional_arity_checked_before_arguments(self):
        self.assert_same_runtime_error(
            'record Point { x: Int, y: Int }\nprint Point(1)\n')

    def test_named_missing_field(self):
        self.assert_same_runtime_error(
            'record Point { x: Int, y: Int }\nlet p = Point { x: 1 }\n')

    def test_named_unknown_field(self):
        self.assert_same_runtime_error(
            'record Point { x: Int }\nlet p = Point { x: 1, z: 2 }\n')

    def test_named_duplicate_field(self):
        self.assert_same_runtime_error(
            'record Point { x: Int }\nlet p = Point { x: 1, x: 2 }\n')

    def test_match_on_record_needs_result(self):
        self.assert_same_runtime_error(
            'record Point { x: Int }\nlet p = Point(1)\n'
            'match p { _ { print 1 } }\n')

    def test_enum_match_no_arm(self):
        self.assert_same_runtime_error(
            'enum Color { Red, Green }\nlet c = Color.Green\n'
            'match c { Color.Red { print 1 } }\n')


class IRRecordsAndEnumsUnsupported(unittest.TestCase):
    def assert_unsupported(self, src):
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(G.parse(src))
        self.assertIn("IR does not support", str(ctx.exception))

    def test_dotted_call_still_unsupported(self):
        self.assert_unsupported('print mod.fn()\n')


class IRProgramTypeTables(unittest.TestCase):
    def test_program_captures_declarations(self):
        program = IR.compile_program(G.parse(
            'record Point { x: Int, y: Int }\n'
            'enum Color { Red, Green }\n'))
        self.assertEqual(program.records, {'Point': ('x', 'y')})
        self.assertEqual(program.enums, {'Color': frozenset({'Red', 'Green'})})

    def test_disassembly_contains_declaration_opcodes(self):
        listing = IR.compile_program(G.parse(
            'record Point { x: Int }\nenum Color { Red }\n')).disassemble()
        self.assertIn("DEF_RECORD", listing)
        self.assertIn("DEF_ENUM", listing)


if __name__ == '__main__':
    unittest.main()
