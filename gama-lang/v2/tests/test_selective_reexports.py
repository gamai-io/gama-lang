"""Selective re-export lists: ``export { name, other as Alias }``.

Semantics pinned here:

* A top-level ``export { ... }`` list selects the alias-visible surface from
  the module's own top-level ``fn``/``let``/``record``/``enum`` declarations.
  Entries are separated by commas or newlines; a trailing comma is allowed.
* ``name as Alias`` renames only the alias-surface name: the module keeps
  using the original name internally, and plain imports keep flattening the
  original names (they ignore both the filter and the rename).
* A module with only a selective list hides every unlisted declaration.  A
  module with at least one export of any form (declaration export or list)
  exposes exactly the union of the two forms; a module with no export keeps
  the legacy all-visible alias behaviour.
* Selective entries may only name local top-level declarations.  Imported
  aliases are namespaced bindings, not declarations, so they are not
  re-exportable (clear ``GamaError``).  Likewise plain-imported names are not
  local declarations and cannot be listed.
* Unknown names, empty lists, duplicate entries, duplicate alias targets,
  collisions with an existing top-level name and missing separators are
  rejected with ``GamaError``.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class SelectiveReexportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

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
        return G.run_file(str(self.root / "main.gm"))

    def _check_lib(self, lib):
        self._write("lib.gm", lib)
        return G.check_file(str(self.root / "lib.gm"))

    # -- selection by declaration kind -----------------------------------
    def test_select_fn_let_record_and_enum(self):
        it = self._run(
            'import "lib.gm" as m\n'
            'print m.add(2, 3)\n'
            'print m.PI\n'
            'let p: m.Point = m.Point(1, 2)\n'
            'print p.x\n'
            'print m.Color.Green\n',
            'export { add, pi as PI, Point, Color }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let pi: Decimal = 3.14\n'
            'record Point { x: Int, y: Int }\n'
            'enum Color { Red, Green }\n',
        )
        self.assertEqual(it.out, ["5", "3.14", "1", "m.Color.Green"])

    def test_only_selected_names_survive_filtering(self):
        self._write(
            "lib.gm",
            'export { add }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'fn helper(x: Int) -> Int { return x }\n'
            'let secret = 9\n'
            'enum Hidden { A }\n'
            'record HiddenRecord { z: Int }\n',
        )
        for ref in ("m.helper(1)", "m.secret", "m.Hidden.A",
                    "m.HiddenRecord(1)"):
            self._write(
                "main.gm",
                'import "lib.gm" as m\nprint %s\n' % ref,
            )
            with self.assertRaisesRegex(G.GamaError, "unknown"):
                G.run_file(str(self.root / "main.gm"))

    def test_unlisted_enum_is_hidden(self):
        self._write(
            "lib.gm",
            'export { add }\n'
            'fn add(a: Int) -> Int { return a }\n'
            'enum Hidden { A }\n',
        )
        self._write("main.gm", 'import "lib.gm" as m\nprint m.Hidden.A\n')
        with self.assertRaisesRegex(G.GamaError, "unknown"):
            G.run_file(str(self.root / "main.gm"))

    # -- renames ----------------------------------------------------------
    def test_rename_exposes_new_name_only(self):
        it = self._run(
            'import "lib.gm" as m\nprint m.PI\n',
            'export { pi as PI }\nlet pi: Decimal = 3.14\n',
        )
        self.assertEqual(it.out, ["3.14"])
        self._write("main.gm", 'import "lib.gm" as m\nprint m.pi\n')
        with self.assertRaisesRegex(G.GamaError, "unknown"):
            G.run_file(str(self.root / "main.gm"))

    def test_module_keeps_using_original_name(self):
        it = self._run(
            'import "lib.gm" as m\nprint m.twice(4)\nprint m.plus(2, 3)\n',
            'export { add as plus, twice }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'fn twice(x: Int) -> Int { return add(x, x) }\n',
        )
        self.assertEqual(it.out, ["8", "5"])

    # -- mixed with declaration-level exports ----------------------------
    def test_mixed_declaration_and_list_exports_union(self):
        it = self._run(
            'import "lib.gm" as m\n'
            'print m.a()\n'
            'print m.b()\n'
            'print m.P\n',
            'export fn a() -> Int { return 1 }\n'
            'fn b() -> Int { return 2 }\n'
            'export { b, p as P }\n'
            'let p: Int = 3\n',
        )
        self.assertEqual(it.out, ["1", "2", "3"])

    def test_mixed_hides_unexported_declarations(self):
        self._write(
            "lib.gm",
            'export fn a() -> Int { return 1 }\n'
            'export { b }\n'
            'fn b() -> Int { return 2 }\n'
            'fn c() -> Int { return 3 }\n',
        )
        self._write("main.gm", 'import "lib.gm" as m\nprint m.c()\n')
        with self.assertRaisesRegex(G.GamaError, "unknown"):
            G.run_file(str(self.root / "main.gm"))

    def test_same_source_in_declaration_and_list_rejected(self):
        self._write(
            "lib.gm",
            'export fn a() -> Int { return 1 }\nexport { a }\n',
        )
        with self.assertRaisesRegex(G.GamaError,
                                    "duplicate export entry 'a'"):
            self._check_lib((self.root / "lib.gm").read_text())

    # -- errors -----------------------------------------------------------
    def test_unknown_name_rejected(self):
        self._write("lib.gm", 'fn a() -> Int { return 1 }\nexport { nope }\n')
        with self.assertRaisesRegex(G.GamaError,
                                    "unknown export name 'nope'"):
            G.check_file(str(self.root / "lib.gm"))

    def test_duplicate_entry_rejected(self):
        self._write(
            "lib.gm",
            'fn a() -> Int { return 1 }\nexport { a, a }\n',
        )
        with self.assertRaisesRegex(G.GamaError,
                                    "duplicate export entry 'a'"):
            G.check_file(str(self.root / "lib.gm"))

    def test_duplicate_target_rejected(self):
        self._write(
            "lib.gm",
            'fn a() -> Int { return 1 }\n'
            'fn b() -> Int { return 2 }\n'
            'export { a as X, b as X }\n',
        )
        with self.assertRaisesRegex(G.GamaError, "duplicate export 'X'"):
            G.check_file(str(self.root / "lib.gm"))

    def test_rename_collides_with_top_level_name(self):
        self._write(
            "lib.gm",
            'fn a() -> Int { return 1 }\n'
            'fn X() -> Int { return 2 }\n'
            'export { a as X }\n',
        )
        with self.assertRaisesRegex(
            G.GamaError,
            "export name 'X' collides with existing top-level name",
        ):
            G.check_file(str(self.root / "lib.gm"))

    def test_empty_list_rejected(self):
        self._write("lib.gm", "export {}\n")
        with self.assertRaisesRegex(G.GamaError,
                                    "export list must not be empty"):
            G.check_file(str(self.root / "lib.gm"))

    def test_missing_separator_rejected(self):
        self._write(
            "lib.gm",
            'fn a() -> Int { return 1 }\nexport { a nope }\n',
        )
        with self.assertRaisesRegex(G.GamaError,
                                    "expected ',' or '}' in export list"):
            G.check_file(str(self.root / "lib.gm"))

    def test_import_alias_is_not_reexportable(self):
        self._write("inner.gm", 'export fn f() -> Int { return 1 }\n')
        self._write(
            "lib.gm",
            'import "inner.gm" as inner\nexport { inner }\n',
        )
        with self.assertRaisesRegex(G.GamaError,
                                    "unknown export name 'inner'"):
            G.check_file(str(self.root / "lib.gm"))

    def test_export_list_inside_block_rejected(self):
        self._write(
            "lib.gm",
            'fn f() -> Int {\n  export { f }\n  return 1\n}\n',
        )
        with self.assertRaisesRegex(G.GamaError,
                                    "export is only allowed at top level"):
            G.check_file(str(self.root / "lib.gm"))

    # -- list spelling ----------------------------------------------------
    def test_newlines_and_trailing_comma_accepted(self):
        it = self._run(
            'import "lib.gm" as m\nprint m.add(1, 2)\nprint m.PI\n',
            'export {\n  add,\n  pi as PI,\n}\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let pi: Decimal = 3.14\n',
        )
        self.assertEqual(it.out, ["3", "3.14"])

    # -- plain imports stay compatible ------------------------------------
    def test_plain_import_flattens_original_names_and_ignores_filter(self):
        it = self._run(
            'import "lib.gm"\n'
            'print add(2, 3)\n'
            'print pi\n'
            'print hidden(1)\n',
            'export { add, pi as PI }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let pi: Decimal = 3.14\n'
            'fn hidden(x: Int) -> Int { return x }\n',
        )
        self.assertEqual(it.out, ["5", "3.14", "1"])

    # -- formatter --------------------------------------------------------
    def test_formatter_round_trips_selective_lists(self):
        src = (
            'export {\n  add,\n  pi as PI,\n  Point,\n  Color,\n}\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let pi: Decimal = 3.14\n'
            'record Point { x: Int, y: Int }\n'
            'enum Color { Red, Green }\n'
        )
        once = G.fmt_source(src)
        twice = G.fmt_source(once)
        self.assertEqual(once, twice)
        self.assertIn("export { add, pi as PI, Point, Color }", once)
        self.assertEqual(G.parse(once), G.parse(twice))

    def test_formatter_rename_survives_execution(self):
        self._write(
            "lib.gm",
            'export { add as plus }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n',
        )
        src = 'import "lib.gm" as m\nprint m.plus(2, 3)\n'
        before = self._write("before.gm", src)
        expected = G.run_file(str(before)).out
        after = self._write("after.gm", G.fmt_source(src))
        self.assertEqual(G.run_file(str(after)).out, expected)

    # -- IR parity --------------------------------------------------------
    def test_ir_handles_selective_reexports(self):
        self._write(
            "lib.gm",
            'export { add, pi as PI, Point, Color }\n'
            'fn add(a: Int, b: Int) -> Int { return a + b }\n'
            'let pi: Decimal = 3.14\n'
            'record Point { x: Int, y: Int }\n'
            'enum Color { Red, Green }\n',
        )
        self._write(
            "main.gm",
            'import "lib.gm" as m\n'
            'print m.add(2, 3)\n'
            'print m.PI\n'
            'let p: m.Point = m.Point(1, 2)\n'
            'print p.x\n'
            'print m.Color.Green\n',
        )
        expected = G.run_file(str(self.root / "main.gm")).out
        self.assertEqual(IR.run_file(str(self.root / "main.gm")).out, expected)


if __name__ == "__main__":
    unittest.main()
