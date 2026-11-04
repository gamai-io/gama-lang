"""Aliased imports expose namespaced enums.

An enum declared in a module imported with ``import "lib.gm" as geo`` becomes
``geo.Enum``; every reference inside the module is renamed too (``Color`` and
``Color.Red`` -> ``geo.Color`` / ``geo.Color.Red``), including constructors,
type annotations and match patterns. Enums stay module-global for explicit
exports (``export enum`` is still rejected), and plain imports keep flattening
enums into the local namespace.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class AliasEnumTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.main = self.root / "main.gm"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def _run(self, main, lib):
        self._write("lib.gm", lib)
        self._write("main.gm", main)
        return G.run_file(str(self.main))

    # -- values, annotations and match -----------------------------------
    def test_aliased_enum_values_annotation_and_match(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'let c: geo.Color = geo.Color.Red\n'
            'print c\n'
            'match c { geo.Color.Red => { print "r" }, _ => { print "x" } }\n',
            'enum Color { Red, Green }\n',
        )
        self.assertEqual(it.out, ["geo.Color.Red", "r"])

    def test_function_returning_namespaced_enum(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'print geo.pick()\n'
            'let c: geo.Color = geo.pick()\n'
            'print c == geo.Color.Red\n',
            'enum Color { Red, Green }\n'
            'fn pick() -> Color { return Color.Red }\n',
        )
        self.assertEqual(it.out, ["geo.Color.Red", "True"])

    def test_nested_alias_enum_references_are_fully_qualified(self):
        self._write("sub.gm", "enum Color { Red, Green }\n")
        self._write(
            "lib.gm",
            'import "sub.gm" as sub\n'
            'let DEFAULT: sub.Color = sub.Color.Red\n'
            'fn pick() -> sub.Color { return sub.Color.Green }\n',
        )
        main = self._write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'print geo.DEFAULT\n'
            'print geo.pick()\n'
            'match geo.pick() {\n'
            '  geo.sub.Color.Green => { print "g" },\n'
            '  _ => { print "x" }\n'
            '}\n',
        )
        it = G.run_file(str(main))
        self.assertEqual(
            it.out,
            ["geo.sub.Color.Red", "geo.sub.Color.Green", "g"],
        )

    def test_payload_enum_constructors_and_patterns_are_renamed(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'let o: geo.Option = geo.Option.Some(5, "a")\n'
            'print o\n'
            'match o {\n'
            '  geo.Option.Some(v, s) => { print v },\n'
            '  geo.Option.None => { print "n" }\n'
            '}\n'
            'print geo.wrap(7)\n',
            'enum Option { Some(Int, Text), None }\n'
            'fn wrap(n: Int) -> Option { return Option.Some(n, "x") }\n',
        )
        self.assertEqual(
            it.out,
            ['geo.Option.Some(5, "a")', "5", 'geo.Option.Some(7, "x")'],
        )

    # -- collisions and missing / ambiguous references --------------------
    def test_collision_with_existing_top_level_name(self):
        self._write('lib.gm', 'enum Color { Red }\n')
        self._write(
            'main.gm',
            'let geo.Color = 1\n'
            'import "lib.gm" as geo\n'
            'print geo.Color.Red\n',
        )
        with self.assertRaisesRegex(
            G.GamaError, "import alias 'geo' produces duplicate name 'geo.Color'"
        ):
            G.run_file(str(self.main))

    def test_missing_variant_is_reported(self):
        self._write('lib.gm', 'enum Color { Red }\n')
        self._write(
            'main.gm',
            'import "lib.gm" as geo\nprint geo.Color.Purple\n',
        )
        with self.assertRaisesRegex(G.GamaTypeError, "unknown enum variant"):
            G.run_file(str(self.main))

    def test_bare_variant_becomes_ambiguous_across_two_aliases(self):
        self._write('one.gm', 'enum Color { Red }\n')
        self._write('two.gm', 'enum Color { Red }\n')
        self._write(
            'main.gm',
            'import "one.gm" as a\n'
            'import "two.gm" as b\n'
            'let c: a.Color = a.Color.Red\n'
            'match c { Red => { print "r" } _ => { print "x" } }\n',
        )
        with self.assertRaisesRegex(G.GamaTypeError, "ambiguous enum variant"):
            G.run_file(str(self.main))

    # -- plain import and explicit exports -------------------------------
    def test_plain_import_keeps_enum_global(self):
        self._write('lib.gm', 'enum Color { Red, Green }\n')
        self._write(
            'main.gm',
            'import "lib.gm"\n'
            'let c: Color = Color.Green\n'
            'print c\n'
            'match c { Color.Red => { print "r" }, _ => { print "g" } }\n',
        )
        it = G.run_file(str(self.main))
        self.assertEqual(it.out, ["Color.Green", "g"])

    def test_enums_stay_visible_with_explicit_exports(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'print geo.pick()\n'
            'print geo.Color.Red\n',
            'enum Color { Red, Green }\n'
            'export fn pick() -> Color { return Color.Green }\n',
        )
        self.assertEqual(it.out, ["geo.Color.Green", "geo.Color.Red"])

    def test_export_enum_remains_rejected(self):
        self._write('main.gm', 'export enum Color { Red }\n')
        with self.assertRaisesRegex(G.GamaError, "export enum is not supported"):
            G.check_file(str(self.main))

    # -- formatter and IR -------------------------------------------------
    def test_formatter_keeps_namespaced_enum_references(self):
        src = (
            'import "lib.gm" as geo\n'
            'let c: geo.Color = geo.Color.Red\n'
            'match c { geo.Color.Red => { print "r" }, _ => { print "x" } }\n'
        )
        once = G.fmt_source(src)
        twice = G.fmt_source(once)
        self.assertEqual(once, twice)
        self.assertIn("geo.Color.Red", once)
        self.assertIn("let c: geo.Color =", once)
        # The AST formatter canonicalises arrows to block arms, but the
        # namespaced pattern itself must survive.
        self.assertIn("geo.Color.Red {", once)
        self.assertNotIn("geo.Color.Red =>", once)
        # Formatting is semantics-preserving: the formatted program still runs.
        self._write("lib.gm", "enum Color { Red, Green }\n")
        before = self._write("before.gm", src)
        expected = G.run_file(str(before)).out
        after = self._write("after.gm", once)
        self.assertEqual(G.run_file(str(after)).out, expected)

    def test_ir_runs_namespaced_nullary_enum_like_interpreter(self):
        self._write('lib.gm', 'enum Color { Red, Green }\n')
        self._write(
            'main.gm',
            'import "lib.gm" as geo\n'
            'print geo.Color.Red\n'
            'match geo.Color.Green {\n'
            '  geo.Color.Red => { print "r" },\n'
            '  _ => { print "other" }\n'
            '}\n',
        )
        expected = G.run_file(str(self.main)).out
        self.assertEqual(IR.run_file(str(self.main)).out, expected)

    def test_ir_rejects_alias_payload_enums_instead_of_miscompiling(self):
        self._write('lib.gm', 'enum Option { Some(Int), None }\n')
        self._write(
            'main.gm',
            'import "lib.gm" as geo\n'
            'print geo.Option.Some(1)\n',
        )
        with self.assertRaisesRegex(IR.IRUnsupported, "enum payload variants"):
            IR.run_file(str(self.main))


if __name__ == "__main__":
    unittest.main()
