"""Module exports: ``export enum`` and renamed exports.

Semantics pinned here:

* ``export enum`` is allowed (including generic and payload enums).
* A renamed export is written ``export <decl> ... as <Ident>``.  For ``fn``
  the canonical position is in the header, after ``effects`` (the formatter
  emits it there); for ``let``/``record``/``enum`` it follows the complete
  declaration (``... = expr as Name`` / ``record R { ... } as Name``).
* Aliased imports expose the renamed name (``geo.plus``); the module keeps
  using its original internal name.  Only exported names are visible once a
  module declares at least one export (enums included).  Modules without any
  ``export`` keep the legacy all-visible surface.
* Plain imports keep flattening under the original declaration names.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class ExportRenameTests(unittest.TestCase):
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

    # -- enum export visibility / filtering ------------------------------
    def test_exported_enum_visible_unexported_enum_hidden(self):
        it = self._run(
            'import "lib.gm" as geo\nprint geo.Color.Green\n',
            'export enum Color { Red, Green }\n'
            'enum Hidden { A, B }\n',
        )
        self.assertEqual(it.out, ["geo.Color.Green"])
        self._write(
            "hidden.gm",
            'import "lib.gm" as geo\nprint geo.Hidden.A\n',
        )
        with self.assertRaisesRegex(G.GamaTypeError, "unknown"):
            G.run_file(str(self.root / "hidden.gm"))

    def test_only_exported_names_survive_filtering(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'fn helper(x: Int) -> Int { return x }\n'
            'let secret = 9\n'
            'enum Hidden { A }\n',
        )
        for ref in ("m.helper(1)", "m.secret", "m.Hidden.A"):
            self._write(
                "main.gm",
                'import "lib.gm" as m\nprint %s\n' % ref,
            )
            with self.assertRaisesRegex(G.GamaError, "unknown"):
                G.run_file(str(self.main))

    # -- renamed fn / let / record / enum --------------------------------
    def test_renamed_exports_exposed_through_alias(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'print geo.plus(2, 3)\n'
            'print geo.PI\n'
            'let p: geo.Pt = geo.Pt(1, 2)\n'
            'print p.x\n'
            'print geo.Hue.Green\n',
            'export enum Color { Red, Green } as Hue\n'
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n'
            'export let pi: Decimal = 3.14 as PI\n'
            'export record Point { x: Int, y: Int } as Pt\n',
        )
        self.assertEqual(it.out, ["5", "3.14", "1", "geo.Hue.Green"])

    def test_original_names_are_not_exposed_through_alias(self):
        self._write(
            "lib.gm",
            'export enum Color { Red } as Hue\n'
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n'
            'export let pi: Decimal = 3.14 as PI\n'
            'export record Point { x: Int, y: Int } as Pt\n',
        )
        for ref in ("geo.add(1, 2)", "geo.pi", "geo.Point(1, 2)",
                    "geo.Color.Red"):
            self._write(
                "main.gm",
                'import "lib.gm" as geo\nprint %s\n' % ref,
            )
            with self.assertRaisesRegex(G.GamaError, "unknown"):
                G.run_file(str(self.main))

    def test_internal_module_code_uses_original_names(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'print geo.twice(4)\n'
            'print geo.plus(2, 3)\n',
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n'
            'export fn twice(x: Int) -> Int { return add(x, x) }\n',
        )
        self.assertEqual(it.out, ["8", "5"])

    def test_internal_enum_references_follow_the_rename(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'print geo.pick()\n'
            'print geo.Hue.Red\n',
            'export enum Color { Red, Green } as Hue\n'
            'export fn pick() -> Color { return Color.Green }\n',
        )
        self.assertEqual(it.out, ["geo.Hue.Green", "geo.Hue.Red"])

    def test_ir_handles_renamed_function_names(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n'
            'export fn twice(x: Int) -> Int { return add(x, x) }\n',
        )
        self._write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'print geo.plus(2, 3)\n'
            'print geo.twice(4)\n',
        )
        expected = G.run_file(str(self.main)).out
        self.assertEqual(IR.run_file(str(self.main)).out, expected)

    # -- collisions -------------------------------------------------------
    def test_duplicate_export_name_rejected(self):
        self._write(
            "main.gm",
            'export fn a() -> Int { return 1 } as same\n'
            'export fn b() -> Int { return 2 } as same\n',
        )
        with self.assertRaisesRegex(G.GamaError, "duplicate export 'same'"):
            G.check_file(str(self.main))

    def test_duplicate_export_name_across_kinds_rejected(self):
        self._write(
            "main.gm",
            'export let x = 1\nexport fn y() -> Int { return 2 } as x\n',
        )
        with self.assertRaisesRegex(G.GamaError, "duplicate export 'x'"):
            G.check_file(str(self.main))

    def test_renamed_target_collides_with_existing_top_level_name(self):
        self._write(
            "main.gm",
            'export fn add(a: Int) -> Int { return a } as helper\n'
            'fn helper(x: Int) -> Int { return x }\n',
        )
        with self.assertRaisesRegex(
            G.GamaError,
            "export name 'helper' collides with existing top-level name",
        ):
            G.check_file(str(self.main))

    def test_import_alias_collides_with_local_top_level_name(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int { return a + b }\n',
        )
        self._write(
            "main.gm",
            'let geo = 1\n'
            'import "lib.gm" as geo\n'
            'print geo.add(1, 2)\n',
        )
        with self.assertRaisesRegex(G.GamaError, "collides with existing"):
            G.run_file(str(self.main))

    # -- legacy (no exports) and plain imports ---------------------------
    def test_no_export_module_keeps_legacy_all_visible(self):
        it = self._run(
            'import "lib.gm" as m\n'
            'print m.add(2, 3)\n'
            'print m.secret\n'
            'print m.Color.Green\n'
            'let p: m.Point = m.Point(1, 2)\n'
            'print p.x\n',
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let secret = 7\n'
            'enum Color { Red, Green }\n'
            'record Point { x: Int, y: Int }\n',
        )
        self.assertEqual(it.out, ["5", "7", "m.Color.Green", "1"])

    def test_plain_import_flattens_original_names(self):
        it = self._run(
            'import "lib.gm"\n'
            'print add(2, 3)\n'
            'print pi\n'
            'print Color.Green\n'
            'let p: Point = Point(1, 2)\n'
            'print p.x\n',
            'export enum Color { Red, Green } as Hue\n'
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n'
            'export let pi: Decimal = 3.14 as PI\n'
            'export record Point { x: Int, y: Int } as Pt\n',
        )
        self.assertEqual(it.out, ["5", "3.14", "Color.Green", "1"])

    def test_plain_import_does_not_expose_renamed_names(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n',
        )
        self._write("main.gm", 'import "lib.gm"\nprint plus(1, 2)\n')
        with self.assertRaisesRegex(G.GamaTypeError, "unknown function plus"):
            G.run_file(str(self.main))

    # -- generic and payload enums ---------------------------------------
    def test_generic_and_payload_enum_export_rename(self):
        it = self._run(
            'import "lib.gm" as geo\n'
            'let o: geo.Maybe<Int> = geo.wrap(5)\n'
            'print o\n'
            'match o {\n'
            '  geo.Maybe.Some(v) => { print v },\n'
            '  geo.Maybe.None => { print "none" }\n'
            '}\n'
            'let d: geo.Duo = geo.Duo.P(1, "x")\n'
            'print d\n',
            'export enum Option<T> { Some(T), None } as Maybe\n'
            'export fn wrap(n: Int) -> Option<Int> { return Option.Some(n) }\n'
            'export enum Pair { P(Int, Text), E } as Duo\n',
        )
        self.assertEqual(
            it.out,
            ['geo.Maybe.Some(5)', "5", 'geo.Duo.P(1, "x")'],
        )

    # -- formatter --------------------------------------------------------
    def test_formatter_round_trips_renamed_exports(self):
        src = (
            'export enum Color { Red, Green } as Hue\n'
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n'
            'export let pi: Decimal = 3.14 as PI\n'
            'export record Point { x: Int, y: Int } as Pt\n'
        )
        once = G.fmt_source(src)
        twice = G.fmt_source(once)
        self.assertEqual(once, twice)
        self.assertIn("export enum Color { Red, Green } as Hue", once)
        self.assertIn("as plus", once)
        self.assertIn("export let pi: Decimal = 3.14 as PI", once)
        self.assertIn("export record Point { x: Int, y: Int } as Pt", once)
        self.assertEqual(G.parse(once), G.parse(twice))

    def test_formatter_rename_survives_execution(self):
        self._write(
            "lib.gm",
            'export fn add(a: Int, b: Int) -> Int { return a + b } as plus\n',
        )
        src = 'import "lib.gm" as geo\nprint geo.plus(2, 3)\n'
        before = self._write("before.gm", src)
        expected = G.run_file(str(before)).out
        after = self._write("after.gm", G.fmt_source(src))
        self.assertEqual(G.run_file(str(after)).out, expected)


if __name__ == "__main__":
    unittest.main()
