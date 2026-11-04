"""Record field assignment (`p.x := v`) — in-place update semantics.

Records are nominal runtime values; assigning to a dotted target mutates the
receiver `RecordVal` in place, unlike copy/`with` (`tests/test_record_with.py`)
which returns a fresh record. Aliases observe this in-place update. Field paths
resolve against the longest dotted variable prefix, so namespaced constants
(`geo.origin.x := ...`) and nested records (`l.a.x := ...`) work too.
"""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class RecordUpdateTests(unittest.TestCase):
    def test_simple_update_and_readback(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p: Point = Point(1, 2)\n'
            'p.x := 3\n'
            'print p.x\n'
            'print p.y\n'
            'print p\n'
            'print p == Point(3, 2)\n'
        )
        self.assertEqual(
            it.out,
            ["3", "2", "Point { x: 3, y: 2 }", "True"])

    def test_nested_update(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l = Line(Point(1, 2), Point(3, 4))\n'
            'l.a.x := 5\n'
            'print l.a.x\n'
            'print l.b.y\n'
            'print l\n'
            'print l == Line(Point(5, 2), Point(3, 4))\n'
        )
        self.assertEqual(
            it.out,
            ["5", "4",
             "Line { a: Point { x: 5, y: 2 }, b: Point { x: 3, y: 4 } }",
             "True"])

    def test_update_mutates_in_place_for_aliases(self):
        # No copy-on-write: a second binding to the same record sees the update.
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'let q = p\n'
            'q.x := 9\n'
            'print p.x\n'
            'print p == q\n'
        )
        self.assertEqual(it.out, ["9", "True"])

    def test_wrong_field(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"unknown field 'z' on Point"):
            G.run(
                'record Point { x: Int }\n'
                'let p = Point(1)\n'
                'p.z := 2\n'
            )

    def test_wrong_value_type(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"p\.x: Text != Int"):
            G.run(
                'record Point { x: Int }\n'
                'let p = Point(1)\n'
                'p.x := "bad"\n'
            )

    def test_non_record_receiver(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"cannot assign field 'x' on non-record type Int"):
            G.run('let n = 5\nn.x := 1\n')

    def test_nested_non_record_receiver(self):
        with self.assertRaisesRegex(
                G.GamaTypeError, r"cannot assign field 'y' on non-record type Int"):
            G.run(
                'record Point { x: Int }\n'
                'let p = Point(1)\n'
                'p.x.y := 1\n'
            )

    def test_unknown_base_variable(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"unknown variable q"):
            G.run('q.x := 1\n')

    def _write(self, root, name, text):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_namespaced_record_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(
                root, "geo.gm",
                'record Point { x: Int, y: Int }\n'
                'let origin: Point = Point(0, 0)\n')
            self._write(
                root, "main.gm",
                'import "geo.gm" as geo\n'
                'geo.origin.x := 7\n'
                'print geo.origin\n'
                'print geo.origin.x\n'
                'let p: geo.Point = geo.Point(1, 2)\n'
                'p.y := 9\n'
                'print p\n'
                'print p == geo.Point(1, 9)\n')
            it = G.run_file(str(root / "main.gm"))
            self.assertEqual(
                it.out,
                ["geo.Point { x: 7, y: 0 }", "7",
                 "geo.Point { x: 1, y: 9 }", "True"])
    def test_ir_rejects_field_assignment(self):
        # The IR value model has no in-place record mutation; reject instead of
        # silently creating a `p.x` shadow variable.
        import gama_ir as IR
        ast = G.check(
            'record Point { x: Int, y: Int }\n'
            'let p = Point(1, 2)\n'
            'p.x := 3\n')
        with self.assertRaisesRegex(IR.IRUnsupported, r"record field assignment"):
            IR.compile_program(ast)


if __name__ == "__main__":
    unittest.main()
