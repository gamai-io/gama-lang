import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class EnumTests(unittest.TestCase):
    def test_declaration_and_values(self):
        it = G.run('enum Color { Red, Green, Blue }\n'
                   'print Color.Red\nprint Color.Green\nprint Color.Blue\n')
        self.assertEqual(it.out, ['Color.Red', 'Color.Green', 'Color.Blue'])

    def test_annotated_let_and_type(self):
        it = G.run('enum Color { Red }\nlet c: Color = Color.Red\nprint c\n')
        self.assertEqual(it.out, ['Color.Red'])
        self.assertEqual(G.tname(it.env.get('c')), 'Color')

    def test_equality(self):
        it = G.run('enum Color { Red, Green }\n'
                   'print Color.Red == Color.Red\n'
                   'print Color.Red == Color.Green\n'
                   'print Color.Red != Color.Green\n')
        self.assertEqual(it.out, ['True', 'False', 'True'])

    def test_cross_enum_equality_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum A { X }\nenum B { X }\nprint A.X == B.X\n')

    def test_match_arrow_and_comma(self):
        src = '''enum Color { Red, Green }
let c: Color = Color.Green
match c {
  Color.Red => { print "red" },
  _ => { print "other" }
}
'''
        self.assertEqual(G.run(src).out, ['other'])

    def test_match_variant_branch(self):
        src = '''enum Color { Red, Green }
match Color.Red {
  Color.Green { print "green" }
  Color.Red { print "red" }
}
'''
        self.assertEqual(G.run(src).out, ['red'])

    def test_match_without_wildcard_no_arm(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('enum Color { Red, Green }\nlet c = Color.Green\n'
                  'match c { Color.Red { print 1 } }\n')

    def test_unknown_variant(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check('enum Color { Red }\nprint Color.Purple\n')
        self.assertIn('unknown enum variant', str(cm.exception))

    def test_unknown_enum_type(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print Missing.Variant\n')

    def test_unknown_variant_in_match(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum Color { Red }\n'
                    'match Color.Red { Color.Purple { print 1 } }\n')

    def test_annotation_mismatch(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum Color { Red }\nlet c: Other = Color.Red\n')

    def test_duplicate_variant(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check('enum Bad { A, A }\n')
        self.assertIn('duplicate enum variant', str(cm.exception))

    def test_duplicate_enum(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum X { A }\nenum X { B }\n')

    def test_shadowing_variant_name(self):
        it = G.run('enum Color { Red }\nlet Red = 1\nlet c = Color.Red\n'
                   'print c == Color.Red\nprint Red\n')
        self.assertEqual(it.out, ['True', '1'])

    def test_same_variant_different_enums(self):
        it = G.run('enum A { X }\nenum B { X }\n'
                   'print A.X\nprint B.X\nprint A.X == A.X\n')
        self.assertEqual(it.out, ['A.X', 'B.X', 'True'])

    def test_function_can_use_enum(self):
        it = G.run('fn f() -> Color { return Color.Blue }\n'
                   'enum Color { Red, Blue }\nprint f()\n')
        self.assertEqual(it.out, ['Color.Blue'])

    def test_enum_is_top_level_only(self):
        with self.assertRaises(G.GamaError):
            G.check('fn f() -> Int { enum X { A }\n return 1 }\nprint f()\n')

    def test_existing_result_match_still_works(self):
        it = G.run('match Ok(5) { Ok(v) { print v } Err(e) { print e } }\n')
        self.assertEqual(it.out, ['5'])
        it = G.run('match Err("bad") { Ok(v) { print v } _ { print "fallback" } }\n')
        self.assertEqual(it.out, ['fallback'])

    def test_enum_pattern_on_result_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('enum Color { Red }\n'
                    'match Ok(1) { Color.Red { print 1 } _ { print 2 } }\n')


if __name__ == '__main__':
    unittest.main()
