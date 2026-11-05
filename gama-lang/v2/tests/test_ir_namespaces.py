"""IR backend: aliased ("namespaced") imports.

The core loader flattens ``import "lib.gm" as m`` into dotted top-level names
(``m.fn``, ``m.pi``, ``m.Point``).  The IR backend resolves those through the
same function, global and nominal-type tables it uses for local declarations, so
aliased calls, constants and records match the reference interpreter.

Aliased ``enum`` declarations are namespaced by the core loader (``geo.Color``),
so both backends resolve ``geo.Color.Red`` identically.  A hand-built AST also
pins that the VM resolves a dotted enum name independently of the front end.
"""
import dataclasses
import pathlib
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import gamal2 as G
import gama_ir as IR


class IrNamespacedImports(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, src):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        return p

    def assert_parity(self, main):
        ref = G.run_file(str(main))
        got = IR.run_file(str(main))
        self.assertEqual(got.out, ref.out)
        return ref.out

    # -- namespaced functions ---------------------------------------------
    def test_namespaced_call_matches_interpreter(self):
        self.write("lib.gm", "fn add(a: Int, b: Int) -> Int { return a + b }\n")
        main = self.write("main.gm", 'import "lib.gm" as math\nprint math.add(2, 3)\n')
        self.assertEqual(self.assert_parity(main), ["5"])

    def test_namespaced_call_recursion_and_defaults(self):
        self.write(
            "lib.gm",
            "fn fib(n: Int) -> Int {\n"
            "  if n < 2 { return n }\n"
            "  return fib(n - 1) + fib(n - 2)\n"
            "}\n"
            "fn bump(x: Int, by: Int = 10) -> Int { return x + by }\n",
        )
        main = self.write(
            "main.gm",
            'import "lib.gm" as math\n'
            "print math.fib(10)\n"
            "print math.bump(5)\n"
            "print math.bump(5, 1)\n",
        )
        self.assertEqual(self.assert_parity(main), ["55", "15", "6"])

    def test_namespaced_call_to_private_sibling_stays_resolvable(self):
        # An aliased module with exports renames non-exported helpers to an
        # internal spelling; calls from inside the module must keep working.
        self.write(
            "lib.gm",
            "fn helper(x: Int) -> Int { return x * 2 }\n"
            "export fn wrap(x: Int) -> Int { return helper(x) + 1 }\n",
        )
        main = self.write("main.gm", 'import "lib.gm" as math\nprint math.wrap(20)\n')
        self.assertEqual(self.assert_parity(main), ["41"])

    # -- namespaced constants ---------------------------------------------
    def test_namespaced_constant_matches_interpreter(self):
        self.write(
            "lib.gm",
            'let pi: Decimal = 3.14\nlet unit: Text = "cm"\n',
        )
        main = self.write(
            "main.gm",
            'import "lib.gm" as math\nprint math.pi\nprint math.unit\n',
        )
        self.assertEqual(self.assert_parity(main), ["3.14", "cm"])

    def test_module_function_reads_namespaced_constant(self):
        self.write(
            "lib.gm",
            "let factor = 10\n"
            "fn scale(x: Int) -> Int { return x * factor }\n",
        )
        main = self.write("main.gm", 'import "lib.gm" as math\nprint math.scale(3)\n')
        self.assertEqual(self.assert_parity(main), ["30"])

    def test_namespaced_constant_is_not_shadowed_by_record_field(self):
        self.write(
            "lib.gm",
            "let pi: Decimal = 3.14\n"
            "record Pair { x: Int, pi: Int }\n",
        )
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            "let q: geo.Pair = geo.Pair(1, 2)\n"
            "print q.pi\n"
            "print geo.pi\n",
        )
        self.assertEqual(self.assert_parity(main), ["2", "3.14"])

    # -- namespaced records / type names -----------------------------------
    def test_namespaced_record_construction_and_type_annotation(self):
        self.write(
            "lib.gm",
            "record Point { x: Int, y: Int }\n"
            "fn origin() -> Point { return Point(0, 0) }\n",
        )
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            "let p: geo.Point = geo.Point(1, 2)\n"
            "print p.x\n"
            "print p\n"
            "print geo.origin()\n",
        )
        self.assertEqual(
            self.assert_parity(main),
            ["1", "geo.Point { x: 1, y: 2 }", "geo.Point { x: 0, y: 0 }"],
        )

    def test_namespaced_record_in_function_signature(self):
        self.write(
            "lib.gm",
            "record Point { x: Int, y: Int }\n"
            "fn shift(p: Point, dx: Int, dy: Int) -> Point {\n"
            "  return Point(p.x + dx, p.y + dy)\n"
            "}\n",
        )
        main = self.write(
            "main.gm",
            'import "lib.gm" as geo\n'
            "print geo.shift(geo.Point(1, 2), 3, 4)\n",
        )
        self.assertEqual(self.assert_parity(main), ["geo.Point { x: 4, y: 6 }"])

    def test_plain_imports_still_flatten(self):
        self.write("lib.gm", "let answer = 42\nfn double(x: Int) -> Int { return x * 2 }\n")
        main = self.write(
            "main.gm",
            'import "lib.gm"\nprint double(answer)\n',
        )
        self.assertEqual(self.assert_parity(main), ["84"])

    # -- missing names ------------------------------------------------------
    def test_missing_namespaced_function_is_a_checker_error_in_both(self):
        self.write("lib.gm", "fn add(a: Int, b: Int) -> Int { return a + b }\n")
        main = self.write("main.gm", 'import "lib.gm" as math\nprint math.nope()\n')
        with self.assertRaises(G.GamaError) as ref:
            G.run_file(str(main))
        with self.assertRaises(G.GamaError) as got:
            IR.run_file(str(main))
        self.assertIsInstance(ref.exception, G.GamaTypeError)
        self.assertEqual(str(got.exception), str(ref.exception))

    def test_missing_namespaced_constant_is_a_checker_error_in_both(self):
        self.write("lib.gm", "let value = 5\n")
        main = self.write("main.gm", 'import "lib.gm" as math\nprint math.missing\n')
        with self.assertRaises(G.GamaError) as ref:
            G.run_file(str(main))
        with self.assertRaises(G.GamaError) as got:
            IR.run_file(str(main))
        self.assertIsInstance(ref.exception, G.GamaTypeError)
        self.assertEqual(str(got.exception), str(ref.exception))

    def test_unchecked_missing_namespaced_function_matches_at_runtime(self):
        # Bypass the checker so the IR's own call lookup is compared directly.
        src = "print math.nope()\n"
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(IR.IRError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(ref.exception))

    def test_unchecked_missing_namespaced_constant_matches_at_runtime(self):
        src = "print math.missing\n"
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(IR.IRError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(ref.exception))

    # -- namespaced enums: front end and VM agree ----------------------------
    def test_aliased_enum_declaration_runs_identically(self):
        self.write("lib.gm", "enum Color { Red, Green }\n")
        main = self.write("main.gm", 'import "lib.gm" as geo\nprint geo.Color.Red\n')
        expected = G.run_file(str(main)).out
        got = IR.run_file(str(main)).out
        self.assertEqual(got, expected)
        self.assertEqual(got, ["geo.Color.Red"])

    def test_vm_resolves_dotted_enum_if_the_front_end_provides_one(self):
        # The core loader does not namespace enums yet.  Feed the VM the AST it
        # would produce (enum ``geo.Color``, variant ``geo.Color.Red``) to pin
        # that the runtime lookup is not hard-coded to a two-part name.
        ast = G.parse("enum Color { Red }\nprint Color.Red\n")
        ast[0] = dataclasses.replace(ast[0], name="geo.Color")
        ast[1] = dataclasses.replace(
            ast[1], expr=dataclasses.replace(ast[1].expr, name="geo.Color.Red"))
        program = IR.compile_program(ast)
        self.assertEqual(program.enums, {"geo.Color": frozenset({"Red"})})
        self.assertEqual(IR.run_program(program).out, ["geo.Color.Red"])


if __name__ == "__main__":
    unittest.main()
