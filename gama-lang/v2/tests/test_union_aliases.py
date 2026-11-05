"""Union type aliases in annotations.

Syntax::

    type Scalar = Int | Decimal
    type MaybeText = Text | Bool
    let a: Scalar = 1
    let b: Scalar = 1.5
    fn f(x: Scalar) -> Scalar { return x }

A union alias stores its alternatives joined by `` | `` and expands to a
union during checking.  Branches may be base types, records, enums, non-union
aliases, or generic record refs (erased to the nominal base).

Semantics:

* ``let`` annotations, parameters, record fields and enum payloads accept a
  value compatible with *at least one* branch.
* A union return accepts any branch.
* Union-to-union assignment uses branch inclusion; narrowing a value whose
  static type is a union back to a single type is rejected because there is no
  flow-sensitive narrowing.  The existing ``Int``/``Decimal`` interchange-
  ability still applies per branch, so a numeric union may narrow to ``Int``
  or ``Decimal``.
* Aliases erase completely at runtime (IR emits ``NOP``), and unions may be
  exported (plain/renamed/selective) and imported (plain/aliased).

Errors are clear ``GamaError``/``GamaTypeError`` values: missing branch,
unknown branch, duplicate branch, nested union alias, union arity misuse and
cycles.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


SCALAR = 'type Scalar = Int | Decimal\n'
MAYBE = 'type MaybeText = Text | Bool\n'


class UnionAliasParsingTests(unittest.TestCase):
    def test_parse_union_target(self):
        alias = G.parse(SCALAR)[0]
        self.assertIsInstance(alias, G.TypeAlias)
        self.assertEqual(alias.name, "Scalar")
        self.assertEqual(alias.type_name, "Int | Decimal")

    def test_parse_multiline_union(self):
        alias = G.parse('type Scalar =\n    Int\n    | Decimal\n')[0]
        self.assertEqual(alias.type_name, "Int | Decimal")

    def test_single_branch_is_a_plain_alias(self):
        alias = G.parse('type Id = Int\n')[0]
        self.assertEqual(alias.type_name, "Int")

    def test_parse_exported_and_renamed_union(self):
        alias = G.parse('export type Scalar = Int | Decimal as S\n')[0]
        self.assertTrue(alias.exported)
        self.assertEqual(alias.export_name, "S")
        self.assertEqual(alias.type_name, "Int | Decimal")


class UnionAliasAcceptTests(unittest.TestCase):
    def test_let_accepts_int_branch(self):
        self.assertEqual(G.run(SCALAR + 'let a: Scalar = 1\nprint a\n').out,
                         ["1"])

    def test_let_accepts_decimal_branch(self):
        self.assertEqual(G.run(SCALAR + 'let a: Scalar = 1.5\nprint a\n').out,
                         ["1.5"])

    def test_text_bool_union_accepts_each_branch(self):
        it = G.run(MAYBE + 'let a: MaybeText = "x"\n'
                   'let b: MaybeText = true\nprint a\nprint b\n')
        self.assertEqual(it.out, ["x", "True"])

    def test_parameter_accepts_each_branch(self):
        it = G.run(SCALAR + 'fn f(x: Scalar) -> Text { return "ok" }\n'
                   'print f(1)\nprint f(1.5)\n')
        self.assertEqual(it.out, ["ok", "ok"])

    def test_union_return_accepts_any_branch(self):
        it = G.run(
            SCALAR
            + 'fn as_int() -> Scalar { return 1 }\n'
            + 'fn as_decimal() -> Scalar { return 1.5 }\n'
            + 'print as_int()\nprint as_decimal()\n')
        self.assertEqual(it.out, ["1", "1.5"])

    def test_record_field_accepts_each_branch(self):
        it = G.run(
            SCALAR
            + 'record Box { v: Scalar }\n'
            + 'let a: Box = Box(1)\n'
            + 'let b: Box = Box(1.5)\n'
            + 'print a.v\nprint b.v\n')
        self.assertEqual(it.out, ["1", "1.5"])

    def test_enum_payload_accepts_each_branch(self):
        it = G.run(
            SCALAR
            + 'enum Ev { Seen(Scalar), None }\n'
            + 'match Ev.Seen(1) { Seen(v) => { print v }, None => {} }\n'
            + 'match Ev.Seen(1.5) { Seen(v) => { print v }, None => {} }\n')
        self.assertEqual(it.out, ["1", "1.5"])

    def test_record_and_enum_branches(self):
        it = G.run(
            'record A { x: Int }\n'
            'enum C { R, G }\n'
            'type AB = A | C\n'
            'let a: AB = A(1)\n'
            'let c: AB = C.R\n'
            'print a\nprint c\n')
        self.assertEqual(it.out, ["A { x: 1 }", "C.R"])

    def test_alias_branch_expands(self):
        it = G.run(
            'type UserId = Int\n'
            'type Scalar = UserId | Decimal\n'
            'let a: Scalar = 2\nprint a\n')
        self.assertEqual(it.out, ["2"])

    def test_generic_branch_erases_to_nominal_base(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type B = Box<Int> | Text\n'
            'let b: B = Box(3)\n'
            'let t: B = "s"\n'
            'print b\nprint t\n')
        self.assertEqual(it.out, ["Box { value: 3 }", "s"])

    def test_plain_alias_to_union_is_transparent(self):
        it = G.run(
            'type Scalar = Int | Text\n'
            'type Alias = Scalar\n'
            'let a: Alias = "x"\nprint a\n')
        self.assertEqual(it.out, ["x"])

    def test_union_to_same_union_assignment(self):
        it = G.run(
            SCALAR
            + 'type Other = Int | Decimal\n'
            + 'let a: Scalar = 1\n'
            + 'let b: Other = a\nprint b\n')
        self.assertEqual(it.out, ["1"])

    def test_numeric_union_may_narrow_to_int(self):
        # Int/Decimal interchangeability is preserved per branch.
        it = G.run(SCALAR + 'let a: Scalar = 1\nlet b: Int = a\nprint b\n')
        self.assertEqual(it.out, ["1"])


class UnionAliasRejectTests(unittest.TestCase):
    def test_let_rejects_unrelated_value(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Text != Scalar"):
            G.check(SCALAR + 'let a: Scalar = "x"\n')

    def test_parameter_rejects_unrelated_value(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"f: x Text != Scalar"):
            G.check(SCALAR + 'fn f(x: Scalar) -> Text { return "ok" }\n'
                    'print f("x")\n')

    def test_record_field_rejects_unrelated_value(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Box.v: Text != Int \| Decimal"):
            G.check(SCALAR + 'record Box { v: Scalar }\nBox("x")\n')

    def test_enum_payload_rejects_unrelated_value(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Ev.Seen: payload Text != Int \| Decimal"):
            G.check(SCALAR + 'enum Ev { Seen(Scalar), None }\n'
                    'let e: Ev = Ev.Seen("x")\n')

    def test_narrowing_text_bool_union_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"b: Text \| Bool != Text"):
            G.check(MAYBE + 'let a: MaybeText = "x"\nlet b: Text = a\n')

    def test_narrowing_to_bool_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"b: Text \| Bool != Bool"):
            G.check(MAYBE + 'let a: MaybeText = true\nlet b: Bool = a\n')


class UnionAliasErrorTests(unittest.TestCase):
    def test_missing_branch_after_bar(self):
        with self.assertRaisesRegex(
                G.GamaError,
                r"union type alias S: expected a branch after '\|'"):
            G.check('type S = Int |\n')

    def test_leading_bar(self):
        with self.assertRaisesRegex(
                G.GamaError,
                r"union type alias S: branch expected before '\|'"):
            G.check('type S = | Int\n')

    def test_unknown_branch(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias S: unknown type Nope"):
            G.check('type S = Int | Nope\n')

    def test_duplicate_branch(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias S: duplicate union branch Int"):
            G.check('type S = Int | Int\n')

    def test_duplicate_branch_after_alias_expansion(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias S: duplicate union branch Int"):
            G.check('type I = Int\ntype S = I | Int\n')

    def test_nested_union_alias(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias B: nested union alias A"):
            G.check('type A = Int | Text\ntype B = A | Bool\n')

    def test_nested_union_alias_through_plain_alias(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias C: nested union alias B"):
            G.check('type A = Int | Text\ntype B = A\ntype C = B | Bool\n')

    def test_union_cycle(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: cyclic alias"):
            G.check('type A = A | Int\n')

    def test_union_alias_is_not_generic(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias S is not generic"):
            G.check('type S = Int | Text\nlet x: S<Int> = 1\n')

    def test_generic_branch_arity_is_checked(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"type alias B: Box expects 1 type argument, got 0"):
            G.check('record Box<T> { value: T }\ntype B = Box | Text\n')


class UnionAliasExportImportTests(unittest.TestCase):
    def test_exported_union_round_trips(self):
        src = 'export type Scalar = Int | Decimal\nlet a: Scalar = 1\n'
        first = gama_fmt.format_source(src)
        self.assertIn("export type Scalar = Int | Decimal", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_selective_export_renames_union(self):
        ast = G.check('type Scalar = Int | Text\nexport { Scalar as Num }\n')
        names = [s.name for s in ast if isinstance(s, G.TypeAlias)]
        self.assertEqual(names, ["Scalar"])

    def test_plain_import_exposes_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type Scalar = Int | Text\n'
                'fn maybe(x: Scalar) -> Scalar { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm"\n'
                'let a: Scalar = "x"\n'
                'print maybe(a)\n')
            self.assertEqual(G.run_file(str(main)).out, ["x"])

    def test_aliased_import_exposes_renamed_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export type Scalar = Int | Decimal as S\n'
                'export fn maybe(x: Scalar) -> Scalar { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let a: geo.S = 1.5\n'
                'print geo.maybe(a)\n')
            self.assertEqual(G.run_file(str(main)).out, ["1.5"])

    def test_aliased_import_keeps_private_branch_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type UserId = Int\n'
                'export type Scalar = UserId | Decimal\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let a: geo.Scalar = 2\n'
                'print a\n')
            self.assertEqual(G.run_file(str(main)).out, ["2"])

    def test_selective_export_exposes_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type Scalar = Int | Text\n'
                'export { Scalar as Num }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let a: geo.Num = "x"\n'
                'print a\n')
            self.assertEqual(G.run_file(str(main)).out, ["x"])


class UnionAliasFormatterIRTests(unittest.TestCase):
    def test_formatter_round_trips_union_forms(self):
        src = (
            'type Scalar =\n'
            '    Int\n'
            '    | Decimal\n'
            'type MaybeText = Text | Bool\n'
            'export type Alias = Scalar | Bool as A\n'
            'let a: Scalar = 1\n')
        first = gama_fmt.format_source(src)
        self.assertIn("type Scalar = Int | Decimal", first)
        self.assertIn("type MaybeText = Text | Bool", first)
        self.assertIn("export type Alias = Scalar | Bool as A", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_ir_matches_interpreter_with_unions(self):
        src = (
            SCALAR
            + MAYBE
            + 'record Box { v: Scalar }\n'
            + 'fn pick(x: Scalar) -> Scalar { return x }\n'
            + 'let a: Scalar = 1\n'
            + 'let b: Scalar = 1.5\n'
            + 'let m: MaybeText = true\n'
            + 'let boxed: Box = Box(b)\n'
            + 'print pick(a)\n'
            + 'print boxed.v\n'
            + 'print m\n')
        ast = G.check(src)
        self.assertEqual(IR.run_program(ast).out, G.run(src).out)

    def test_union_declaration_emits_no_value(self):
        src = SCALAR + 'type Other = Int | Text\n'
        self.assertEqual(G.run(src).out, [])
        self.assertEqual(IR.run_program(G.check(src)).out, [])


if __name__ == "__main__":
    unittest.main()
