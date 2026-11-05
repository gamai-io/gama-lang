"""Generic enums with erased type parameters.

Syntax::

    enum Option<T> { Some(T), None }
    enum Result2<T, E> { Good(T), Bad(E) }

Enum type parameters are erased at runtime (a ``T`` payload erases to ``Any``
and a generic payload reference erases to its nominal base).  Concrete payload
types, including payloads of a generic enum, keep their static checks.  Nested
generic annotations are arity-checked recursively.  Namespaced generic enums
work through aliased imports, including constructors and match patterns.

Limits: no bounds and no variance; the IR backend uses the same erasure and
deliberately ignores annotations / payload type arguments (they are runtime
metadata only), while enum identity, variant names and payload arity are
preserved.  IR does not perform static payload checks (the checker does).
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR

OPTION = 'enum Option<T> { Some(T), None }\n'
RESULT2 = 'enum Result2<T, E> { Good(T), Bad(E) }\n'


class GenericEnumTests(unittest.TestCase):
    # -- Option ----------------------------------------------------------
    def test_option_construct_and_match(self):
        it = G.run(
            OPTION
            + 'let o: Option<Int> = Some(5)\n'
            + 'match o { Some(v) => { print v }, None => { print "none" } }\n')
        self.assertEqual(it.out, ["5"])

    def test_option_nullary_and_qualified_value(self):
        it = G.run(
            OPTION
            + 'let a: Option<Int> = Option.None\n'
            + 'print a\n'
            + 'match a { Some(v) => { print v }, None => { print "none" } }\n')
        self.assertEqual(it.out, ["Option.None", "none"])

    def test_function_returning_generic_enum_annotation(self):
        it = G.run(
            OPTION
            + 'fn mk() -> Option<Int> { return Some(2) }\n'
            + 'let o: Option<Int> = mk()\n'
            + 'print o\n')
        self.assertEqual(it.out, ["Option.Some(2)"])

    def test_option_payload_type_erases_to_any(self):
        # T erases to Any, so the annotation does not reject a Text payload.
        it = G.run(
            OPTION
            + 'let o: Option<Int> = Some("text")\n'
            + 'print o\n')
        self.assertEqual(it.out, ['Option.Some("text")'])

    # -- Result2 (two parameters, qualified constructor) -----------------
    def test_result2_two_parameters_and_qualified_constructor(self):
        it = G.run(
            RESULT2
            + 'let a: Result2<Int, Text> = Result2.Good(7)\n'
            + 'let b: Result2<Int, Text> = Bad("boom")\n'
            + 'print a\nprint b\n'
            + 'match a { Good(v) => { print v }, Bad(e) => { print e } }\n'
            + 'match b { Good(v) => { print v }, Bad(e) => { print e } }\n')
        self.assertEqual(
            it.out,
            ['Result2.Good(7)', 'Result2.Bad("boom")', '7', 'boom'])

    def test_multi_payload_generic_variant(self):
        it = G.run(
            'enum Pair<A, B> { P(A, B), Empty }\n'
            'let p: Pair<Int, Text> = P(1, "x")\n'
            'print p\n'
            'match p { P(a, b) => { print a; print b }, Empty => { print "e" } }\n')
        self.assertEqual(it.out, ['Pair.P(1, "x")', '1', 'x'])

    # -- nested generic annotations --------------------------------------
    def test_nested_option_annotation_and_value(self):
        it = G.run(
            OPTION
            + 'let n: Option<Option<Int>> = Some(Some(3))\n'
            + 'match n {\n'
            + '  Some(inner) => {\n'
            + '    match inner { Some(x) => { print x }, None => { print "inner" } }\n'
            + '  },\n'
            + '  None => { print "outer" }\n'
            + '}\n')
        self.assertEqual(it.out, ["3"])

    def test_nested_annotation_arity_is_recursive(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Option expects 1 type argument, got 2"):
            G.check(
                OPTION
                + 'let n: Option<Option<Int, Text>> = Some(Some(1))\n')

    # -- arity errors ----------------------------------------------------
    def test_annotation_arity_error(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Option expects 1 type argument, got 2"):
            G.check(OPTION + 'let o: Option<Int, Text> = Some(1)\n')

    def test_annotation_requires_type_arguments(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Option expects 1 type argument, got 0"):
            G.check(OPTION + 'let o: Option = Some(1)\n')

    def test_payload_annotation_arity_error(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Option expects 1 type argument, got 2"):
            G.check(
                'enum Holder<T> { H(Option<T, Int>) }\n'
                + OPTION)

    # -- payload checks --------------------------------------------------
    def test_concrete_payload_keeps_check(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"V: payload Text != Int"):
            G.check('enum E<T> { V(Int), W(T) }\nlet x = V("s")\n')

    def test_concrete_payload_in_annotated_generic_enum(self):
        it = G.run(
            'enum E<T> { V(Int), W(T) }\n'
            'let a: E<Text> = V(3)\n'
            'print a\n')
        self.assertEqual(it.out, ["E.V(3)"])

    def test_duplicate_type_parameter_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type parameter T in enum D"):
            G.check('enum D<T, T> { A(T) }\n')

    # -- namespaced generic enums ---------------------------------------
    def test_namespaced_generic_enum(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "lib.gm").write_text(
                OPTION
                + 'fn wrap(x: Int) -> Option<Int> { return Option.Some(x) }\n',
                encoding="utf-8")
            main = root / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let o: geo.Option<Int> = geo.Option.Some(5)\n'
                'let w: geo.Option<Int> = geo.wrap(9)\n'
                'print o\n'
                'print w\n'
                'match o {\n'
                '  geo.Option.Some(v) => { print v },\n'
                '  None => { print "none" }\n'
                '}\n',
                encoding="utf-8")
            it = G.run_file(str(main))
            self.assertEqual(
                it.out, ["geo.Option.Some(5)", "geo.Option.Some(9)", "5"])

    def test_namespaced_generic_enum_arity_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "lib.gm").write_text(OPTION, encoding="utf-8")
            main = root / "main.gm"
            main.write_text(
                'import "lib.gm" as geo\n'
                'let o: geo.Option<Int, Text> = geo.Option.Some(1)\n',
                encoding="utf-8")
            with self.assertRaisesRegex(
                    G.GamaTypeError,
                    r"geo\.Option expects 1 type argument, got 2"):
                G.check_file(str(main))

    # -- interop with generic functions ---------------------------------
    def test_generic_function_signature_uses_generic_enum(self):
        it = G.run(
            OPTION
            + 'fn keep<T>(x: Option<T>) -> Option<T> { return x }\n'
            + 'let o: Option<Int> = Some(3)\n'
            + 'print keep(o)\n')
        self.assertEqual(it.out, ["Option.Some(3)"])

    # -- formatter -------------------------------------------------------
    def test_formatter_round_trips_generic_enums(self):
        src = (
            'enum Result2<T, E> { Good(T), Bad(E) }\n'
            + OPTION
            + 'let r: Result2<Int, Text> = Result2.Good(1)\n'
            + 'let n: Option<Option<Int>> = Some(Some(2))\n')
        first = gama_fmt.format_source(src)
        self.assertIn("enum Result2<T, E> { Good(T), Bad(E) }", first)
        self.assertIn("enum Option<T> { Some(T), None }", first)
        self.assertIn("Result2<Int, Text>", first)
        self.assertIn("Option<Option<Int>>", first)
        self.assertEqual(gama_fmt.format_source(first), first)

    # -- IR --------------------------------------------------------------
    def test_ir_matches_interpreter_with_generic_enums(self):
        src = (
            OPTION
            + RESULT2
            + 'let o: Option<Int> = Some(5)\n'
            + 'let r: Result2<Int, Text> = Result2.Good(7)\n'
            + 'print o\nprint r\n'
            + 'match o { Some(v) => { print v }, None => { print "none" } }\n'
            + 'match r { Good(v) => { print v }, Bad(e) => { print e } }\n')
        ast = G.check(src)
        self.assertEqual(IR.run_program(ast).out, G.run(src).out)

    def test_ir_erases_annotations_without_miscompiling(self):
        # The IR intentionally ignores annotations: generic enum values are
        # still constructed, printed and matched by enum/variant/arity.
        src = (
            OPTION
            + 'let o: Option<Int> = Option.None\n'
            + 'print o\n'
            + 'match o { Some(v) => { print v }, None => { print "none" } }\n')
        ast = G.check(src)
        self.assertEqual(IR.run_program(ast).out, ["Option.None", "none"])


if __name__ == "__main__":
    unittest.main()
