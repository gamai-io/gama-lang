import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


class RecordPatternRuntimeTests(unittest.TestCase):
    def test_basic_positional(self):
        src = '''record Point { x: Int, y: Int }
let p = Point(1, 2)
match p {
  Point(x, y) => { print x + y },
  _ => { print 0 }
}
'''
        self.assertEqual(G.run(src).out, ["3"])

    def test_inherited_fields_come_first(self):
        src = '''record Base { a: Int }
record Child extends Base { b: Text }
let c = Child(7, "z")
match c { Child(n, s) => { print n; print s } }
'''
        self.assertEqual(G.run(src).out, ["7", "z"])

    def test_wildcard_field_and_arm_fallthrough(self):
        src = '''record Point { x: Int, y: Int }
let p = Point(4, 5)
match p {
  Point(_, y) => { print y },
  _ => { print -1 }
}
match Point(1, 2) { Point(x, y) => { print "hit" }, _ => { print "fallback" } }
'''
        # The second match binds both fields and therefore hits the first arm.
        self.assertEqual(G.run(src).out, ["5", "hit"])

    def test_nested_record_pattern(self):
        src = '''record Point { x: Int, y: Int }
record Outer { inner: Point, name: Text }
let o = Outer(Point(2, 3), "n")
match o {
  Outer(Point(a, b), n) => { print a + b; print n },
  _ => { print 0 }
}
'''
        self.assertEqual(G.run(src).out, ["5", "n"])

    def test_nested_wildcard(self):
        src = '''record Point { x: Int, y: Int }
record Outer { inner: Point, name: Text }
let o = Outer(Point(2, 3), "n")
match o { Outer(_, n) => { print n } }
'''
        self.assertEqual(G.run(src).out, ["n"])

    def test_zero_field_record(self):
        src = '''record Empty { }
let e = Empty()
match e { Empty() => { print "empty" }, _ => { print "no" } }
'''
        self.assertEqual(G.run(src).out, ["empty"])

    def test_single_field_record(self):
        src = '''record Box { value: Text }
let b = Box("hi")
match b { Box(v) => { print v } }
'''
        self.assertEqual(G.run(src).out, ["hi"])

    def test_no_arm_matched(self):
        src = '''record A { x: Int }
record B { y: Int }
fn build() -> Any { return B(1) }
let v = build()
match v { A(x) => { print x } }
'''
        with self.assertRaises(G.GamaRuntimeError):
            G.run(src)

    def test_subclass_does_not_match_parent_pattern(self):
        # Chosen semantics: record patterns are exact-nominal.  A parent
        # pattern does not match a child value; the arm falls through.
        src = '''record Base { a: Int }
record Child extends Base { b: Text }
fn build() -> Any { return Child(1, "x") }
let v = build()
match v {
  Base(a) => { print "base" },
  _ => { print "other" }
}
'''
        self.assertEqual(G.run(src).out, ["other"])


class RecordPatternCheckerTests(unittest.TestCase):
    def test_binding_types_are_field_types(self):
        src = '''record Point { x: Int, y: Int }
match Point(1, 2) { Point(x, y) => { print x + y } }
'''
        G.check(src)
        bad = '''record Point { x: Int, y: Int }
match Point(1, 2) { Point(x, y) => { print x + "no" } }
'''
        with self.assertRaises(G.GamaTypeError):
            G.check(bad)

    def test_text_binding_type(self):
        src = '''record Named { name: Text }
match Named("a") { Named(n) => { print n + "!" } }
'''
        self.assertEqual(G.run(src).out, ["a!"])

    def test_arity_mismatch(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('record P { x: Int }\nmatch P(1) { P(a, b) => { print 1 } }\n')
        self.assertIn("expects 1 field binding", str(ctx.exception))

    def test_unknown_record_pattern(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record P { x: Int }\nmatch P(1) { Mystery(a) => { print a } }\n')

    def test_record_arm_on_result_subject(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record P { x: Int }\nmatch Ok(1) { P(a) => { print a } }\n')

    def test_result_arm_on_record_subject(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record P { x: Int }\nmatch P(1) { Ok(v) => { print v } }\n')

    def test_wrong_record_subject(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('record P { x: Int }\nrecord Q { y: Int }\n'
                    'match P(1) { Q(a) => { print a } }\n')
        self.assertIn("does not match subject type", str(ctx.exception))

    def test_non_record_subject(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record P { x: Int }\nmatch 1 { P(a) => { print a } }\n')

    def test_duplicate_binding_names(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('record P { x: Int, y: Int }\n'
                    'match P(1, 2) { P(a, a) => { print a } }\n')
        self.assertIn("duplicate binding a", str(ctx.exception))

    def test_duplicate_across_nested_pattern(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record P { x: Int }\nrecord O { p: P, n: Int }\n'
                    'match O(P(1), 2) { O(P(a), a) => { print a } }\n')

    def test_nested_pattern_requires_record_field(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('record P { x: Int }\nrecord O { p: Int }\n'
                    'match O(1) { O(P(a)) => { print a } }\n')
        self.assertIn("not a record", str(ctx.exception))

    def test_nested_pattern_must_match_field_type(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('record P { x: Int }\nrecord Q { y: Int }\n'
                    'record O { p: P }\n'
                    'match O(P(1)) { O(Q(a)) => { print a } }\n')
        self.assertIn("does not match", str(ctx.exception))


class RecordPatternFormatterTests(unittest.TestCase):
    def test_round_trip_nested(self):
        src = '''record Point { x: Int, y: Int }
record Outer { inner: Point, name: Text }
let o = Outer(Point(2, 3), "n")
match o {
  Outer(Point(a, b), n) => { print a + b },
  _ => { print 0 }
}
'''
        once = gama_fmt.format_source(src)
        self.assertIn("Outer(Point(a, b), n)", once)
        self.assertEqual(gama_fmt.format_source(once), once)
        self.assertEqual(G.run(once).out, G.run(src).out)

    def test_round_trip_wildcard_and_zero_field(self):
        src = '''record Empty { }
record Outer { inner: Empty, name: Text }
let o = Outer(Empty(), "n")
match o { Outer(_, n) => { print n }, _ => { print "x" } }
'''
        once = gama_fmt.format_source(src)
        self.assertEqual(gama_fmt.format_source(once), once)
        self.assertEqual(G.run(once).out, ["n"])


class RecordPatternIRTests(unittest.TestCase):
    def test_ir_rejects_record_patterns(self):
        src = '''record P { x: Int }
match P(1) { P(a) => { print a } }
'''
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))

    def test_ir_rejects_nested_record_patterns(self):
        src = '''record P { x: Int }
record O { p: P }
match O(P(1)) { O(P(a)) => { print a } }
'''
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))


if __name__ == '__main__':
    unittest.main()
