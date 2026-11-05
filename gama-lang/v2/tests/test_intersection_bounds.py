"""Intersection (multi-constraint) bounds on generic named functions.

Syntax::

    fn tag<T: Named & Aged>(x: T) -> Text { return x.name }

``T: A & B`` requires the inferred or explicit type argument to satisfy every
component at once.  Record components use the checker's structural record
compatibility (the argument must have all fields of each bound); base types
use the existing ``compat`` rules, so ``Int`` and ``Decimal`` remain mutually
compatible and ``Int & Decimal`` behaves like the numeric family.  Unions
(``T: A | B``) keep their previous "any component" meaning; mixing ``&`` and
``|`` in one bound list is a parse error.

Intersection bounds are function-only: generic records/enums still accept a
single bound.  Bounds are erased at runtime (``T`` -> ``Any``), the IR compiler
ignores them, and the formatter round-trips ``A & B``.  Lambdas do not carry
bounds (their parameter list has no bound slot).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir

PRELUDE = (
    'record Named { name: Text }\n'
    'record Aged { age: Int }\n'
    'fn tag<T: Named & Aged>(x: T) -> Text { return x.name }\n'
)
PERSON = 'record Person { name: Text, age: Int }\n'
NAME_ONLY = 'record NameOnly { name: Text }\n'


class TwoRecordTests(unittest.TestCase):
    def test_two_record_bounds_accept_structural_argument(self):
        it = G.run(PRELUDE + PERSON + 'print tag(Person("a", 3))\n')
        self.assertEqual(it.out, ["a"])

    def test_two_record_bounds_require_every_component(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"tag: type parameter T NameOnly does not satisfy all "
                r"bounds Named & Aged"):
            G.check(PRELUDE + NAME_ONLY + 'print tag(NameOnly("x"))\n')

    def test_extra_fields_are_still_allowed(self):
        src = (PRELUDE + 'record Full { name: Text, age: Int, city: Text }\n'
               + 'print tag(Full("f", 1, "S"))\n')
        self.assertEqual(G.run(src).out, ["f"])


class BaseTypeTests(unittest.TestCase):
    def test_numeric_intersection_accepts_both_numeric_types(self):
        src = ('fn keep<T: Int & Decimal>(x: T) -> T { return x }\n'
               + 'print keep(1)\nprint keep(2.5)\n')
        self.assertEqual(G.run(src).out, ["1", "2.5"])

    def test_base_type_intersection_rejects_unrelated_type(self):
        src = ('fn keep<T: Int & Decimal>(x: T) -> T { return x }\n'
               + 'print keep("x")\n')
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"keep: type parameter T Text does not satisfy all bounds "
                r"Int & Decimal"):
            G.check(src)

    def test_cross_base_intersection_rejects(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"does not satisfy all bounds Text & Int"):
            G.check(
                'fn both<T: Text & Int>(x: T) -> T { return x }\n'
                'print both("x")\n')


class ExplicitTypeArgumentTests(unittest.TestCase):
    def test_explicit_type_argument_must_satisfy_all_bounds(self):
        src = PRELUDE + PERSON + 'print tag<Person>(Person("b", 4))\n'
        self.assertEqual(G.run(src).out, ["b"])

    def test_explicit_type_argument_rejects_missing_component(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"tag: type argument NameOnly does not satisfy all bounds "
                r"Named & Aged for T"):
            G.check(PRELUDE + NAME_ONLY
                    + 'print tag<NameOnly>(NameOnly("x"))\n')

    def test_generic_to_generic_forwards_intersection_bounds(self):
        src = (PRELUDE + PERSON
               + 'fn relay<U: Named & Aged>(x: U) -> Text { '
                 'return tag<U>(x) }\n'
               + 'print relay(Person("g", 9))\n')
        self.assertEqual(G.run(src).out, ["g"])
        self.assertEqual(
            gama_ir.run_program(G.check(src)).out,
            G.run(src).out)


class DeclarationErrorTests(unittest.TestCase):
    def test_unknown_component_is_named(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"unknown bound Nope for type parameter T"):
            G.check('record Named { name: Text }\n'
                    'fn bad<T: Named & Nope>(x: T) -> Text { '
                    'return x.name }\n')

    def test_duplicate_component_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"duplicate bound Named for type parameter T"):
            G.check('record Named { name: Text }\n'
                    'fn dup<T: Named & Named>(x: T) -> Text { '
                    'return x.name }\n')

    def test_mixing_intersection_and_union_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaError, r"cannot mix '&' and '\|' in bounds"):
            G.check(
                'record Named { name: Text }\n'
                'record Aged { age: Int }\n'
                'fn bad<T: Named & Aged | Named>(x: T) -> Text { '
                'return x.name }\n')

    def test_union_bounds_keep_any_semantics(self):
        src = ('record Named { name: Text }\n'
               'record Aged { age: Int }\n'
               'fn either<T: Named | Aged>(x: T) -> Int { return 1 }\n'
               'print either(Named("n"))\n'
               'print either(Aged(2))\n')
        self.assertEqual(G.run(src).out, ["1", "1"])

    def test_intersection_bounds_on_records_are_rejected(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"intersection bounds are only supported on functions"):
            G.check('record Box<T: Named & Aged> { value: T }\n')


class FormatterAndIrTests(unittest.TestCase):
    SOURCE = (PRELUDE + PERSON
              + 'print tag(Person("a", 3))\n'
              + 'print tag<Person>(Person("b", 4))\n')

    def test_formatter_emits_and_round_trips_intersection_bounds(self):
        out = gama_fmt.format_source(self.SOURCE)
        self.assertIn("fn tag<T: Named & Aged>", out)
        self.assertEqual(out, gama_fmt.format_source(out))
        fn = [n for n in G.parse(out) if getattr(n, "name", None) == "tag"][0]
        self.assertEqual(fn.type_param_bounds, {"T": "Named & Aged"})

    def test_ir_erases_bounds_and_matches_interpreter(self):
        expected = G.run(self.SOURCE).out
        self.assertEqual(
            gama_ir.run_program(G.check(self.SOURCE)).out, expected)


if __name__ == "__main__":
    unittest.main()
