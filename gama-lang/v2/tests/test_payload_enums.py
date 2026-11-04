import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


OPTION = 'enum Option { Some(Int), None }\n'


class PayloadEnumTests(unittest.TestCase):
    def test_construct_annotate_and_print(self):
        it = G.run(OPTION + 'let x: Option = Some(5)\nprint x\n')
        self.assertEqual(it.out, ['Option.Some(5)'])
        self.assertEqual(G.tname(it.env.get('x')), 'Option')

    def test_qualified_constructor(self):
        it = G.run(OPTION + 'print Option.Some(3)\nprint Option.None\n')
        self.assertEqual(it.out, ['Option.Some(3)', 'Option.None'])

    def test_match_binds_payload(self):
        src = OPTION + '''match Some(7) {
  Some(v) => { print v },
  None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['7'])

    def test_match_nullary_variant(self):
        src = OPTION + '''match Option.None {
  Some(v) => { print v },
  None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['none'])

    def test_nested_payload_match(self):
        src = '''enum Option { Some(Int), None }
enum Outer { Wrap(Option), Empty }
match Wrap(Some(9)) {
  Wrap(inner) => {
    match inner {
      Some(v) => { print v },
      None => { print "none" }
    }
  },
  Empty => { print "empty" }
}
'''
        self.assertEqual(G.run(src).out, ['9'])

    def test_function_using_option(self):
        src = OPTION + '''fn get_or(o: Option, d: Int) -> Int {
  match o {
    Some(v) => { return v },
    _ => { return d }
  }
}
print get_or(Some(9), 0)
print get_or(Option.None, 42)
'''
        self.assertEqual(G.run(src).out, ['9', '42'])

    def test_payload_equality(self):
        src = OPTION + '''print Some(5) == Some(5)
print Some(5) == Some(6)
print Some(5) != Some(6)
print Option.None == Option.None
'''
        self.assertEqual(G.run(src).out, ['True', 'False', 'True', 'True'])

    def test_payload_vs_nullary_equality(self):
        src = OPTION + 'print Some(1) == Option.None\n'
        self.assertEqual(G.run(src).out, ['False'])

    def test_payload_printing_is_deterministic(self):
        src = '''enum Box { B(Text), F(Bool), D(Decimal) }
print B("hi")
print F(true)
print D(1.50)
'''
        self.assertEqual(G.run(src).out, ['Box.B("hi")', 'Box.F(true)',
                                          'Box.D(1.50)'])

    def test_nullable_payload_value(self):
        # A Null payload (``null``) is still a real payload type value.
        it = G.run('enum Box { B(Any) }\nprint B(null)\n')
        self.assertEqual(it.out, ['Box.B(null)'])

    def test_wrong_payload_type_is_type_error(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = Some("s")\n')
        self.assertIn('payload', str(cm.exception))

    def test_wrong_payload_arity_zero_is_type_error(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = Some()\n')
        self.assertIn('expects 1 payload', str(cm.exception))

    def test_wrong_payload_arity_two_is_type_error(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = Some(1, 2)\n')
        self.assertIn('expects 1 payload', str(cm.exception))

    def test_unknown_constructor(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = Maybe(5)\n')
        self.assertIn('unknown function Maybe', str(cm.exception))

    def test_nullary_variant_cannot_be_called(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = None()\n')
        self.assertIn('nullary', str(cm.exception))

    def test_payload_variant_used_bare(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = Option.Some\n')
        self.assertIn('requires a payload', str(cm.exception))

    def test_unknown_variant_in_match(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + '''match Some(1) {
  Missing(v) => { print v },
  _ => { print "x" }
}
''')
        self.assertIn('unknown enum variant', str(cm.exception))

    def test_nullary_pattern_cannot_bind(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + '''match Option.None {
  None(v) => { print v }
}
''')
        self.assertIn('nullary', str(cm.exception))

    def test_payload_pattern_requires_binding(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + '''match Some(1) {
  Some => { print 1 }
}
''')
        self.assertIn('requires a payload pattern', str(cm.exception))

    def test_cross_enum_equality_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum A { X(Int) }\nenum B { X(Int) }\n'
                    'print A.X(1) == B.X(1)\n')

    def test_ambiguous_bare_constructor(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check('enum A { X(Int) }\nenum B { X(Int) }\nlet z = X(1)\n')
        self.assertIn('ambiguous', str(cm.exception))

    def test_ambiguous_bare_constructor_qualified_ok(self):
        it = G.run('enum A { X(Int) }\nenum B { X(Int) }\n'
                   'print A.X(1)\nprint B.X(2)\n')
        self.assertEqual(it.out, ['A.X(1)', 'B.X(2)'])

    def test_qualified_payload_pattern(self):
        src = OPTION + '''match Option.Some(3) {
  Option.Some(v) => { print v },
  Option.None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['3'])

    def test_annotation_mismatch_with_payload_enum(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(OPTION + 'let x: Other = Some(1)\n')

    def test_nullary_enum_behaviour_preserved(self):
        it = G.run('enum Color { Red, Green }\n'
                   'print Color.Red\nprint Color.Red == Color.Red\n'
                   'match Color.Green { Color.Red { print "r" } '
                   '_ { print "other" } }\n')
        self.assertEqual(it.out, ['Color.Red', 'True', 'other'])

    def test_result_match_still_works(self):
        it = G.run('match Ok(5) { Ok(v) { print v } Err(e) { print e } }\n')
        self.assertEqual(it.out, ['5'])

    def test_payload_enum_with_result_payload(self):
        src = '''enum Wrapped { W(Result), Nothing }
match W(Ok(4)) {
  W(r) => { match r { Ok(v) => { print v } _ => { print "err" } } },
  Nothing => { print "nothing" }
}
'''
        self.assertEqual(G.run(src).out, ['4'])

    def test_records_and_lambdas_still_work(self):
        src = '''record Point { x: Int, y: Int }
enum Option { Some(Point), None }
let f = fn(p: Int) -> Int { return p + 1 }
match Some(Point(2, 3)) {
  Some(p) => { print p.x },
  None => { print 0 }
}
print f(4)
'''
        self.assertEqual(G.run(src).out, ['2', '5'])


if __name__ == '__main__':
    unittest.main()
