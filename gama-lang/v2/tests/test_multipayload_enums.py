import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


PAIR = 'enum Pair { Two(Int, Text), None }\n'


class MultiPayloadEnumTests(unittest.TestCase):
    def test_construct_annotate_and_print(self):
        it = G.run(PAIR + 'let p: Pair = Two(1, "a")\nprint p\n')
        self.assertEqual(it.out, ['Pair.Two(1, "a")'])
        self.assertEqual(G.tname(it.env.get('p')), 'Pair')

    def test_qualified_constructor_and_nullary(self):
        it = G.run(PAIR + 'print Pair.Two(3, "b")\nprint Pair.None\n')
        self.assertEqual(it.out, ['Pair.Two(3, "b")', 'Pair.None'])

    def test_match_binds_all_payloads_in_order(self):
        src = PAIR + '''match Two(7, "hi") {
  Two(n, s) => { print n; print s },
  None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['7', 'hi'])

    def test_bindings_have_declared_types(self):
        src = PAIR + '''match Two(2, "hi") {
  Two(n, s) => { print n + 1; print s + "!" },
  None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['3', 'hi!'])

    def test_match_nullary_variant_preserved(self):
        src = PAIR + '''match Pair.None {
  Two(n, s) => { print n; print s },
  None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['none'])

    def test_qualified_multi_pattern(self):
        src = PAIR + '''match Pair.Two(9, "q") {
  Pair.Two(n, s) => { print n; print s },
  Pair.None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['9', 'q'])

    def test_nested_multi_payload_match(self):
        src = '''enum Pair { Two(Int, Text), None }
enum Box { Wrap(Pair), Empty }
match Wrap(Two(4, "z")) {
  Wrap(inner) => {
    match inner {
      Two(n, s) => { print n; print s },
      None => { print "none" }
    }
  },
  Empty => { print "empty" }
}
'''
        self.assertEqual(G.run(src).out, ['4', 'z'])

    def test_multi_payload_holding_record(self):
        src = '''record Point { x: Int, y: Int }
enum Box { B(Point, Text), Empty }
match B(Point(2, 3), "tag") {
  B(p, tag) => { print p.x; print tag },
  Empty => { print "empty" }
}
'''
        self.assertEqual(G.run(src).out, ['2', 'tag'])

    def test_multi_payload_holding_result(self):
        src = '''enum Wrap { W(Result, Int), Empty }
match W(Ok(4), 10) {
  W(r, n) => {
    match r { Ok(v) => { print v + n } _ => { print "err" } }
  },
  Empty => { print "empty" }
}
'''
        self.assertEqual(G.run(src).out, ['14'])

    def test_equality(self):
        src = PAIR + '''print Two(1, "a") == Two(1, "a")
print Two(1, "a") == Two(1, "b")
print Two(1, "a") != Two(2, "a")
print Two(1, "a") == Pair.None
'''
        self.assertEqual(G.run(src).out, ['True', 'False', 'True', 'False'])

    def test_printing_is_deterministic_for_mixed_types(self):
        src = '''enum Mixed { V(Text, Bool, Decimal), Empty }
print V("x", true, 1.50)
'''
        self.assertEqual(G.run(src).out, ['Mixed.V("x", true, 1.50)'])

    def test_cross_enum_equality_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum A { X(Int, Text) }\nenum B { X(Int, Text) }\n'
                    'print A.X(1, "a") == B.X(1, "a")\n')

    def test_bare_constructor_ambiguous(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check('enum A { X(Int, Text) }\nenum B { X(Int, Text) }\n'
                    'let z = X(1, "a")\n')
        self.assertIn('ambiguous', str(cm.exception))

    def test_bare_constructor_unambiguous(self):
        it = G.run(PAIR + 'print Two(5, "x")\n')
        self.assertEqual(it.out, ['Pair.Two(5, "x")'])

    def test_wrong_arity_too_few(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(PAIR + 'let p = Two(1)\n')
        self.assertIn('expects 2 payload', str(cm.exception))

    def test_wrong_arity_too_many(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(PAIR + 'let p = Two(1, "a", 2)\n')
        self.assertIn('expects 2 payload', str(cm.exception))

    def test_wrong_payload_type(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(PAIR + 'let p = Two("a", 1)\n')
        self.assertIn('payload', str(cm.exception))

    def test_wrong_binding_count(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(PAIR + '''match Two(1, "a") {
  Two(n) => { print n }
}
''')
        self.assertIn('expects 2 payload binding', str(cm.exception))

    def test_payload_pattern_requires_binding(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(PAIR + '''match Two(1, "a") {
  Two => { print 1 }
}
''')
        self.assertIn('requires a payload pattern', str(cm.exception))

    def test_nullary_pattern_cannot_bind(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(PAIR + '''match Pair.None {
  None(v) => { print v }
}
''')
        self.assertIn('nullary', str(cm.exception))

    def test_annotation_mismatch(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(PAIR + 'let p: Other = Two(1, "a")\n')

    def test_single_payload_compatibility(self):
        src = 'enum Option { Some(Int), None }\n' + '''print Some(5)
match Some(5) {
  Some(v) => { print v },
  None => { print "none" }
}
'''
        self.assertEqual(G.run(src).out, ['Option.Some(5)', '5'])

    def test_nullary_compatibility(self):
        it = G.run('enum Color { Red, Green }\n'
                   'print Color.Red\nprint Color.Red == Color.Red\n'
                   'match Color.Green { Color.Red { print "r" } '
                   '_ { print "other" } }\n')
        self.assertEqual(it.out, ['Color.Red', 'True', 'other'])

    def test_formatter_round_trips_multi_payload(self):
        import gama_fmt
        src = ('enum Pair {\nTwo(Int,Text),\nNone\n}\n'
               'match Two(1,"a") {\n'
               '  Two(n,s) => { print n; print s },\n'
               '  None => { print "none" }\n}\n')
        out = gama_fmt.format_source(src)
        self.assertIn('enum Pair { Two(Int, Text), None }', out)
        self.assertIn('Two(n, s) {', out)
        self.assertEqual(out, gama_fmt.format_source(out))


if __name__ == '__main__':
    unittest.main()
