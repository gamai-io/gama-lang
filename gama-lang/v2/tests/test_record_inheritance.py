"""Nominal record inheritance.

Semantics under test:

* ``record Child extends Parent { ... }`` requires ``Parent`` to be a declared
  non-generic record (it may appear later in the file and may be namespaced).
* The flattened field list is *inherited fields first, then child fields*.
  Positional construction follows that order; named construction accepts all
  fields in any order.  Duplicate field names anywhere in the chain are
  rejected, as are unknown parents and inheritance cycles.
* Nominal subtyping: a Child value is assignable where a Parent is expected
  (``let``/parameters/returns/fields).  The reverse is a static error.
* Equality stays nominal: a Child value is never equal to an independently
  constructed Parent value (the runtime compares the nominal type name), and
  comparing unrelated record types is still a type error.
* Methods are inherited and callable on the child.  An override is accepted
  only when its parameter and return types are exactly the parent's (``self``
  excluded, as it must already name the child); otherwise it is rejected.
  ``self`` in an inherited method is statically the declaring record.
* Namespaced declarations (``geo.Dog extends geo.Animal``) work unchanged.
* The AST formatter round-trips ``extends`` headers.

Limits (documented, intentionally narrow): a parent may not be generic, and
the IR backend rejects inheritance loudly with ``IRUnsupported`` rather than
erasing it (only the tree-walking interpreter executes inheritance).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


BASE = (
    'record Animal { name: Text }\n'
    'record Dog extends Animal { breed: Text }\n')


class RecordInheritanceTests(unittest.TestCase):
    # -- fields / construction / access ----------------------------------
    def test_inherited_fields_come_first(self):
        it = G.run(BASE
                   + 'let d: Dog = Dog("Rex", "lab")\n'
                   + 'print d.name\nprint d.breed\nprint d\n')
        self.assertEqual(
            it.out,
            ["Rex", "lab", 'Dog { name: "Rex", breed: "lab" }'])

    def test_named_construction_includes_inherited_fields(self):
        it = G.run(BASE
                   + 'let d = Dog { breed: "lab", name: "Rex" }\n'
                   + 'print d.name\nprint d\n')
        self.assertEqual(
            it.out, ["Rex", 'Dog { name: "Rex", breed: "lab" }'])

    def test_forward_parent_reference(self):
        it = G.run(
            'record Dog extends Animal { breed: Text }\n'
            'record Animal { name: Text }\n'
            'let d = Dog("Rex", "lab")\n'
            'print d.name\n')
        self.assertEqual(it.out, ["Rex"])

    # -- nominal subtyping ----------------------------------------------
    def test_child_assignable_to_parent_in_let(self):
        it = G.run(BASE
                   + 'let d: Dog = Dog("Rex", "lab")\n'
                   + 'let a: Animal = d\n'
                   + 'print a.name\n')
        self.assertEqual(it.out, ["Rex"])

    def test_child_assignable_to_parent_parameter_and_return(self):
        it = G.run(BASE
                   + 'fn label(a: Animal) -> Text { return a.name }\n'
                   + 'fn build() -> Animal { return Dog("Rex", "lab") }\n'
                   + 'print label(Dog("Rex", "lab"))\n'
                   + 'let a = build()\n'
                   + 'print a.name\n')
        self.assertEqual(it.out, ["Rex", "Rex"])

    def test_child_assignable_to_parent_field(self):
        it = G.run(BASE
                   + 'record Shelter { resident: Animal }\n'
                   + 'let s = Shelter(Dog("Rex", "lab"))\n'
                   + 'print s.resident.name\n')
        self.assertEqual(it.out, ["Rex"])

    def test_parent_not_assignable_to_child(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"A != Dog|Animal != Dog"):
            G.check(BASE + 'let d: Dog = Animal("Rex")\n')

    # -- methods ---------------------------------------------------------
    def test_inherited_method_is_callable(self):
        it = G.run(
            'record Animal {\n'
            '  name: Text,\n'
            '  fn speak(self: Animal) -> Text { return self.name }\n'
            '}\n'
            'record Dog extends Animal { breed: Text }\n'
            'let d = Dog("Rex", "lab")\n'
            'print d.speak()\n')
        self.assertEqual(it.out, ["Rex"])

    def test_override_with_compatible_signature(self):
        it = G.run(
            'record Animal {\n'
            '  name: Text,\n'
            '  fn speak(self: Animal) -> Text { return self.name }\n'
            '}\n'
            'record Dog extends Animal {\n'
            '  breed: Text,\n'
            '  fn speak(self: Dog) -> Text { return "woof" }\n'
            '}\n'
            'let d = Dog("Rex", "lab")\n'
            'print d.speak()\n'
            'let a: Animal = d\n'
            'print a.speak()\n')
        # Dynamic dispatch: the runtime value is a Dog in both bindings.
        self.assertEqual(it.out, ["woof", "woof"])

    def test_incompatible_override_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"incompatible signature"):
            G.check(
                'record Animal {\n'
                '  name: Text,\n'
                '  fn speak(self: Animal) -> Text { return self.name }\n'
                '}\n'
                'record Dog extends Animal {\n'
                '  breed: Text,\n'
                '  fn speak(self: Dog, n: Int) -> Text { return "woof" }\n'
                '}\n')

    # -- declaration errors ---------------------------------------------
    def test_duplicate_inherited_field_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"record Dog: duplicate field name"):
            G.check(
                'record Animal { name: Text }\n'
                'record Dog extends Animal { name: Int }\n')

    def test_unknown_parent_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"record Dog: unknown parent Nope"):
            G.check('record Dog extends Nope { name: Text }\n')

    def test_inheritance_cycle_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"inheritance cycle"):
            G.check(
                'record A extends B { x: Int }\n'
                'record B extends A { y: Int }\n')

    def test_generic_parent_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"cannot extend generic record Box"):
            G.check(
                'record Box<T> { value: T }\n'
                'record Tagged extends Box { tag: Text }\n')

    def test_wrong_arity_and_type(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Dog expects 2 fields"):
            G.check(BASE + 'let d = Dog("Rex")\n')
        with self.assertRaisesRegex(G.GamaTypeError, r"Dog.breed: Int != Text"):
            G.check(BASE + 'let d = Dog("Rex", 1)\n')

    # -- equality --------------------------------------------------------
    def test_equality_is_nominal_across_inheritance(self):
        it = G.run(BASE
                   + 'print Dog("Rex", "lab") == Dog("Rex", "lab")\n'
                   + 'print Dog("Rex", "lab") == Dog("Rex", "pug")\n'
                   + 'print Dog("Rex", "lab") == Animal("Rex")\n'
                   + 'print Dog("Rex", "lab") != Animal("Rex")\n')
        self.assertEqual(it.out, ["True", "False", "False", "True"])

    def test_unrelated_records_still_cannot_be_compared(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"matching types"):
            G.check(
                'record A { x: Int }\n'
                'record B { x: Int }\n'
                'print A(1) == B(1)\n')

    # -- namespaced records ---------------------------------------------
    def test_namespaced_inheritance(self):
        it = G.run(
            'record geo.Animal { name: Text }\n'
            'record geo.Dog extends geo.Animal { breed: Text }\n'
            'let d = geo.Dog("Rex", "lab")\n'
            'print d.name\nprint d.breed\n'
            'let a: geo.Animal = d\n'
            'print a.name\n')
        self.assertEqual(it.out, ["Rex", "lab", "Rex"])

    # -- formatter -------------------------------------------------------
    def test_formatter_round_trips_extends(self):
        src = (
            'record Animal { name: Text }\n'
            'record Dog extends Animal { breed: Text }\n')
        out = gama_fmt.format_source(src)
        self.assertIn('record Dog extends Animal', out)
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertTrue(G.parse(out))

    def test_formatter_wraps_extends_header(self):
        src = (
            'record geo.VeryLongParentRecordName { name: Text }\n'
            'record geo.VeryLongChildRecordName '
            'extends geo.VeryLongParentRecordName { extra: Int }\n')
        out = gama_fmt.format_source(src, width=40)
        self.assertIn('extends', out)
        self.assertEqual(out, gama_fmt.format_source(out, width=40))

    # -- IR --------------------------------------------------------------
    def test_ir_rejects_inheritance_loudly(self):
        with self.assertRaises(IR.IRUnsupported) as ctx:
            IR.compile_program(G.check(BASE))
        self.assertIn("record inheritance", str(ctx.exception))

    def test_ir_still_runs_plain_records(self):
        out = IR.run_program(G.check(
            'record Point { x: Int, y: Int }\n'
            'print Point(1, 2)\n')).out
        self.assertEqual(out, ["Point { x: 1, y: 2 }"])


if __name__ == "__main__":
    unittest.main()
