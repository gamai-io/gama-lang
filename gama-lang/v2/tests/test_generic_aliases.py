"""Generic top-level type aliases.

Syntax::

    type Boxed<T> = Box<T>
    type PairT<A, B> = Pair<A, B>
    type IntBox = Boxed<Int>

A generic alias is a transparent, compile-time-only name.  When it is used,
its declared type parameters are substituted with the arguments at the use
site and the target is expanded recursively: ``Boxed<Int>`` becomes
``Box<Int>`` and ``Boxed<Boxed<Int>>`` becomes ``Box<Box<Int>>``.

Type parameters declared by an alias shadow global aliases with the same name
inside its target.  Cycles are rejected (including cycles whose arguments grow
such as ``type A<T> = Box<A<T>>``), arity is checked both at declaration and
at every use, and alias parameters travel with exported, renamed, selectively
re-exported and imported aliases.

Like plain aliases, generic aliases are erased at runtime: they never create a
value or a ``DEF``, and the IR backend emits a ``NOP`` for the declaration.
Union targets remain unsupported.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


class GenericTypeAliasTests(unittest.TestCase):
    # -- basic use -------------------------------------------------------
    def test_generic_alias_in_let(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'let b: Boxed<Int> = Box(5)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 5 }"])

    def test_generic_alias_rejects_wrong_value(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Box.value: Text != Int"):
            G.check(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'let b: Boxed<Int> = Box("x")\n')

    def test_generic_alias_with_two_params(self):
        it = G.run(
            'record Pair<A, B> { first: A, second: B }\n'
            'type PairT<A, B> = Pair<A, B>\n'
            'let p: PairT<Int, Text> = Pair(1, "x")\n'
            'print p\n')
        self.assertEqual(it.out, ['Pair { first: 1, second: "x" }'])

    def test_non_generic_alias_of_generic_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'type IntBox = Boxed<Int>\n'
            'let b: IntBox = Box(7)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 7 }"])

    def test_nested_generic_alias_use(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'let b: Boxed<Boxed<Int>> = Box(Box(2))\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: Box { value: 2 } }"])

    def test_alias_chain(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type A<T> = Box<T>\n'
            'type B<T> = A<T>\n'
            'let b: B<Int> = Box(3)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 3 }"])

    def test_type_param_shadows_global_alias(self):
        it = G.run(
            'type T = Int\n'
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'let b: Boxed<Text> = Box("s")\n'
            'print b\n')
        self.assertEqual(it.out, ['Box { value: "s" }'])

    def test_alias_in_generic_argument(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'type IntBox = Boxed<Int>\n'
            'let b: Box<IntBox> = Box(Box(4))\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: Box { value: 4 } }"])

    # -- parameters / returns / fields / payloads -----------------------
    def test_parameter_and_return_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'fn ident(b: Boxed<Int>) -> Boxed<Int> { return b }\n'
            'print ident(Box(6))\n')
        self.assertEqual(it.out, ["Box { value: 6 }"])

    def test_parameter_alias_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"ident: b Text != Box"):
            G.check(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'fn ident(b: Boxed<Int>) -> Boxed<Int> { return b }\n'
                'print ident("x")\n')

    def test_record_field_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'type IntBox = Boxed<Int>\n'
            'record Holder { b: IntBox }\n'
            'let h: Holder = Holder(Box(8))\n'
            'print h.b\n')
        self.assertEqual(it.out, ["Box { value: 8 }"])

    def test_record_field_alias_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Holder.b: Text != Box"):
            G.check(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'type IntBox = Boxed<Int>\n'
                'record Holder { b: IntBox }\n'
                'let h: Holder = Holder("x")\n')

    def test_nested_generic_field_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'record Holder { b: Box<Boxed<Int>> }\n'
            'let h: Holder = Holder(Box(Box(9)))\n'
            'print h.b\n')
        self.assertEqual(it.out, ["Box { value: Box { value: 9 } }"])

    def test_enum_payload_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'enum Ev { Seen(Boxed<Int>), Empty }\n'
            'let e: Ev = Ev.Seen(Box(3))\n'
            'match e { Seen(v) => { print v }, Empty => { print "no" } }\n')
        self.assertEqual(it.out, ["Box { value: 3 }"])

    def test_enum_payload_alias_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Ev.Seen: payload Text != Box"):
            G.check(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'enum Ev { Seen(Boxed<Int>), Empty }\n'
                'let e: Ev = Ev.Seen("x")\n')

    def test_method_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'record Holder { fn get(self: Holder, b: Boxed<Int>) -> '
            'Boxed<Int> { return b } }\n'
            'let h: Holder = Holder()\n'
            'print h.get(Box(5))\n')
        self.assertEqual(it.out, ["Box { value: 5 }"])

    def test_generic_function_annotation_alias(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'fn wrap<T>(x: T) -> Boxed<T> { return Box(x) }\n'
            'let b: Boxed<Int> = wrap(10)\n'
            'print b\n')
        self.assertEqual(it.out, ["Box { value: 10 }"])

    # -- declaration errors ---------------------------------------------
    def test_duplicate_alias(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type alias A"):
            G.check('type A<T> = Int\ntype A<U> = Text\n')

    def test_duplicate_type_parameter(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"type alias A: duplicate type parameter T"):
            G.check('type A<T, T> = Int\n')

    def test_unknown_target(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: unknown type Nope"):
            G.check('type A<T> = Nope\n')

    def test_unknown_target_in_generic_argument(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: unknown type Nope"):
            G.check('type A<T> = List<Nope>\n')

    def test_unknown_type_parameter_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: unknown type U"):
            G.check('type A<T> = U\n')

    def test_self_cycle(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A: cyclic alias"):
            G.check('type A<T> = A<T>\n')

    def test_mutual_cycle(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"cyclic alias"):
            G.check('type A<T> = B<T>\ntype B<U> = A<U>\n')

    def test_cycle_through_generic_target(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"cyclic alias"):
            G.check('record Box<T> { value: T }\n'
                    'type A<T> = Box<A<T>>\n')

    def test_growing_cycle_is_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"cyclic alias"):
            G.check('record Box<T> { value: T }\n'
                    'type A<T> = A<Box<T>>\n')

    def test_target_arity_is_checked(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"type alias A: Box expects 1 type argument, got 2"):
            G.check('record Box<T> { value: T }\n'
                    'type A<T> = Box<T, T>\n')

    def test_alias_use_without_arguments(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"type alias Boxed expects 1 type argument, got 0"):
            G.check('record Box<T> { value: T }\n'
                    'type Boxed<T> = Box<T>\n'
                    'let b: Boxed = Box(1)\n')

    def test_alias_use_with_too_many_arguments(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"type alias Boxed expects 1 type argument, got 2"):
            G.check('record Box<T> { value: T }\n'
                    'type Boxed<T> = Box<T>\n'
                    'let b: Boxed<Int, Text> = Box(1)\n')

    def test_non_generic_alias_still_rejects_arguments(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"type alias A is not generic"):
            G.check('type A = Int\nlet x: A<Int> = 1\n')

    def test_duplicate_named_type_parameter(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"type alias PairT: duplicate type parameter dup"):
            G.check('type PairT<dup, dup> = Int\n')

    def test_collision_with_record(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"record A collides with type alias A"):
            G.check('type A<T> = Int\nrecord A { x: Int }\n')

    def test_collision_with_builtin_type(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"collides with an existing type name"):
            G.check('type Int<T> = Text\n')

    def test_union_target_is_rejected(self):
        with self.assertRaisesRegex(
                G.GamaError, r"union types are not supported in type aliases"):
            G.check('type A<T> = Text | Bool\n')

    def test_alias_only_at_top_level(self):
        with self.assertRaisesRegex(G.GamaError, r"only allowed at top level"):
            G.check('fn f() -> Int { type A<T> = Int return 1 }\n')

    # -- exports / imports ----------------------------------------------
    def test_exported_generic_alias_round_trips(self):
        src = ('record Box<T> { value: T }\n'
               'export type Boxed<T> = Box<T>\n'
               'let b: Boxed<Int> = Box(1)\n')
        first = gama_fmt.format_source(src)
        self.assertIn("export type Boxed<T> = Box<T>", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_exported_generic_alias_rename_round_trips(self):
        src = ('record Box<T> { value: T }\n'
               'export type Boxed<T> = Box<T> as Bx\n'
               'let b: Bx<Int> = Box(1)\n')
        first = gama_fmt.format_source(src)
        self.assertIn("export type Boxed<T> = Box<T> as Bx", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_selective_export_name_is_accepted(self):
        ast = G.check('record Box<T> { value: T }\n'
                      'type Boxed<T> = Box<T>\n'
                      'export { Boxed }\n')
        names = [s.name for s in ast if isinstance(s, G.TypeAlias)]
        self.assertEqual(names, ["Boxed"])
        alias = next(s for s in ast if isinstance(s, G.TypeAlias))
        self.assertEqual(alias.type_params, ["T"])

    def test_plain_import_exposes_generic_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'fn mk(x: Boxed<Int>) -> Boxed<Int> { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm"\n'
                'let b: Boxed<Int> = mk(Box(5))\n'
                'print b\n')
            self.assertEqual(G.run_file(str(main)).out,
                             ["Box { value: 5 }"])

    def test_aliased_import_exposes_exported_generic_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export record Box<T> { value: T }\n'
                'export type Boxed<T> = Box<T>\n'
                'export fn mk(x: Boxed<Int>) -> Boxed<Int> { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let b: geo.Boxed<Int> = geo.mk(geo.Box(1))\n'
                'print b\n')
            self.assertEqual(G.run_file(str(main)).out,
                             ["geo.Box { value: 1 }"])

    def test_aliased_import_renames_generic_alias_declaration_and_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export record Box<T> { value: T }\n'
                'export type Boxed<T> = Box<T> as Bx\n'
                'export fn mk(x: Boxed<Int>) -> Boxed<Int> { return x }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let b: geo.Bx<Int> = geo.mk(geo.Box(2))\n'
                'print b\n')
            self.assertEqual(G.run_file(str(main)).out,
                             ["geo.Box { value: 2 }"])

    def test_selective_export_exposes_generic_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'fn mk(x: Boxed<Int>) -> Boxed<Int> { return x }\n'
                'export { Box, Boxed, mk }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let b: geo.Boxed<Int> = geo.mk(geo.Box(3))\n'
                'print b\n')
            self.assertEqual(G.run_file(str(main)).out,
                             ["geo.Box { value: 3 }"])

    def test_selective_export_renames_generic_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'fn mk(x: Boxed<Int>) -> Boxed<Int> { return x }\n'
                'export { Box, Boxed as Bx, mk }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let b: geo.Bx<Int> = geo.mk(geo.Box(4))\n'
                'print b\n')
            self.assertEqual(G.run_file(str(main)).out,
                             ["geo.Box { value: 4 }"])

    def test_aliased_import_keeps_alias_type_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export type T = Int\n'
                'export type Id<T> = T\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let x: geo.Id<Text> = "s"\n'
                'print x\n')
            self.assertEqual(G.run_file(str(main)).out, ["s"])

    def test_unexported_generic_alias_is_private_through_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'record Box<T> { value: T }\n'
                'type Boxed<T> = Box<T>\n'
                'export fn mk(x: Int) -> Boxed<Int> { return Box(x) }\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'print geo.mk(1)\n')
            self.assertEqual(G.run_file(str(main)).out,
                             ["geo::private::Box { value: 1 }"])

    def test_imported_alias_arity_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "lib.gm").write_text(
                'export record Box<T> { value: T }\n'
                'export type Boxed<T> = Box<T>\n')
            main = d / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let b: geo.Boxed = geo.Box(1)\n')
            with self.assertRaisesRegex(
                    G.GamaTypeError,
                    r"type alias geo\.Boxed expects 1 type argument, got 0"):
                G.run_file(str(main))

    # -- formatter / IR --------------------------------------------------
    def test_formatter_round_trips_all_generic_alias_forms(self):
        src = (
            'type Boxed<T> = Box<T>\n'
            'type PairT<A, B> = Pair<A, B>\n'
            'type IntBox = Boxed<Int>\n'
            'type Nested<T> = Box<Boxed<T>>\n'
            'record Box<T> { value: T }\n'
            'record Pair<A, B> { first: A, second: B }\n'
            'let b: Nested<Int> = Box(Box(1))\n')
        first = gama_fmt.format_source(src)
        self.assertIn("type Boxed<T> = Box<T>", first)
        self.assertIn("type PairT<A, B> = Pair<A, B>", first)
        self.assertIn("type IntBox = Boxed<Int>", first)
        self.assertIn("type Nested<T> = Box<Boxed<T>>", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    def test_parse_keeps_alias_and_type_params(self):
        ast = G.parse('type PairT<A, B> = Pair<A, B>\n')
        self.assertIsInstance(ast[0], G.TypeAlias)
        self.assertEqual(ast[0].name, "PairT")
        self.assertEqual(ast[0].type_name, "Pair<A, B>")
        self.assertEqual(ast[0].type_params, ["A", "B"])

    def test_alias_is_erased_at_runtime(self):
        src = (
            'record Box<T> { value: T }\n'
            'type Boxed<T> = Box<T>\n'
            'let b: Boxed<Int> = Box(7)\n'
            'print b\n')
        self.assertEqual(G.run(src).out, ["Box { value: 7 }"])
        self.assertEqual(IR.run_program(G.check(src)).out,
                         ["Box { value: 7 }"])

    def test_ir_matches_interpreter_with_generic_aliases(self):
        src = (
            'record Box<T> { value: T }\n'
            'record Pair<A, B> { first: A, second: B }\n'
            'type Boxed<T> = Box<T>\n'
            'type PairT<A, B> = Pair<A, B>\n'
            'type IntBox = Boxed<Int>\n'
            'fn mk(x: IntBox) -> PairT<Int, IntBox> {\n'
            '  return Pair(1, x)\n'
            '}\n'
            'let p: PairT<Int, IntBox> = mk(Box(2))\n'
            'print p\n')
        ast = G.check(src)
        self.assertEqual(IR.run_program(ast).out, G.run(src).out)

    def test_generic_alias_declaration_emits_no_value(self):
        src = ('record Box<T> { value: T }\n'
               'type Boxed<T> = Box<T>\n'
               'type IntBox = Boxed<Int>\n')
        self.assertEqual(G.run(src).out, [])
        self.assertEqual(IR.run_program(G.check(src)).out, [])


if __name__ == "__main__":
    unittest.main()
