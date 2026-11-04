import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class RecordTests(unittest.TestCase):
    def test_construct_and_access_positional(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p: Point = Point(1, 2)\n'
            'print p.x\n'
            'print p.y\n'
        )
        self.assertEqual(it.out, ["1", "2"])

    def test_named_construction(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let q: Point = Point { y: 4, x: 3 }\n'
            'print q.x\n'
            'print q.y\n'
        )
        self.assertEqual(it.out, ["3", "4"])

    def test_annotation_accepts_record_type(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let p: Point = Point(1, 2)\n'
            'fn same(v: Point) -> Point effects {} { return v }\n'
            'let r: Point = same(p)\n'
            'print r\n'
        )
        self.assertEqual(it.out, ["Point { x: 1, y: 2 }"])

    def test_missing_field_positional(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Point.*fields"):
            G.run('record Point { x: Int, y: Int }\nlet p = Point(1)\n')

    def test_extra_field_positional(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Point.*fields"):
            G.run('record Point { x: Int }\nlet p = Point(1, 2)\n')

    def test_wrong_field_type(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Point\.x: Text != Int"):
            G.run('record Point { x: Int }\nlet p = Point("bad")\n')

    def test_wrong_annotation_type(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"Int != Point"):
            G.run('record Point { x: Int }\nlet p: Point = 5\n')

    def test_named_missing_field(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"missing field y"):
            G.run('record Point { x: Int, y: Int }\nlet p = Point { x: 1 }\n')

    def test_named_extra_field(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"unknown field z"):
            G.run('record Point { x: Int }\nlet p = Point { x: 1, z: 2 }\n')

    def test_named_duplicate_field(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"duplicate field x"):
            G.run('record Point { x: Int }\nlet p = Point { x: 1, x: 2 }\n')

    def test_duplicate_declared_field(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"duplicate field x"):
            G.run('record Point { x: Int, x: Int }\n')

    def test_unknown_field_access(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"unknown field 'z'"):
            G.run(
                'record Point { x: Int }\n'
                'let p = Point(1)\n'
                'print p.z\n'
            )

    def test_nested_records(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'record Line { a: Point, b: Point }\n'
            'let l: Line = Line(Point(1, 2), Point(3, 4))\n'
            'print l.a.x\n'
            'print l.b.y\n'
            'print l == Line(Point(1, 2), Point(3, 4))\n'
        )
        self.assertEqual(it.out, ["1", "4", "True"])

    def test_field_shadowing(self):
        # A field named like an outer variable must resolve on the value, not
        # through the surrounding scope.
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'let x = 99\n'
            'let p: Point = Point(1, 2)\n'
            'print p.x\n'
            'print x\n'
            'fn pick(x: Int) -> Int effects {} {\n'
            '  let p: Point = Point(x, x + 1)\n'
            '  return p.y\n'
            '}\n'
            'print pick(7)\n'
        )
        self.assertEqual(it.out, ["1", "99", "8"])

    def test_printing_is_deterministic_and_documented(self):
        # Declaration order, plain decimal formatting, raw text values.
        it = G.run(
            'record Money { amount: Decimal, label: Text }\n'
            'record Pair { a: Int, b: Int }\n'
            'print Money(1.50, "net")\n'
            'print Pair(1, 2)\n'
        )
        self.assertEqual(
            it.out,
            ['Money { amount: 1.50, label: "net" }', 'Pair { a: 1, b: 2 }'])

    def test_named_literal_preserves_declaration_order(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'print Point { y: 2, x: 1 }\n'
        )
        self.assertEqual(it.out, ["Point { x: 1, y: 2 }"])

    def test_equality_is_nominal_and_value_based(self):
        it = G.run(
            'record Point { x: Int, y: Int }\n'
            'print Point(1, 2) == Point(1, 2)\n'
            'print Point(1, 2) == Point(2, 1)\n'
            'print Point(1, 2) != Point(2, 1)\n'
        )
        self.assertEqual(it.out, ["True", "False", "True"])

    def test_equality_between_distinct_record_types_is_type_error(self):
        with self.assertRaisesRegex(G.GamaTypeError, r"matching types"):
            G.run(
                'record A { x: Int }\n'
                'record B { x: Int }\n'
                'print A(1) == B(1)\n'
            )

    def _write(self, root, name, text):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_namespaced_calls_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, "lib.gm",
                        'fn add(a: Int, b: Int) -> Int effects {} '
                        '{ return a + b }\n')
            self._write(root, "main.gm",
                        'import "lib.gm" as math\nprint math.add(2, 3)\n')
            it = G.run_file(str(root / "main.gm"))
            self.assertEqual(it.out, ["5"])

    def test_records_work_through_namespaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(
                root, "geo.gm",
                'record Point { x: Int, y: Int }\n'
                'let origin: Point = Point(0, 0)\n'
                'fn shift(p: Point, dx: Int, dy: Int) -> Point effects {} {\n'
                '  return Point(p.x + dx, p.y + dy)\n'
                '}\n'
                'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n')
            self._write(
                root, "main.gm",
                'import "geo.gm" as geo\n'
                'print geo.add(2, 3)\n'
                'let p: geo.Point = geo.Point(1, 2)\n'
                'print p.x\n'
                'let s: geo.Point = geo.shift(p, 5, 6)\n'
                'print s\n')
            it = G.run_file(str(root / "main.gm"))
            self.assertEqual(
                it.out,
                ["5", "1", "geo.Point { x: 6, y: 8 }"])

    def test_formatter_keeps_record_syntax(self):
        src = ('record Point { x: Int, y: Int }\n'
               'let p: Point = Point(1, 2)\n'
               'print p.x\n')
        formatted = G.fmt_source(src)
        self.assertEqual(formatted, src)
        self.assertEqual(G.fmt_source(formatted), formatted)

    def test_results_and_lambdas_are_unaffected(self):
        it = G.run(
            'record Point { x: Int }\n'
            'fn inc(x: Int) -> Int effects {} { return x + 1 }\n'
            'let f: Function = fn(x: Int) -> Int { return inc(x) }\n'
            'print f(3)\n'
            'print Ok(Point(9))\n'
        )
        self.assertEqual(it.out, ["4", "Ok(Point { x: 9 })"])


if __name__ == "__main__":
    unittest.main()
