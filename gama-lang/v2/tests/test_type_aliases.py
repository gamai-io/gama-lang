"""Top-level type aliases.

Syntax::

    type UserId = Int
    type IntBox = Box<Int>
    type PointList = List<Point>
    export type Id = Int as Ident

An alias is a transparent, compile-time-only name for another type.  Aliases
are expanded recursively by the checker, so they work in ``let`` annotations,
parameters, return types, record fields, enum payloads and generic arguments.
Runtime erasure is unchanged: an alias never creates a value, a ``DEF`` or a
type check at run time, and the IR backend emits a ``NOP`` for the declaration.

Limits: generic aliases are covered separately in
``test_generic_aliases.py``, and union aliases (``type Name = Text | Bool``)
are rejected because union types are not
modelled in annotations.  Aliases are type-namespace declarations; they may be
exported (plain, renamed, or through a selective ``export { ... }`` list) and
are visible through plain and aliased imports (``geo.Id``).
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


class TypeAliasTests(unittest.TestCase):
    # -- basic use -------------------------------------------------------
    def test_simple_alias_in_let(self):
        it = G.run('type UserId = Int\nlet id: UserId = 7\nprint id\n')
        self.assertEqual(it.out, ["7"])

    def test_simple_alias_rejects_wrong_value(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Text != UserId"):
            G.check('type UserId = Int\nlet id: UserId = "x"\n')

    def test_generic_target(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type IntBox = Box<Int>\n'
            'let b: IntBox = Box(5)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 5 }"])

    def test_generic_target_checks_concrete_argument(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Box.value: Text != Int"):
            G.check(
                'record Box<T> { value: T }\n'
                'type IntBox = Box<Int>\n'
                'let b: IntBox = Box("x")\n')

    def test_generic_target_arity_is_checked(self):
        for target, message in (
                ("Box", r"Box expects 1 type argument, got 0"),
                ("Box<Int, Text>", r"Box expects 1 type argument, got 2")):
            with self.assertRaisesRegex(G.GamaTypeError, message):
                G.check('record Box<T> { value: T }\ntype X = %s\n' % target)

    def test_nested_aliases(self):
        it = G.run(
            'type A = Int\n'
            'type B = A\n'
            'type C = B\n'
            'let n: C = 3\n'
            'print n\n')
        self.assertEqual(it.out, ["3"])

    def test_nested_alias_inside_generic_target(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'type Coord = Point\n'
            'type Points = List<Coord>\n'
            'let p: Coord = Point(1, 2)\n'
            'print p\n')
        self.assertEqual(it.out, ["Point { x: 1, y: 2 }"])

    def test_alias_as_generic_argument(self):
        it = G.run(
            'type UserId = Int\n'
            'record Box<T> { value: T }\n'
            'let b: Box<UserId> = Box(8)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 8 }"])

    def test_alias_in_generic_function_annotation(self):
        it = G.run(
            'type UserId = Int\n'
            'record Box<T> { value: T }\n'
            'fn wrap<T>(x: T) -> Box<T> { return Box(x) }\n'
            'let b: Box<UserId> = wrap(8)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 8 }"])

    # -- parameters / returns / fields / payloads -----------------------
    def test_parameter_and_return_alias(self):
        it = G.run(
            'type UserId = Int\n'
            'type Name = Text\n'
            'fn label(id: UserId, name: Name) -> Name { return name }\n'
            'print label(1, "ana")\n')
        self.assertEqual(it.out, ["ana"])

    def test_parameter_alias_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"label: id Text != UserId"):
            G.check(
                'type UserId = Int\n'
                'fn label(id: UserId) -> Text { return "x" }\n'
                'print label("x")\n')

    def test_record_field_alias(self):
        it = G.run(
            'type UserId = Int\n'
            'record User { id: UserId }\n'
            'let u: User = User(9)\n'
            'print u.id\n')
        self.assertEqual(it.out, ["9"])

    def test_record_field_generic_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type IntBox = Box<Int>\n'
            'record Holder { b: IntBox }\n'
            'let h: Holder = Holder(Box(5))\n'
            'print h.b\n')
        self.assertEqual(it.out, ["Box { value: 5 }"])

    def test_record_field_alias_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"User.id: Text != Int"):
            G.check(
                'type UserId = Int\n'
                'record User { id: UserId }\n'
                'let u: User = User("x")\n')

    def test_enum_payload_alias(self):
        it = G.run(
            'type UserId = Int\n'
            'enum Ev { Seen(UserId), None }\n'
            'let e: Ev = Ev.Seen(4)\n'
            'match e { Seen(v) => { print v }, None => { print "no" } }\n')
        self.assertEqual(it.out, ["4"])

    def test_enum_payload_alias_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Ev.Seen: payload Text != Int"):
            G.check(
                'type UserId = Int\n'
                'enum Ev { Seen(UserId), None }\n'
                'let e: Ev = Ev.Seen("x")\n')

    def test_method_alias(self):
        it = G.run(
            'type UserId = Int\n'
            'record User { id: Int, fn set(self: User, v: UserId) -> UserId '
            '{ return v } }\n'
            'let u: User = User(1)\n'
            'print u.set(5)\n')
        self.assertEqual(it.out, ["5"])

    # -- checker errors --------------------------------------------------
    def test_duplicate_alias(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type alias A"):
            G.check('type A = Int\ntype A = Text\n')

    def test_unknown_target(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: unknown type Nope"):
            G.check('type A = Nope\n')

    def test_unknown_nested_target(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: unknown type Nope"):
            G.check('type A = List<Nope>\n')

    def test_self_cycle(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: cyclic alias"):
            G.check('type A = A\n')

    def test_mutual_cycle(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"cyclic alias"):
            G.check('type A = B\ntype B = A\n')

    def test_cycle_through_generic_target(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"cyclic alias"):
            G.check('record Box<T> { value: T }\n'
                    'type A = Box<A>\n')

    def test_collision_with_record(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"record A collides with type alias A"):
            G.check('type A = Int\nrecord A { x: Int }\n')

    def test_collision_with_enum(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"enum A collides with type alias A"):
            G.check('enum A { X }\ntype A = Int\n')

    def test_collision_with_builtin_type(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"collides with an existing type name"):
            G.check('type Int = Text\n')

    def test_alias_is_not_generic(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A is not generic"):
            G.check('type A = Int\nlet x: A<Int> = 1\n')

    def test_generic_alias_declaration_is_supported(self):
        ast = G.check(
            'record Box<T> { value: T }\n'
            'type Pair<T> = Box<T>\n')
        alias = next(s for s in ast if isinstance(s, G.TypeAlias))
        self.assertEqual(alias.name, "Pair")
        self.assertEqual(alias.type_params, ["T"])
        self.assertEqual(alias.type_name, "Box<T>")

    def test_union_target_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaError, r"union types are not supported in type aliases"):
            G.check('type Name = Text | Bool\n')

    def test_alias_only_at_top_level(self):
        with self.assertRaisesRegex(G.GamaError, r"only allowed at top level"):
            G.check('fn f() -> Int { type A = Int return 1 }\n')

    # -- exports ---------------------------------------------------------
    def test_exported_alias_round_trips(self):
        src = 'export type UserId = Int\nlet id: UserId = 1\n'
        first = gama_fmt.format_source(src)
        self.assertIn("export type UserId = Int", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_exported_alias_rename_round_trips(self):
        src = 'export type UserId = Int as Id\nlet id: Id = 1\n'
        first = gama_fmt.format_source(src)
        self.assertIn("export type UserId = Int as Id", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_selective_export_name_is_accepted(self):
        ast = G.check('type Id = Int\nexport { Id }\n')
        names = [s.name for s in ast if isinstance(s, G.TypeAlias)]
        self.assertEqual(names, ["Id"])

    def test_plain_import_exposes_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type UserId = Int\n'
                'fn ident(id: UserId) -> UserId { return id }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm"\n'
                'let x: UserId = 5\n'
                'print ident(x)\n')
            self.assertEqual(G.run_file(str(main)).out, ["5"])

    def test_aliased_import_exposes_exported_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export type UserId = Int\n'
                'export fn ident(id: UserId) -> UserId { return id }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let x: geo.UserId = 5\n'
                'print geo.ident(x)\n')
            self.assertEqual(G.run_file(str(main)).out, ["5"])

    def test_aliased_import_renames_alias_declaration_and_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export type Id = Int as Ident\n'
                'export fn ident(x: Id) -> Id { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let x: geo.Ident = 7\n'
                'print geo.ident(x)\n')
            self.assertEqual(G.run_file(str(main)).out, ["7"])

    def test_selective_export_exposes_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type Id = Int\n'
                'fn ident(x: Id) -> Id { return x }\n'
                'export { Id, ident }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let x: geo.Id = 6\n'
                'print geo.ident(x)\n')
            self.assertEqual(G.run_file(str(main)).out, ["6"])

    def test_selective_export_renames_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type Id = Int\n'
                'fn ident(x: Id) -> Id { return x }\n'
                'export { Id as Ident, ident }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let x: geo.Ident = 8\n'
                'print geo.ident(x)\n')
            self.assertEqual(G.run_file(str(main)).out, ["8"])

    def test_unexported_alias_is_private_through_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'type Id = Int\n'
                'export fn ident(x: Id) -> Id { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'print geo.ident(1)\n')
            self.assertEqual(G.run_file(str(main)).out, ["1"])

    # -- formatter / IR --------------------------------------------------
    def test_formatter_round_trips_all_alias_forms(self):
        src = (
            'type UserId = Int\n'
            'type IntBox = Box<Int>\n'
            'type PointList = List<Point>\n'
            'type Alias = UserId\n'
            'record Point { x: Int, y: Int }\n'
            'record Box<T> { value: T }\n'
            'let id: Alias = 1\n')
        first = gama_fmt.format_source(src)
        self.assertIn("type UserId = Int", first)
        self.assertIn("type IntBox = Box<Int>", first)
        self.assertIn("type PointList = List<Point>", first)
        self.assertIn("type Alias = UserId", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_parse_keeps_alias_node(self):
        ast = G.parse('type UserId = Int\n')
        self.assertIsInstance(ast[0], G.TypeAlias)
        self.assertEqual(ast[0].name, "UserId")
        self.assertEqual(ast[0].type_name, "Int")

    def test_alias_is_erased_at_runtime(self):
        src = (
            'type UserId = Int\n'
            'let id: UserId = 7\n'
            'print id\n')
        self.assertEqual(G.run(src).out, ["7"])
        self.assertEqual(IR.run_program(G.check(src)).out, ["7"])

    def test_ir_matches_interpreter_with_aliases(self):
        src = (
            'type UserId = Int\n'
            'type Point = Coord\n'
            'record Coord { x: Int, y: Int }\n'
            'type IntBox = Box<Int>\n'
            'record Box<T> { value: T }\n'
            'fn mk(id: UserId) -> Point { return Coord(id, id) }\n'
            'let p: Point = mk(3)\n'
            'let b: IntBox = Box(4)\n'
            'print p\n'
            'print b\n')
        ast = G.check(src)
        self.assertEqual(IR.run_program(ast).out, G.run(src).out)

    def test_alias_declaration_emits_no_value(self):
        src = 'type A = Int\ntype B = A\n'
        self.assertEqual(G.run(src).out, [])
        self.assertEqual(IR.run_program(G.check(src)).out, [])


if __name__ == "__main__":
    unittest.main()
