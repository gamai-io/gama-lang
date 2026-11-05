"""Single nominal upper bounds on generic record and enum type parameters.

Syntax::

    record Box<T: Text> { value: T }
    enum Opt<T: Int> { Some(T), None }

A bound is one base type (``Int``/``Decimal``/``Text``/``Bool``) or a declared
record/enum used without type arguments.  Exactly one bound per parameter; the
declaration may appear later in the file.  Bounds are a checker-only contract:
runtime erasure is unchanged (``T`` -> ``Any`` and ``Box<Int>`` -> ``Box``),
and the IR compiler erases them too.  Annotations (``Box<Int>``),
constructors (inferred ``Box(1)`` / ``Some(5)`` and explicit ``Box<Int>(1)``)
and record method ``self`` types are checked against the bounds.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir

BOX = 'record Box<T: Text> { value: T }\n'
OPT = 'enum Opt<T: Int> { Some(T), None }\n'


class RecordBoundTests(unittest.TestCase):
    def test_record_bound_accepts_matching_annotation(self):
        it = G.run(BOX + 'let b: Box<Text> = Box("x")\nprint b.value\n')
        self.assertEqual(it.out, ["x"])

    def test_record_bound_rejects_mismatched_annotation(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Box: type argument Int != bound Text for T"):
            G.check(BOX + 'let b: Box<Int> = Box(1)\n')

    def test_record_bound_is_enforced_without_annotation(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Box: type parameter T Int != bound Text"):
            G.check(BOX + 'print Box(1)\n')

    def test_explicit_constructor_type_argument_checks_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Box: type argument Int != bound Text for T"):
            G.check(BOX + 'print Box<Int>(1)\n')

    def test_explicit_constructor_type_argument_accepts_bound(self):
        it = G.run(BOX + 'print Box<Text>("ok")\n')
        self.assertEqual(it.out, ['Box { value: "ok" }'])

    def test_annotation_arity_is_still_checked(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"Box expects 1 type argument, got 2"):
            G.check(BOX + 'let b: Box<Text, Int> = Box("x")\n')

    def test_bound_may_reference_a_record_declared_later(self):
        it = G.run(
            'record Box<T: Base> { value: T }\n'
            'record Base { n: Int }\n'
            'let b: Box<Base> = Box(Base(4))\n'
            'print b.value.n\n')
        self.assertEqual(it.out, ["4"])

    def test_bound_may_reference_an_enum_declared_later(self):
        it = G.run(
            'record Box<T: Color> { value: T }\n'
            'enum Color { Red, Green }\n'
            'let b: Box<Color> = Box(Color.Red)\n'
            'print b.value\n')
        self.assertEqual(it.out, ["Color.Red"])


class EnumBoundTests(unittest.TestCase):
    def test_enum_bound_accepts_matching_annotation(self):
        it = G.run(OPT + 'let o: Opt<Int> = Some(5)\nprint o\n')
        self.assertEqual(it.out, ["Opt.Some(5)"])

    def test_enum_bound_rejects_mismatched_annotation(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Opt: type argument Text != bound Int for T"):
            G.check(OPT + 'let o: Opt<Text> = Some("x")\n')

    def test_enum_constructor_inference_enforces_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Some: type parameter T Text != bound Int"):
            G.check(OPT + 'print Some("x")\n')

    def test_qualified_enum_constructor_inference_enforces_bound(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"bound Int"):
            G.check(OPT + 'print Opt.Some("x")\n')

    def test_nullary_variant_is_unaffected_by_bounds(self):
        it = G.run(OPT + 'let o: Opt<Int> = Opt.None\nprint o\n')
        self.assertEqual(it.out, ["Opt.None"])

    def test_multi_parameter_bounds_are_checked_independently(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Pair: type argument Int != bound Text for B"):
            G.check(
                'enum Pair<A: Int, B: Text> { P(A, B) }\n'
                'let p: Pair<Int, Int> = P(1, 2)\n')

    def test_enum_annotation_arity_is_still_checked(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"Opt expects 1 type argument, got 2"):
            G.check(OPT + 'let o: Opt<Int, Int> = Some(1)\n')


class BoundDeclarationTests(unittest.TestCase):
    def test_unknown_record_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"unknown bound Nope for type parameter T"):
            G.check('record Box<T: Nope> { value: T }\n')

    def test_unknown_enum_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"unknown bound Nope for type parameter T"):
            G.check('enum Opt<T: Nope> { Some(T), None }\n')

    def test_generic_record_bound_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"bound must be a simple type"):
            G.check('record Box<T: List<Int>> { value: T }\n')

    def test_generic_enum_bound_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"bound must be a simple type"):
            G.check('enum Opt<T: Box<Int>> { Some(T), None }\n')

    def test_multiple_bounds_are_rejected(self):
        with self.assertRaises(G.GamaError):
            G.check('record Box<T: Int & Text> { value: T }\n')
        with self.assertRaises(G.GamaError):
            G.check('enum Opt<T: Int & Text> { Some(T) }\n')

    def test_duplicate_type_parameter_with_bounds(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type parameter T"):
            G.check('record Box<T: Text, T: Int> { value: T }\n')
        with self.assertRaisesRegex(
                G.GamaTypeError, r"duplicate type parameter T in enum"):
            G.check('enum Opt<T: Int, T: Text> { Some(T) }\n')

    def test_record_bound_cannot_name_its_own_parameter(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"unknown bound T"):
            G.check('record Box<T: T> { value: T }\n')


class MethodBoundTests(unittest.TestCase):
    def test_method_can_use_the_record_type_parameter(self):
        it = G.run(
            'record Box<T: Text> {\n'
            '  value: T\n'
            '  fn echo(self: Box<T>) -> T { return self.value }\n'
            '}\n'
            'let b: Box<Text> = Box("m")\n'
            'print b.echo()\n')
        self.assertEqual(it.out, ["m"])

    def test_method_self_type_must_satisfy_record_bound(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Box: type argument Int != bound Text for T"):
            G.check(
                'record Box<T: Text> {\n'
                '  value: T\n'
                '  fn bad(self: Box<Int>) -> T { return self.value }\n'
                '}\n')


class NamespacedBoundTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "lib.gm").write_text(
            'record Money { amount: Decimal }\n'
            'record Box<T: Money> { value: T }\n'
            'enum Opt<T: Money> { Some(T), None }\n',
            encoding="utf-8")
        self.main = self.root / "main.gm"

    def tearDown(self):
        self._tmp.cleanup()

    def test_imported_record_and_enum_bounds_accept(self):
        self.main.write_text(
            'import "lib.gm" as geo\n'
            'let b: geo.Box<geo.Money> = geo.Box(geo.Money(5))\n'
            'print b.value.amount\n'
            'let o: geo.Opt<geo.Money> = geo.Opt.Some(geo.Money(7))\n'
            'print o\n', encoding="utf-8")
        self.assertEqual(
            G.run_file(str(self.main)).out,
            ["5", "geo.Opt.Some(geo.Money { amount: 7 })"])

    def test_imported_record_bound_rejects(self):
        self.main.write_text(
            'import "lib.gm" as geo\n'
            'let b: geo.Box<Text> = geo.Box("x")\n', encoding="utf-8")
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"geo\.Box: type argument Text != bound geo\.Money for T"):
            G.check_file(str(self.main))

    def test_imported_enum_constructor_inference_rejects(self):
        self.main.write_text(
            'import "lib.gm" as geo\n'
            'print geo.Opt.Some(1)\n', encoding="utf-8")
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"geo\.Opt\.Some: type parameter T Int != "
                r"bound geo\.Money"):
            G.check_file(str(self.main))


class BoundFormatterTests(unittest.TestCase):
    def test_formatter_emits_record_and_enum_bounds(self):
        out = gama_fmt.format_source(
            'record Box<T: Text>{value:T}\n'
            'enum Opt<T: Int>{Some(T),None}\n')
        self.assertIn("record Box<T: Text> { value: T }", out)
        self.assertIn("enum Opt<T: Int> { Some(T), None }", out)

    def test_formatter_round_trips_bounds(self):
        src = (BOX + OPT
               + 'let b: Box<Text> = Box<Text>("x")\n'
               + 'let o: Opt<Int> = Some(1)\n')
        out = gama_fmt.format_source(src)
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertIn("record Box<T: Text>", out)
        self.assertIn("enum Opt<T: Int>", out)
        self.assertEqual(G.parse(out)[0].type_param_bounds, {"T": "Text"})
        self.assertEqual(G.parse(out)[1].type_param_bounds, {"T": "Int"})


class BoundErasureAndIrTests(unittest.TestCase):
    def test_runtime_erasure_is_unchanged(self):
        it = G.run(
            BOX + OPT
            + 'let b: Box<Text> = Box("x")\n'
            + 'let o: Opt<Int> = Some(1)\n'
            + 'print b\nprint o\n')
        self.assertEqual(it.out, ['Box { value: "x" }', "Opt.Some(1)"])

    def test_ir_erases_bounds_and_matches_interpreter(self):
        src = (
            BOX + OPT
            + 'print Box<Text>("a")\n'
            + 'print Box("b")\n'
            + 'print Some(2)\n'
            + 'print Opt.None\n')
        expected = G.run(src).out
        ast = G.check(src)
        self.assertEqual(gama_ir.run_program(ast).out, expected)

    def test_ir_ignores_bounds_at_definition_only(self):
        ast = G.check(BOX + 'print Box("z")\n')
        self.assertEqual(
            gama_ir.run_program(ast).out, ['Box { value: "z" }'])

    def test_ir_still_rejects_record_methods_with_bounds(self):
        ast = G.check(
            'record Box<T: Text> {\n'
            '  value: T\n'
            '  fn get(self: Box<T>) -> T { return self.value }\n'
            '}\n'
            'print Box("x")\n')
        with self.assertRaisesRegex(gama_ir.IRUnsupported,
                                    r"record methods"):
            gama_ir.run_program(ast)


if __name__ == "__main__":
    unittest.main()
