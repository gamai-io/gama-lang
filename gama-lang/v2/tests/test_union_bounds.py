"""Union upper bounds on generic named function type parameters.

Syntax::

    fn add<T: Int | Decimal>(a: T, b: T) -> T { return a + b }
    fn show<T: Text | Bool>(x: T) -> Text { return text(x) }

A bound may be a union of one or more alternatives separated by ``|``.  Each
alternative is a base type (``Int``, ``Decimal``, ``Text``, ``Bool``) or a
declared record/enum.  Whitespace and newlines around ``|`` are ignored, and a
single bound (``<T: Text>``) keeps its old meaning.  A type argument or an
inferred argument is accepted when it is compatible with at least one
alternative; ``Int``/``Decimal`` numeric compatibility is preserved per
alternative.  Unknown and duplicate alternatives are rejected.

Bounds remain a checker-only contract: runtime erasure is unchanged (``T`` ->
``Any``), the formatter round-trips ``T: A | B``, and the IR compiler ignores
type parameters, so bounded programs run exactly like the reference
interpreter.  Bounds are only supported on named functions (records and enums
reject them, as before).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir


ADD = 'fn add<T: Int | Decimal>(a: T, b: T) -> T { return a + b }\n'
SHOW = 'fn show<T: Text | Bool>(x: T) -> Text { return text(x) }\n'
SINGLE = 'fn show<T: Text>(x: T) -> Text { return x }\n'


class UnionBoundParsingTests(unittest.TestCase):
    def test_parse_captures_union_bound(self):
        fn = G.parse(ADD)[0]
        self.assertEqual(fn.type_params, ["T"])
        self.assertEqual(fn.type_param_bounds, {"T": "Int | Decimal"})

    def test_parse_union_with_newlines_and_spaces(self):
        src = ('fn add<T:\n    Int\n    |   Decimal\n>(a: T, b: T) -> T '
               '{ return a + b }\n')
        fn = G.parse(src)[0]
        self.assertEqual(fn.type_param_bounds, {"T": "Int | Decimal"})

    def test_parse_mixed_bounded_and_unbounded(self):
        fn = G.parse(
            'fn pair<A, B: Int | Decimal>(a: A, b: B) -> Text '
            '{ return text(a) + text(b) }\n')[0]
        self.assertEqual(fn.type_params, ["A", "B"])
        self.assertEqual(fn.type_param_bounds, {"B": "Int | Decimal"})

    def test_single_bound_is_unchanged(self):
        fn = G.parse(SINGLE)[0]
        self.assertEqual(fn.type_param_bounds, {"T": "Text"})


class UnionAcceptTests(unittest.TestCase):
    def test_add_accepts_int(self):
        it = G.run(ADD + 'print add(2, 3)\n')
        self.assertEqual(it.out, ["5"])

    def test_add_accepts_decimal(self):
        it = G.run(ADD + 'print add(1.5, 2.5)\n')
        self.assertEqual(it.out, ["4.0"])

    def test_show_accepts_text(self):
        it = G.run(SHOW + 'print show("hello")\n')
        self.assertEqual(it.out, ["hello"])

    def test_show_accepts_bool(self):
        it = G.run(SHOW + 'print show(true)\n')
        self.assertEqual(it.out, ["True"])

    def test_show_mixed_arguments_are_independently_inferred(self):
        it = G.run(SHOW + 'print show("a")\nprint show(false)\n')
        self.assertEqual(it.out, ["a", "False"])


class UnionRejectTests(unittest.TestCase):
    def test_add_rejects_bool(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"add: type parameter T Bool != bound Int \| Decimal"):
            G.check(ADD + 'print add(true, false)\n')

    def test_show_rejects_int(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"show: type parameter T Int != bound Text \| Bool"):
            G.check(SHOW + 'print show(1)\n')

    def test_unknown_alternative(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"unknown bound Nope for type parameter T"):
            G.check(
                'fn f<T: Int | Nope>(x: T) -> T { return x }\n')

    def test_duplicate_alternative(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"duplicate bound Int for type parameter T"):
            G.check(
                'fn f<T: Int | Int>(x: T) -> T { return x }\n')

    def test_single_bound_rejection_unchanged(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"show: type parameter T Int != bound Text"):
            G.check(SINGLE + 'print show(1)\n')


class UnionRecordEnumAlternativeTests(unittest.TestCase):
    def test_record_alternative_accepts_both_records(self):
        it = G.run(
            'record Money { amount: Decimal }\n'
            'record Points { amount: Decimal }\n'
            'fn keep<T: Money | Points>(x: T) -> T { return x }\n'
            'let m: Money = keep(Money(5))\n'
            'let p: Points = keep(Points(7))\n'
            'print m.amount\nprint p.amount\n')
        self.assertEqual(it.out, ["5", "7"])

    def test_record_alternative_rejects_unrelated_type(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"bound Money \| Points"):
            G.check(
                'record Money { amount: Decimal }\n'
                'record Points { amount: Decimal }\n'
                'fn keep<T: Money | Points>(x: T) -> T { return x }\n'
                'print keep(1)\n')

    def test_enum_alternative_accepts_enum(self):
        it = G.run(
            'enum Color { Red, Green }\n'
            'fn pick<T: Text | Color>(x: T) -> T { return x }\n'
            'let c: Color = pick(Color.Red)\n'
            'print c\n')
        self.assertEqual(it.out, ["Color.Red"])


class UnionExplicitTypeArgumentTests(unittest.TestCase):
    def test_explicit_int_argument(self):
        it = G.run(ADD + 'print add<Int>(2, 3)\n')
        self.assertEqual(it.out, ["5"])

    def test_explicit_decimal_argument_accepts_int_values(self):
        it = G.run(ADD + 'print add<Decimal>(2, 3)\n')
        self.assertEqual(it.out, ["5"])

    def test_explicit_bool_argument_rejected_for_numeric_union(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"add: type argument Bool != bound Int \| Decimal for T"):
            G.check(ADD + 'print add<Bool>(true, false)\n')

    def test_explicit_bool_argument_accepted_by_show_union(self):
        it = G.run(SHOW + 'print show<Bool>(true)\n')
        self.assertEqual(it.out, ["True"])

    def test_explicit_int_argument_rejected_by_show_union(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"show: type argument Int != bound Text \| Bool for T"):
            G.check(SHOW + 'print show<Int>(1)\n')


class UnionFormatterAndIrTests(unittest.TestCase):
    def test_formatter_emits_union_bounds(self):
        out = gama_fmt.format_source(ADD + SHOW)
        self.assertIn("fn add<T: Int | Decimal>", out)
        self.assertIn("fn show<T: Text | Bool>", out)

    def test_formatter_round_trips_union_bounds(self):
        src = ADD + SHOW + 'print add(1, 2)\nprint show(true)\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out))
        fn = G.parse(out)[0]
        self.assertEqual(fn.type_param_bounds, {"T": "Int | Decimal"})

    def test_ir_erases_bounds_and_matches_interpreter(self):
        src = (ADD + SHOW +
               'print add(2, 3)\n'
               'print add(1.5, 2.5)\n'
               'print show("z")\n'
               'print show(true)\n')
        expected = G.run(src).out
        got = gama_ir.run_program(G.check(src)).out
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
