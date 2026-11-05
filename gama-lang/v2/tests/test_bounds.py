"""Single nominal upper bounds on generic named function type parameters.

Syntax::

    fn show<T: Text>(x: T) -> Text { return x }
    fn numeric<T: Int>(x: T) -> Int { return x + 1 }

A bound is one of the base types ``Int``, ``Decimal``, ``Text`` or ``Bool``,
or a declared record/enum.  Exactly one bound per parameter; there are no
multiple bounds, no bounds on records/enums, no higher-kinded bounds, and no
generic lambdas.  Bounds are a checker-only contract: runtime erasure is
unchanged (``T`` -> ``Any``), explicit call type arguments are checked against
the bound, and inferred calls accept values compatible with the bound
(``Int``/``Decimal`` are mutually compatible for a numeric bound).  A value
whose static type is already erased to ``Any`` (a type parameter from an
enclosing generic function) is accepted because its concrete type is unknown.

The IR compiler ignores type parameters entirely, so bounds are erased there
as well and programs run exactly like the reference interpreter.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir


SHOW = 'fn show<T: Text>(x: T) -> Text { return x }\n'
NUMERIC = 'fn numeric<T: Int>(x: T) -> Int { return x + 1 }\n'


class BoundParsingTests(unittest.TestCase):
    def test_parse_captures_names_and_bounds(self):
        fn = G.parse(SHOW)[0]
        self.assertEqual(fn.type_params, ["T"])
        self.assertEqual(fn.type_param_bounds, {"T": "Text"})

    def test_parse_mixed_bounded_and_unbounded_parameters(self):
        fn = G.parse(
            'fn pair<A, B: Text>(a: A, b: B) -> Text '
            '{ return text(a) + b }\n')[0]
        self.assertEqual(fn.type_params, ["A", "B"])
        self.assertEqual(fn.type_param_bounds, {"B": "Text"})

    def test_unbounded_generic_has_empty_bounds(self):
        fn = G.parse('fn id<T>(x: T) -> T { return x }\n')[0]
        self.assertEqual(fn.type_params, ["T"])
        self.assertEqual(fn.type_param_bounds, {})

    def test_bound_on_record_is_rejected(self):
        with self.assertRaisesRegex(G.GamaError, r"bounds are only supported"):
            G.check('record Box<T: Int> { v: T }\n')

    def test_bound_on_enum_is_rejected(self):
        with self.assertRaisesRegex(G.GamaError, r"bounds are only supported"):
            G.check('enum Box<T: Int> { Empty }\n')

    def test_multiple_bounds_are_rejected(self):
        with self.assertRaises(G.GamaError):
            G.check('fn f<T: Int & Text>(x: T) -> T { return x }\n')


class ShowBoundTests(unittest.TestCase):
    def test_show_accepts_text(self):
        it = G.run(SHOW + 'print show("hello")\n')
        self.assertEqual(it.out, ["hello"])

    def test_show_accepts_erased_generic_parameter(self):
        it = G.run(
            SHOW +
            'fn outer<U>(u: U) -> Text { return show(u) }\n'
            'print outer("via-generic")\n')
        self.assertEqual(it.out, ["via-generic"])

    def test_show_accepts_explicit_outer_type_parameter(self):
        it = G.run(
            SHOW +
            'fn outer<U>(u: U) -> Text { return show<U>(u) }\n'
            'print outer("via-explicit")\n')
        self.assertEqual(it.out, ["via-explicit"])

    def test_show_rejects_int(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"show: type parameter T Int != bound Text"):
            G.check(SHOW + 'print show(1)\n')

    def test_show_rejects_bool(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"bound Text"):
            G.check(SHOW + 'print show(true)\n')


class NumericBoundTests(unittest.TestCase):
    def test_numeric_accepts_int(self):
        it = G.run(NUMERIC + 'print numeric(1)\n')
        self.assertEqual(it.out, ["2"])

    def test_numeric_accepts_decimal(self):
        it = G.run(NUMERIC + 'print numeric(1.5)\n')
        self.assertEqual(it.out, ["2.5"])

    def test_numeric_rejects_text(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"numeric: type parameter T Text != bound Int"):
            G.check(NUMERIC + 'print numeric("no")\n')

    def test_decimal_bound_accepts_int_and_decimal(self):
        it = G.run(
            'fn n<T: Decimal>(x: T) -> T { return x }\n'
            'print n(2)\nprint n(2.5)\n')
        self.assertEqual(it.out, ["2", "2.5"])


class RecordAndEnumBoundTests(unittest.TestCase):
    def test_record_bound_accepts_record(self):
        it = G.run(
            'record Money { amount: Decimal }\n'
            'fn keep<T: Money>(x: T) -> T { return x }\n'
            'let m: Money = keep(Money(5))\n'
            'print m.amount\n')
        self.assertEqual(it.out, ["5"])

    def test_record_bound_rejects_unrelated_type(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"bound Money"):
            G.check(
                'record Money { amount: Decimal }\n'
                'fn keep<T: Money>(x: T) -> T { return x }\n'
                'print keep(1)\n')

    def test_enum_bound_accepts_enum(self):
        it = G.run(
            'enum Color { Red, Green }\n'
            'fn pick<T: Color>(x: T) -> T { return x }\n'
            'let c: Color = pick(Color.Red)\n'
            'print c\n')
        self.assertEqual(it.out, ["Color.Red"])

    def test_enum_bound_rejects_unrelated_type(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"bound Color"):
            G.check(
                'enum Color { Red, Green }\n'
                'fn pick<T: Color>(x: T) -> T { return x }\n'
                'print pick(1)\n')


class AliasedBoundTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.main = self.root / "main.gm"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, main, lib):
        (self.root / "lib.gm").write_text(lib, encoding="utf-8")
        self.main.write_text(main, encoding="utf-8")
        return G.run_file(str(self.main))

    def test_imported_record_bound_is_renamed_with_the_alias(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'let m: geo.Money = geo.keep(geo.Money(5))\n'
            'print m.amount\n',
            'record Money { amount: Decimal }\n'
            'fn keep<T: Money>(x: T) -> T { return x }\n')
        self.assertEqual(it.out, ["5"])


class BoundDiagnosticTests(unittest.TestCase):
    def test_unknown_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"unknown bound Nope for type parameter T"):
            G.check('fn f<T: Nope>(x: T) -> T { return x }\n')

    def test_generic_bound_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"bound must be a simple type"):
            G.check('fn f<T: List<Int>>(x: T) -> T { return x }\n')

    def test_duplicate_type_parameter_still_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type parameter T"):
            G.check('fn f<T: Text, T: Text>(x: T) -> T { return x }\n')

    def test_explicit_type_argument_must_satisfy_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"show: type argument Int != bound Text"):
            G.check(SHOW + 'print show<Int>("x")\n')

    def test_explicit_numeric_type_argument_accepts_decimal(self):
        it = G.run(NUMERIC + 'print numeric<Decimal>(2.5)\n')
        self.assertEqual(it.out, ["3.5"])

    def test_explicit_type_argument_rejects_text_for_numeric(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"bound Int"):
            G.check(NUMERIC + 'print numeric<Text>(2)\n')


class BoundFormatterAndIrTests(unittest.TestCase):
    def test_formatter_emits_bounds(self):
        out = gama_fmt.format_source(SHOW + NUMERIC)
        self.assertIn("fn show<T: Text>(x: T) -> Text", out)
        self.assertIn("fn numeric<T: Int>(x: T) -> Int", out)

    def test_formatter_round_trips_bounds(self):
        src = SHOW + NUMERIC + 'print show("x")\nprint numeric(1)\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out))
        fn = G.parse(out)[0]
        self.assertEqual(fn.type_param_bounds, {"T": "Text"})
        self.assertIn("fn show<T: Text>", out)

    def test_ir_erases_bounds_and_matches_interpreter(self):
        src = (SHOW + NUMERIC +
               'print show("a")\n'
               'print numeric(2)\n'
               'print numeric(2.5)\n')
        expected = G.run(src).out
        got = gama_ir.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def test_ir_ignores_bounds_at_definition_only(self):
        ast = G.check('fn show<T: Text>(x: T) -> Text { return x }\n'
                      'print show("z")\n')
        self.assertEqual(gama_ir.run_program(ast).out, ["z"])


if __name__ == "__main__":
    unittest.main()
