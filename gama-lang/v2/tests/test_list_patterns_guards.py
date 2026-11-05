"""List patterns and match guards.

Covers ``[]``, fixed ``[a, b]``, rest ``[a, ...rest]``, nested lists, literal
items, ``_`` and variable items, and ``pattern if <Bool>`` guards.  Record
destructuring patterns from the previous feature must keep working unchanged.
The IR backend deliberately rejects the new pattern forms with IRUnsupported;
existing Ok/Err/enum arms still compile and run.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


class ListPatternRuntimeTests(unittest.TestCase):
    def test_empty_list(self):
        src = ('match [] { [] => { print "empty" }, '
               '[a] => { print a }, _ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["empty"])

    def test_single_element(self):
        src = ('match [7] { [] => { print "empty" }, [a] => { print a }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["7"])

    def test_fixed_pair(self):
        src = ('match [2, 3] { [a, b] => { print a + b }, '
               '_ => { print 0 } }\n')
        self.assertEqual(G.run(src).out, ["5"])

    def test_fixed_length_mismatch_falls_through(self):
        src = ('match [2, 3] { [a] => { print a }, [a, b] => { print a + b }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["5"])

    def test_rest_binds_tail_as_list(self):
        src = ('match [1, 2, 3, 4] {\n'
               '  [] => { print "empty" },\n'
               '  [a] => { print a },\n'
               '  [a, b] => { print a + b },\n'
               '  [a, ...rest] => { print a; print len(rest) },\n'
               '  _ => { print "other" }\n'
               '}\n')
        self.assertEqual(G.run(src).out, ["1", "3"])

    def test_rest_can_be_empty(self):
        src = ('match [9] { [a, ...rest] => { print a; print len(rest) }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["9", "0"])

    def test_rest_only(self):
        src = ('match [4, 5] { [...rest] => { print len(rest) } }\n')
        self.assertEqual(G.run(src).out, ["2"])

    def test_rest_wildcard_skips_tail(self):
        src = ('match [1, 2, 3] { [a, ..._] => { print a }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["1"])

    def test_nested_list(self):
        src = ('match [[1], 2] { [[a], b] => { print a + b }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["3"])

    def test_nested_list_with_rest(self):
        src = ('match [[1, 2, 3], 9] { [[a, ...r], b] => '
               '{ print a; print len(r); print b }, _ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["1", "2", "9"])

    def test_negative_literal_item(self):
        src = ('match [-1, 2] { [-1, b] => { print b }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["2"])

    def test_record_pattern_inside_list(self):
        src = ('record Point { x: Int, y: Int }\n'
               'match [Point(1, 2)] { [Point(a, b)] => { print a + b }, '
               '_ => { print 0 } }\n')
        self.assertEqual(G.run(src).out, ["3"])

    def test_wildcard_item(self):
        src = ('match [1, 2] { [_, b] => { print b }, _ => { print 0 } }\n')
        self.assertEqual(G.run(src).out, ["2"])

    def test_literals_match(self):
        src = ('match [0, "x", true, null] {\n'
               '  [0, "x", true, null] => { print "hit" },\n'
               '  _ => { print "miss" }\n'
               '}\n')
        self.assertEqual(G.run(src).out, ["hit"])

    def test_literal_mismatch_falls_through(self):
        src = ('match [1] { [0] => { print "zero" }, '
               '_ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["other"])

    def test_bool_literal_does_not_match_int(self):
        # Python's True == 1 must not leak into GAMA literal matching.
        src = ('match [true] { [1] => { print "int" }, '
               '[true] => { print "bool" }, _ => { print "other" } }\n')
        self.assertEqual(G.run(src).out, ["bool"])

    def test_no_arm_matched(self):
        src = 'match [1, 2] { [] => { print "empty" } }\n'
        with self.assertRaises(G.GamaRuntimeError) as ctx:
            G.run(src)
        self.assertIn("no arm matched", str(ctx.exception))

    def test_list_pattern_on_non_list_is_rejected_by_checker(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('match Ok(1) { [a] => { print a }, _ => { print 1 } }\n')


class GuardRuntimeTests(unittest.TestCase):
    def test_variable_guard_true_and_false(self):
        src = ('match n {\n'
               '  x if x > 0 => { print "pos" },\n'
               '  _ => { print "other" }\n'
               '}\n')
        self.assertEqual(G.run('let n = 5\n' + src).out, ["pos"])
        self.assertEqual(G.run('let n = -5\n' + src).out, ["other"])

    def test_multiple_guards(self):
        src = ('match n {\n'
               '  x if x > 0 => { print "pos" },\n'
               '  x if x < 0 => { print "neg" },\n'
               '  _ => { print "zero" }\n'
               '}\n')
        self.assertEqual(G.run('let n = 7\n' + src).out, ["pos"])
        self.assertEqual(G.run('let n = -7\n' + src).out, ["neg"])
        self.assertEqual(G.run('let n = 0\n' + src).out, ["zero"])

    def test_guard_runs_after_bindings(self):
        src = ('match [3, 1] {\n'
               '  [a, b] if a > b => { print a - b },\n'
               '  [a, b] => { print b - a },\n'
               '  _ => { print 0 }\n'
               '}\n')
        self.assertEqual(G.run(src).out, ["2"])

    def test_guard_false_then_next_arm_binds_again(self):
        src = ('match [1, 4] {\n'
               '  [a, b] if a > b => { print a },\n'
               '  [a, b] => { print b },\n'
               '  _ => { print 0 }\n'
               '}\n')
        self.assertEqual(G.run(src).out, ["4"])

    def test_record_guard(self):
        src = ('record Point { x: Int, y: Int }\n'
               'match Point(1, 2) {\n'
               '  Point(x, y) if x > y => { print "x" },\n'
               '  Point(x, y) => { print x + y },\n'
               '  _ => { print 0 }\n'
               '}\n')
        self.assertEqual(G.run(src).out, ["3"])

    def test_enum_guard(self):
        src = ('enum Option { Some(Int), None }\n'
               'match Some(5) {\n'
               '  Some(v) if v > 0 => { print v },\n'
               '  Some(v) => { print 0 - v },\n'
               '  None => { print "none" }\n'
               '}\n')
        self.assertEqual(G.run(src).out, ["5"])

    def test_guard_false_with_no_fallback_is_no_match(self):
        src = ('match 1 { x if x > 10 => { print x } }\n')
        with self.assertRaises(G.GamaRuntimeError) as ctx:
            G.run(src)
        self.assertIn("no arm matched", str(ctx.exception))


class ListPatternCheckerTests(unittest.TestCase):
    def test_bindings_are_usable(self):
        G.check('match [1, 2] { [a, b] => { print a; print b } }\n')

    def test_rest_has_list_type(self):
        G.check('match [1, 2] { [a, ...rest] => { print len(rest) } }\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('match [1, 2] { [a, ...rest] => { let n: Int = rest } }\n')

    def test_item_bindings_are_any(self):
        G.check('fn f(xs: List) { match xs { [a] => { print a } } }\n')

    def test_duplicate_fixed_binding(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('match [1, 2] { [a, a] => { print a } }\n')
        self.assertIn("duplicate binding a", str(ctx.exception))

    def test_duplicate_rest_binding(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('match [1, 2] { [a, ...a] => { print a } }\n')

    def test_duplicate_across_nested_list(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('match [[1], 2] { [[a], a] => { print a } }\n')

    def test_list_pattern_subject_must_be_list(self):
        for src in (
            'match 1 { [a] => { print a } }\n',
            'match "abc" { [a] => { print a } }\n',
            'match Ok(1) { [a] => { print a } }\n',
        ):
            with self.subTest(src=src):
                with self.assertRaises(G.GamaTypeError):
                    G.check(src)

    def test_guard_must_be_bool(self):
        with self.assertRaises(G.GamaTypeError) as ctx:
            G.check('match 1 { x if x => { print x } }\n')
        self.assertIn("guard must be Bool", str(ctx.exception))

    def test_guard_uses_item_binding(self):
        G.check('match [1, 2] { [a, b] if a == b => { print a }, '
                '_ => { print 0 } }\n')

    def test_variable_pattern_binds_subject_type(self):
        G.check('match 3 { x if x > 0 => { print x + 1 } }\n')

    def test_match_on_int_without_variable_still_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('match 1 { _ => { print 1 } }\n')

    def test_record_patterns_still_work(self):
        src = ('record Point { x: Int, y: Int }\n'
               'match Point(1, 2) { Point(a, b) => { print a + b }, '
               '_ => { print 0 } }\n')
        self.assertEqual(G.run(src).out, ["3"])

    def test_record_duplicate_still_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record P { x: Int, y: Int }\n'
                    'match P(1, 2) { P(a, a) => { print a } }\n')

    def test_enum_and_result_arms_still_work(self):
        src = ('enum Option { Some(Int), None }\n'
               'match Ok(1) { Ok(v) => { print v }, Err(e) => { print e } }\n'
               'match Some(2) { Some(v) => { print v }, None => { print 0 } }\n')
        self.assertEqual(G.run(src).out, ["1", "2"])


class ListPatternParserTests(unittest.TestCase):
    def test_rest_must_be_last(self):
        with self.assertRaises(G.GamaError):
            G.parse('match [1] { [...rest, a] => { print a } }\n')

    def test_rest_requires_binding(self):
        with self.assertRaises(G.GamaError):
            G.parse('match [1] { [..., a] => { print a } }\n')

    def test_trailing_comma(self):
        src = 'match [1] { [a,] => { print a } }\n'
        self.assertEqual(G.run(src).out, ["1"])

    def test_empty_list_literal_subject(self):
        src = 'match [] { [] => { print "yes" }, _ => { print "no" } }\n'
        self.assertEqual(G.run(src).out, ["yes"])


class ListPatternFormatterTests(unittest.TestCase):
    def test_round_trip_list_and_rest(self):
        src = ('match xs {\n'
               '  [] => { print "empty" },\n'
               '  [a] => { print a },\n'
               '  [a, b] => { print a + b },\n'
               '  [a, ...rest] => { print a; print len(rest) },\n'
               '  _ => { print "other" }\n'
               '}\n')
        once = gama_fmt.format_source('let xs = [1, 2]\n' + src)
        self.assertIn("[a, ...rest]", once)
        self.assertEqual(gama_fmt.format_source(once), once)
        self.assertEqual(G.run(once).out, G.run('let xs = [1, 2]\n' + src).out)

    def test_round_trip_nested_and_literals(self):
        src = ('match xs {\n'
               '  [[a], 0] => { print a },\n'
               '  [0, "x", true, null] => { print "lit" },\n'
               '  _ => { print "other" }\n'
               '}\n')
        once = gama_fmt.format_source('let xs = [[1], 0]\n' + src)
        self.assertIn('[[a], 0]', once)
        self.assertIn('[0, "x", true, null]', once)
        self.assertEqual(gama_fmt.format_source(once), once)

    def test_round_trip_guard(self):
        src = ('match n {\n'
               '  x if x > 0 => { print "pos" },\n'
               '  _ => { print "other" }\n'
               '}\n')
        once = gama_fmt.format_source('let n = 5\n' + src)
        self.assertIn("x if x > 0", once)
        self.assertEqual(gama_fmt.format_source(once), once)
        self.assertEqual(G.run(once).out, ["pos"])

    def test_round_trip_record_pattern(self):
        src = ('record Point { x: Int, y: Int }\n'
               'match Point(1, 2) { Point(a, b) => { print a + b } }\n')
        once = gama_fmt.format_source(src)
        self.assertIn("Point(a, b)", once)
        self.assertEqual(gama_fmt.format_source(once), once)
        self.assertEqual(G.run(once).out, ["3"])


class ListPatternIRTests(unittest.TestCase):
    def test_ir_rejects_list_pattern(self):
        src = 'match [1] { [a] => { print a } }\n'
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))

    def test_ir_rejects_nested_list_pattern(self):
        src = 'match [[1], 2] { [[a], b] => { print a + b } }\n'
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))

    def test_ir_rejects_guard(self):
        src = 'match Ok(1) { Ok(v) if v > 0 => { print v }, ' \
              '_ => { print 0 } }\n'
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))

    def test_ir_rejects_variable_pattern(self):
        src = 'match 1 { x => { print x } }\n'
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))

    def test_ir_existing_result_and_enum_arms_unchanged(self):
        result = 'match Ok(1) { Ok(v) => { print v }, Err(e) => { print e } }\n'
        enum = ('enum Option { Some(Int), None }\n'
                'match Some(2) { Some(v) => { print v }, None => { print 0 } }\n')
        for src in (result, enum):
            with self.subTest(src=src):
                self.assertEqual(IR.run_program(G.check(src)).out,
                                 G.run(src).out)

    def test_ir_rejects_record_patterns_still(self):
        src = ('record P { x: Int }\n'
               'match P(1) { P(a) => { print a } }\n')
        with self.assertRaises(IR.IRUnsupported):
            IR.compile_program(G.parse(src))


if __name__ == '__main__':
    unittest.main()
