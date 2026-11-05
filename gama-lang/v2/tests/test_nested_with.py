"""Nested record copy/update paths: ``o with { inner.x = 9 }``.

Semantics under test:

* A ``with`` key may be a dotted path; the checker resolves every intermediate
  segment through record field types and requires a record at each step.
* Only the records on the updated path are copied; unchanged siblings and
  container values stay shared with the receiver, and the receiver plus its
  aliases are untouched.
* Duplicate paths (``inner.x`` twice) and conflicting paths (``inner`` together
  with ``inner.x``) are rejected with a clear type error.
* Namespaced records (``geo.Inner``) work through aliased imports.
* The formatter preserves dotted paths and the IR backend implements the same
  deep-copy-along-the-path semantics as the interpreter.
"""
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_fmt
import gama_ir as IR

TARGET = (
    'record Inner { x: Int, y: Int }\n'
    'record Outer { inner: Inner, name: Text }\n'
    'let o: Outer = Outer(Inner(1, 2), "a")\n'
    'let p: Outer = o with { inner.x = 9 }\n'
    'print o.inner.x\n'
    'print p.inner.x\n'
    'print p.inner.y\n')


class NestedWithTests(unittest.TestCase):
    def test_target_example(self):
        it = G.run(TARGET)
        self.assertEqual(it.out, ["1", "9", "2"])

    def test_deeper_path(self):
        src = (
            'record Leaf { v: Int }\n'
            'record Mid { leaf: Leaf, keep: Leaf }\n'
            'record Root { mid: Mid }\n'
            'let o = Root(Mid(Leaf(1), Leaf(2)))\n'
            'let p = o with { mid.leaf.v = 9 }\n'
            'print o.mid.leaf.v\n'
            'print p.mid.leaf.v\n'
            'print p.mid.keep.v\n')
        it = G.run(src)
        self.assertEqual(it.out, ["1", "9", "2"])
        o, p = it.env.get("o"), it.env.get("p")
        self.assertIsNot(o, p)
        self.assertIsNot(o.fields["mid"], p.fields["mid"])
        self.assertIsNot(o.fields["mid"].fields["leaf"],
                         p.fields["mid"].fields["leaf"])
        # The untouched sibling record is shared, not copied.
        self.assertIs(o.fields["mid"].fields["keep"],
                      p.fields["mid"].fields["keep"])

    def test_multiple_paths_share_one_new_intermediate(self):
        src = (
            'record Inner { x: Int, y: Int }\n'
            'record Outer { inner: Inner, name: Text }\n'
            'let o = Outer(Inner(1, 2), "a")\n'
            'let p = o with { inner.y = 8, inner.x = 9 }\n'
            'print p.inner\n'
            'print o.inner\n')
        it = G.run(src)
        self.assertEqual(
            it.out, ["Inner { x: 9, y: 8 }", "Inner { x: 1, y: 2 }"])
        self.assertIsNot(it.env.get("o").fields["inner"],
                         it.env.get("p").fields["inner"])

    def test_multiple_disjoint_paths(self):
        src = (
            'record Point { x: Int, y: Int }\n'
            'record Pair { a: Point, b: Point }\n'
            'let o = Pair(Point(1, 2), Point(3, 4))\n'
            'let p = o with { a.x = 9, b.y = 8 }\n'
            'print p\n'
            'print o\n')
        it = G.run(src)
        self.assertEqual(
            it.out,
            ["Pair { a: Point { x: 9, y: 2 }, b: Point { x: 3, y: 8 } }",
             "Pair { a: Point { x: 1, y: 2 }, b: Point { x: 3, y: 4 } }"])

    def test_original_and_aliases_unchanged(self):
        src = (
            'record Inner { x: Int, y: Int }\n'
            'record Outer { inner: Inner, name: Text }\n'
            'let o: Outer = Outer(Inner(1, 2), "a")\n'
            'let alias = o\n'
            'let p = o with { inner.x = 9 }\n'
            'print o.inner.x\n'
            'print alias.inner.x\n'
            'print p.inner.x\n'
            'let q = p\n'
            'q.inner.y := 7\n'
            'print o.inner.y\n'
            'print alias.inner.y\n'
            'print p.inner.y\n')
        it = G.run(src)
        # The copied path is independent of the receiver; mutating it through
        # the new value does not leak back through the original or its alias.
        self.assertEqual(it.out, ["1", "1", "9", "2", "2", "7"])
        env = it.env
        self.assertIs(env.get("alias"), env.get("o"))
        self.assertIsNot(env.get("p"), env.get("o"))

    def test_duplicate_path_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"Outer: duplicate field inner\.x in with"):
            G.check(
                'record Inner { x: Int }\n'
                'record Outer { inner: Inner }\n'
                'let o = Outer(Inner(1))\n'
                'let p = o with { inner.x = 1, inner.x = 2 }\n')

    def test_conflicting_paths_are_a_type_error(self):
        for fields in ("inner = Inner(1), inner.x = 2",
                       "inner.x = 2, inner = Inner(1)"):
            with self.assertRaisesRegex(
                    G.GamaTypeError, r"Outer: conflicting with paths"):
                G.check(
                    'record Inner { x: Int }\n'
                    'record Outer { inner: Inner }\n'
                    'let o = Outer(Inner(1))\n'
                    f'let p = o with {{ {fields} }}\n')

    def test_non_record_intermediate_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Outer\.n: Int is not a record in with"):
            G.check(
                'record Outer { n: Int }\n'
                'let o = Outer(1)\n'
                'let p = o with { n.x = 2 }\n')

    def test_unknown_intermediate_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"Outer: unknown field missing in with"):
            G.check(
                'record Inner { x: Int }\n'
                'record Outer { inner: Inner }\n'
                'let o = Outer(Inner(1))\n'
                'let p = o with { missing.x = 2 }\n')

    def test_unknown_leaf_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"Outer\.inner: unknown field z in with"):
            G.check(
                'record Inner { x: Int }\n'
                'record Outer { inner: Inner }\n'
                'let o = Outer(Inner(1))\n'
                'let p = o with { inner.z = 2 }\n')

    def test_wrong_leaf_type_is_a_type_error(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r'Outer\.inner\.x: Text != Int in with'):
            G.check(
                'record Inner { x: Int }\n'
                'record Outer { inner: Inner }\n'
                'let o = Outer(Inner(1))\n'
                'let p = o with { inner.x = "bad" }\n')

    def _write(self, root, name, text):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_namespaced_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(
                root, "geo.gm",
                'export record Inner { x: Int, y: Int }\n'
                'export record Outer { inner: Inner, name: Text }\n'
                'export let origin: Outer = Outer(Inner(1, 2), "a")\n')
            self._write(
                root, "main.gm",
                'import "geo.gm" as geo\n'
                'let o: geo.Outer = geo.Outer(geo.Inner(1, 2), "a")\n'
                'let p: geo.Outer = o with { inner.x = 9 }\n'
                'print o.inner.x\n'
                'print p.inner.x\n'
                'print p.inner.y\n'
                'let r = geo.origin with { inner.y = 8 }\n'
                'print r\n')
            it = G.run_file(str(root / "main.gm"))
            self.assertEqual(
                it.out,
                ["1", "9", "2",
                 "geo.Outer { inner: geo.Inner { x: 1, y: 8 }, "
                 'name: "a" }'])

    def test_formatter_canonical_dotted_path(self):
        src = (
            'record Inner { x: Int, y: Int }\n'
            'record Outer { inner: Inner, name: Text }\n'
            'let o = Outer(Inner(1, 2), "a")\n'
            'let p = o with {inner.x=9}\n'
            'print p.inner.x\n')
        out = gama_fmt.format_source(src)
        self.assertEqual(
            out,
            'record Inner { x: Int, y: Int }\n'
            'record Outer { inner: Inner, name: Text }\n'
            'let o = Outer(Inner(1, 2), "a")\n'
            'let p = o with { inner.x = 9 }\n'
            'print p.inner.x\n')
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertEqual(G.run(out).out, G.run(src).out)

    def test_formatter_wraps_dotted_paths_and_round_trips(self):
        src = (
            'record Inner { x: Int, y: Int, z: Int }\n'
            'record Outer { inner: Inner }\n'
            'let o = Outer(Inner(1, 2, 3))\n'
            'let p = o with { inner.x = 111111111111111111, '
            'inner.y = 222222222222222222, inner.z = 333333333333333333 }\n'
            'print p\n')
        out = gama_fmt.format_source(src)
        self.assertIn("inner.x = 111111111111111111,\n", out)
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertEqual(G.run(out).out, G.run(src).out)

    def test_ir_matches_interpreter(self):
        self.assertEqual(
            IR.run_program(G.check(TARGET)).out, G.run(TARGET).out)

    def test_ir_deep_copies_only_the_path(self):
        src = (
            'record Leaf { v: Int }\n'
            'record Mid { leaf: Leaf, keep: Leaf }\n'
            'record Root { mid: Mid }\n'
            'let o = Root(Mid(Leaf(1), Leaf(2)))\n'
            'let p = o with { mid.leaf.v = 9 }\n'
            'print o\n'
            'print p\n')
        result = IR.run_program(G.check(src))
        self.assertEqual(result.out, G.run(src).out)
        o, p = result.globals["o"], result.globals["p"]
        self.assertIsNot(o.fields["mid"], p.fields["mid"])
        self.assertIsNot(o.fields["mid"].fields["leaf"],
                         p.fields["mid"].fields["leaf"])
        self.assertIs(o.fields["mid"].fields["keep"],
                      p.fields["mid"].fields["keep"])

    def test_ir_parity_for_multiple_paths_and_errors(self):
        src = (
            'record Inner { x: Int, y: Int }\n'
            'record Outer { inner: Inner, name: Text }\n'
            'let o = Outer(Inner(1, 2), "a")\n'
            'let p = o with { inner.y = 8, inner.x = 9 }\n'
            'print p\nprint o\n')
        self.assertEqual(
            IR.run_program(G.check(src)).out, G.run(src).out)

    def test_unchecked_conflicting_paths_raise_the_same_error(self):
        src = (
            'record Inner { x: Int }\n'
            'record Outer { inner: Inner }\n'
            'let o = Outer(Inner(1))\n'
            'let p = o with { inner = Inner(1), inner.x = 2 }\n')
        with self.assertRaises(G.GamaRuntimeError) as want:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(IR.IRRuntimeError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(want.exception))


if __name__ == "__main__":
    unittest.main()
