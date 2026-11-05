"""IR-vs-interpreter parity for nominal record inheritance.

``record Child extends Parent`` now compiles to the same runtime shape as the
reference interpreter:

* fields flatten parent-first, so positional construction takes inherited
  fields before child fields and named construction accepts the whole set;
* the runtime value keeps its actual nominal type, so field access, printing,
  ``with`` copies and equality all see inherited fields without erasing the
  child type;
* methods are inherited and overridden, and a value statically typed as an
  ancestor still dispatches to the actual value's override (dynamic dispatch);
* namespaced records (``geo.Child extends geo.Parent``) work unchanged.

Unknown parents and inheritance cycles are rejected by the checker first; an
unchecked AST is rejected by the IR with a clear ``IRUnsupported``.
"""
import pathlib
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


BASE = (
    'record Animal { name: Text }\n'
    'record Dog extends Animal { breed: Text }\n')


class IRRecordInheritanceMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)
        return got

    # -- construction / access -------------------------------------------
    def test_positional_construction_and_inherited_access(self):
        self.assertEqual(
            self.assert_same(BASE
                             + 'let d = Dog("Rex", "lab")\n'
                             + 'print d.name\nprint d.breed\nprint d\n'),
            ["Rex", "lab", 'Dog { name: "Rex", breed: "lab" }'])

    def test_named_construction_accepts_all_fields(self):
        self.assertEqual(
            self.assert_same(BASE
                             + 'let d = Dog { breed: "lab", name: "Rex" }\n'
                             + 'print d.name\nprint d\n'),
            ["Rex", 'Dog { name: "Rex", breed: "lab" }'])

    def test_forward_parent_declaration(self):
        self.assertEqual(
            self.assert_same(
                'record Dog extends Animal { breed: Text }\n'
                'record Animal { name: Text }\n'
                'let d = Dog("Rex", "lab")\n'
                'print d.name\n'),
            ["Rex"])

    def test_multilevel_flattening(self):
        self.assertEqual(
            self.assert_same(
                'record A { a: Int }\n'
                'record B extends A { b: Int }\n'
                'record C extends B { c: Int }\n'
                'let c = C(1, 2, 3)\n'
                'print c\nprint c.a + c.b + c.c\n'),
            ['C { a: 1, b: 2, c: 3 }', "6"])

    # -- nominal subtyping ----------------------------------------------
    def test_subtype_assignment_let_parameter_return_field(self):
        self.assertEqual(
            self.assert_same(
                'record Animal { name: Text }\n'
                'record Dog extends Animal { breed: Text }\n'
                'fn label(a: Animal) -> Text { return a.name }\n'
                'fn build() -> Animal { return Dog("Rex", "lab") }\n'
                'record Shelter { resident: Animal }\n'
                'let d: Dog = Dog("Rex", "lab")\n'
                'let a: Animal = d\n'
                'let b: Animal = build()\n'
                'let s = Shelter(d)\n'
                'print a.name\nprint label(d)\nprint b.name\n'
                'print s.resident.name\n'),
            ["Rex", "Rex", "Rex", "Rex"])

    # -- methods / overrides / dispatch ----------------------------------
    def test_inherited_method_is_callable(self):
        self.assertEqual(
            self.assert_same(
                'record Animal {\n'
                '  name: Text\n'
                '  fn speak(self: Animal) -> Text { return self.name }\n'
                '}\n'
                'record Dog extends Animal { breed: Text }\n'
                'let d = Dog("Rex", "lab")\n'
                'print d.speak()\n'),
            ["Rex"])

    def test_override_and_dynamic_dispatch(self):
        self.assertEqual(
            self.assert_same(
                'record Animal {\n'
                '  name: Text\n'
                '  fn speak(self: Animal) -> Text { return self.name }\n'
                '}\n'
                'record Dog extends Animal {\n'
                '  breed: Text\n'
                '  fn speak(self: Dog) -> Text { return "woof" }\n'
                '}\n'
                'let d = Dog("Rex", "lab")\n'
                'print d.speak()\n'
                'let a: Animal = d\n'
                'print a.speak()\n'),
            ["woof", "woof"])

    def test_multilevel_override_dispatch(self):
        self.assertEqual(
            self.assert_same(
                'record A { a: Int\n'
                '  fn who(self: A) -> Text { return "A" }\n'
                '}\n'
                'record B extends A { b: Int\n'
                '  fn who(self: B) -> Text { return "B" }\n'
                '}\n'
                'record C extends B { c: Int }\n'
                'let c = C(1, 2, 3)\n'
                'print c.who()\n'
                'let a: A = c\n'
                'print a.who()\n'),
            ["B", "B"])

    # -- copy / mutation / equality -------------------------------------
    def test_with_and_in_place_assignment_on_inherited_fields(self):
        self.assertEqual(
            self.assert_same(BASE
                             + 'let d = Dog("Rex", "lab")\n'
                             + 'let e = d with { name = "Fido" }\n'
                             + 'print e\nprint d\n'
                             + 'e.name := "Max"\n'
                             + 'e.breed := "pug"\n'
                             + 'print e\n'),
            ['Dog { name: "Fido", breed: "lab" }',
             'Dog { name: "Rex", breed: "lab" }',
             'Dog { name: "Max", breed: "pug" }'])

    def test_equality_is_nominal_across_inheritance(self):
        self.assertEqual(
            self.assert_same(BASE
                             + 'print Dog("Rex", "lab") == Dog("Rex", "lab")\n'
                             + 'print Dog("Rex", "lab") == Dog("Rex", "pug")\n'
                             + 'print Dog("Rex", "lab") == Animal("Rex")\n'
                             + 'print Dog("Rex", "lab") != Animal("Rex")\n'),
            ["True", "False", "False", "True"])

    # -- namespaced records ---------------------------------------------
    def test_namespaced_inheritance(self):
        self.assertEqual(
            self.assert_same(
                'record geo.Animal { name: Text }\n'
                'record geo.Dog extends geo.Animal { breed: Text }\n'
                'let d = geo.Dog("Rex", "lab")\n'
                'print d.name\nprint d.breed\n'
                'let a: geo.Animal = d\n'
                'print a.name\nprint d\n'),
            ["Rex", "lab", "Rex", 'geo.Dog { name: "Rex", breed: "lab" }'])

    # -- unchecked AST errors -------------------------------------------
    def test_unchecked_unknown_parent_is_rejected(self):
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(
                G.parse('record Dog extends Nope { name: Text }\n'))
        self.assertIn("unknown parent Nope", str(ctx.exception))

    def test_unchecked_inheritance_cycle_is_rejected(self):
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(
                G.parse('record A extends B { x: Int }\n'
                        'record B extends A { y: Int }\n'))
        self.assertIn("inheritance cycle", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
