"""IR-vs-interpreter parity for payload enum variants.

Successful programs run on both the reference interpreter and the standalone IR
backend; printed output must be byte-for-byte identical.  Runtime failures are
compared by full message (line/col included), using parsed-but-unchecked ASTs so
the IR's own validation runs (the checker rejects several of these first).
Aliased imports go through the real file loader, exactly like ``ir run FILE``.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


OPTION = 'enum Option { Some(Int), None }\n'


class IRPayloadEnumsMatchInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def test_success_programs_match(self):
        programs = {
            "bare constructor and annotation": (
                OPTION + 'let x: Option = Some(5)\nprint x\n'),
            "qualified constructor and nullary": (
                OPTION + 'print Option.Some(3)\nprint Option.None\n'),
            "bare pattern binds payload": (
                OPTION + 'match Some(7) { Some(v) => { print v }, '
                'None => { print "none" } }\n'),
            "qualified pattern binds payload": (
                OPTION + 'match Option.Some(8) { Option.Some(v) => { print v }, '
                'Option.None => { print "none" } }\n'),
            "nullary pattern": (
                OPTION + 'match Option.None { Some(v) => { print v }, '
                'None => { print "none" } }\n'),
            "multi payload construct and bind": (
                'enum Pair { P(Int, Text) }\n'
                'let p: Pair = P(1, "a")\n'
                'print p\n'
                'match p { P(a, b) => { print a\n print b } }\n'),
            "nested payload match": (
                OPTION + 'enum Outer { Wrap(Option), Empty }\n'
                'match Wrap(Some(9)) {\n'
                '  Wrap(inner) => { match inner { Some(v) => { print v }, '
                'None => { print "none" } } },\n'
                '  Empty => { print "empty" }\n'
                '}\n'),
            "function match with default": (
                OPTION + 'fn get_or(o: Option, d: Int) -> Int {\n'
                '  match o { Some(v) => { return v }, _ => { return d } }\n'
                '}\n'
                'print get_or(Some(9), 0)\nprint get_or(Option.None, 42)\n'),
            "payload equality": (
                OPTION + 'print Some(5) == Some(5)\n'
                'print Some(5) == Some(6)\n'
                'print Some(5) != Some(6)\n'
                'print Option.None == Option.None\n'
                'print Some(5) == Option.None\n'),
            "deterministic payload printing": (
                'enum Box { B(Text), F(Bool), D(Decimal) }\n'
                'print B("hi")\nprint F(true)\nprint D(1.50)\n'),
            "null payload is a real payload": (
                'enum Box { B(Any) }\nprint B(null)\n'),
            "mixed nullary and payload variants": (
                'enum E { A, B(Int) }\n'
                'match B(2) { A => { print "a" }, B(x) => { print x } }\n'),
            "payload values in lists": (
                OPTION + 'let xs = [Some(1), Option.None, Some(3)]\n'
                'for o in xs { match o { Some(v) => { print v }, '
                'None => { print "n" } } }\n'),
            "payload enum in a record field": (
                OPTION + 'record Box { name: Text, value: Option }\n'
                'let b: Box = Box("x", Some(4))\n'
                'print b.value\nprint b\n'),
            "recursive payload enum": (
                'enum List2 { Nil, Cons(Int, List2) }\n'
                'let xs: List2 = Cons(1, Cons(2, List2.Nil))\n'
                'print xs\n'
                'fn total(l: List2) -> Int {\n'
                '  match l { List2.Nil => { return 0 }, '
                'Cons(h, t) => { return h + total(t) } }\n'
                '}\nprint total(xs)\n'),
            "payload rebound and used in arithmetic": (
                'enum Pair { P(Int, Int) }\n'
                'match P(2, 3) { P(a, b) => { print a + b } }\n'),
        }
        for name, src in programs.items():
            with self.subTest(name=name):
                self.assert_same(src)

    def test_payload_enum_compiles_instead_of_irunsupported(self):
        program = IR.compile_program(G.parse(OPTION + 'print Some(1)\n'))
        self.assertEqual(program.enums['Option'], {'Some': ('Int',), 'None': ()})
        listing = program.disassemble()
        self.assertIn("DEF_ENUM", listing)
        self.assertIn("CHECK_CALL", listing)

    def test_nested_payload_enum_activates_when_reached(self):
        src = (
            'if true {\n'
            '  enum Local { V(Int) }\n'
            '  let q = V(7)\n'
            '  print q\n'
            '  match q { V(n) => { print n } }\n'
            '}\n'
        )
        expected = G.Interpreter().run(G.parse(src)).out
        got = IR.run_program(G.parse(src)).out
        self.assertEqual(got, expected)
        self.assertEqual(got, ['Local.V(7)', '7'])


class IRPayloadEnumRuntimeErrorsMatchInterpreter(unittest.TestCase):
    def assert_same_runtime_error(self, src):
        with self.assertRaises(G.GamaError) as ref:
            G.Interpreter().run(G.parse(src))
        with self.assertRaises(G.GamaError) as got:
            IR.run_program(G.parse(src))
        self.assertEqual(str(got.exception), str(ref.exception))
        self.assertIsInstance(got.exception, IR.IRError)

    def test_error_programs_match(self):
        programs = {
            "nullary variant called": OPTION + 'print None()\n',
            "qualified nullary variant called": OPTION + 'print Option.None()\n',
            "payload variant used bare": OPTION + 'print Option.Some\n',
            "ambiguous bare constructor": (
                'enum A { X(Int) }\nenum B { X(Text) }\nprint X(1)\n'),
            "constructor arity too many": OPTION + 'print Some(1, 2)\n',
            "constructor arity zero": OPTION + 'print Some()\n',
            "pattern binds too many": (
                OPTION + 'match Some(1) { Some(a, b) => { print a }, '
                '_ => { print "x" } }\n'),
            "nullary pattern binds a payload": (
                OPTION + 'match Option.None { None(x) => { print x }, '
                '_ => { print "x" } }\n'),
            "unknown qualified variant": OPTION + 'print Option.Other\n',
            "match no arm": (
                OPTION + 'match Option.Some(1) { Option.None => { print 1 } }\n'),
        }
        for name, src in programs.items():
            with self.subTest(name=name):
                self.assert_same_runtime_error(src)

    def test_payload_variant_bare_is_rejected_by_checker(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check(OPTION + 'let x = Option.Some\n')
        self.assertIn("requires a payload", str(cm.exception))


class IRAliasedPayloadEnums(unittest.TestCase):
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

    def test_aliased_payload_enum_constructors_and_match(self):
        self._write(
            "lib.gm",
            'enum Option { Some(Int, Text), None }\n'
            'fn wrap(n: Int) -> Option { return Option.Some(n, "x") }\n',
        )
        self._write(
            "main.gm",
            'import "lib.gm" as geo\n'
            'let o: geo.Option = geo.Option.Some(5, "a")\n'
            'print o\n'
            'match o {\n'
            '  geo.Option.Some(v, s) => { print v\n print s },\n'
            '  geo.Option.None => { print "n" }\n'
            '}\n'
            'print geo.wrap(7)\n'
            'print geo.Option.None\n',
        )
        expected = G.run_file(str(self.main)).out
        got = IR.run_file(str(self.main)).out
        self.assertEqual(got, expected)
        self.assertEqual(
            got,
            ['geo.Option.Some(5, "a")', '5', 'a',
             'geo.Option.Some(7, "x")', 'geo.Option.None'],
        )

    def test_compile_file_records_payload_arity(self):
        self._write("lib.gm", 'enum Option { Some(Int, Text), None }\n')
        self._write("main.gm", 'import "lib.gm" as geo\nprint geo.Option.None\n')
        program = IR.compile_file(str(self.main))
        self.assertEqual(
            program.enums,
            {"geo.Option": {"Some": ("Int", "Text"), "None": ()}},
        )


if __name__ == "__main__":
    unittest.main()
