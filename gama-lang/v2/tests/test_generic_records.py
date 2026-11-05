"""Generic records with type erasure.

Syntax::

    record Box<T> { value: T }
    record Pair<A, B> { first: A, second: B }

Type parameters are erased: a ``T``-typed field erases to ``Any`` while
concrete fields keep their declared type.  Annotations (``Box<Int>``) and
explicit constructor arguments (``Box<Int>(5)``) bind the parameters so the
field types can still be checked; without them the erased field accepts any
value.  Generic type references erase to their nominal base (``Box<Box<Int>>``
is a ``Box`` at runtime).

Limits: no bounds, no generic enums, no method-level type parameters (methods
may use the record's parameters), and no explicit type arguments on namespaced
constructors (``geo.Box<Int>(5)``); annotate the binding instead.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir


class GenericRecordTests(unittest.TestCase):
    # -- Box / Pair ------------------------------------------------------
    def test_box_construction_and_field_access(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'let b: Box<Int> = Box(5)\n'
            'print b.value\n'
            'print b\n')
        self.assertEqual(it.out, ["5", "Box { value: 5 }"])

    def test_pair_two_type_parameters(self):
        it = G.run(
            'record Pair<A, B> { first: A, second: B }\n'
            'let p: Pair<Text, Int> = Pair("x", 2)\n'
            'print p.first\nprint p.second\nprint p\n')
        self.assertEqual(
            it.out, ["x", "2", 'Pair { first: "x", second: 2 }'])

    def test_named_record_literal_with_generic_declaration(self):
        it = G.run(
            'record Pair<A, B> { first: A, second: B }\n'
            'let p: Pair<Int, Text> = Pair { second: "y", first: 1 }\n'
            'print p.first\nprint p.second\n')
        self.assertEqual(it.out, ["1", "y"])

    # -- nested type references -----------------------------------------
    def test_nested_box(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'let n: Box<Box<Int>> = Box(Box(5))\n'
            'print n.value.value\n'
            'print n.value\n')
        self.assertEqual(it.out, ["5", "Box { value: 5 }"])

    # -- explicit constructor type arguments ----------------------------
    def test_explicit_type_arguments_construct(self):
        it = G.run(
            'record Box<T> { value: T }\n'
            'let b: Box<Int> = Box<Int>(5)\n'
            'print b.value\n'
            'print Box<Text>("s")\n')
        self.assertEqual(it.out, ["5", 'Box { value: "s" }'])

    def test_explicit_type_arguments_check_fields(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Box\.value: Text != Int"):
            G.check(
                'record Box<T> { value: T }\n'
                'let b: Box<Int> = Box<Int>("x")\n')

    def test_explicit_type_argument_arity(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Box expects 1 type argument, got 2"):
            G.check(
                'record Box<T> { value: T }\n'
                'let b: Box<Int> = Box<Int, Int>(1)\n')

    # -- annotation checks ----------------------------------------------
    def test_annotation_arity_error(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Box expects 1 type argument, got 2"):
            G.check(
                'record Box<T> { value: T }\n'
                'let b: Box<Int, Int> = Box(1)\n')

    def test_non_generic_annotation_rejects_arguments(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Plain expects 0 type arguments, got 1"):
            G.check(
                'record Plain { x: Int }\n'
                'let p: Plain<Int> = Plain(1)\n')

    def test_annotation_binds_parameters_for_constructor_check(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Pair\.second: Text != Int"):
            G.check(
                'record Pair<A, B> { first: A, second: B }\n'
                'let p: Pair<Text, Int> = Pair("x", "y")\n')

    def test_erased_field_accepts_any_value_without_annotation(self):
        # Erasure is real: without a binding, T is Any.
        it = G.run(
            'record Box<T> { value: T }\n'
            'print Box(5)\n'
            'print Box("x")\n'
            'print Box(null)\n')
        self.assertEqual(it.out, [
            "Box { value: 5 }", 'Box { value: "x" }', "Box { value: null }"])

    # -- concrete fields keep their type --------------------------------
    def test_concrete_field_type_is_checked(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Tagged\.tag: Int != Text"):
            G.check(
                'record Tagged<T> { tag: Text, value: T }\n'
                'print Tagged(1, 2)\n')

    def test_concrete_field_accepts_correct_type(self):
        it = G.run(
            'record Tagged<T> { tag: Text, value: T }\n'
            'let t: Tagged<Int> = Tagged("ok", 1)\n'
            'print t.tag\nprint t.value\n')
        self.assertEqual(it.out, ["ok", "1"])

    # -- declaration errors ---------------------------------------------
    def test_duplicate_type_parameter(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate type parameter T"):
            G.check('record Box<T, T> { value: T }\n')

    def test_duplicate_field_still_rejected(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"duplicate field value"):
            G.check('record Box<T> { value: T, value: T }\n')

    def test_wrong_field_count(self):
        with self.assertRaisesRegex(G.GamaTypeError,
                                    r"Box expects 1 fields, got 0"):
            G.check('record Box<T> { value: T }\nprint Box()\n')

    # -- methods ---------------------------------------------------------
    def test_methods_use_record_type_parameters(self):
        it = G.run(
            'record Box<T> {\n'
            '  value: T\n'
            '  fn get(self: Box<T>) -> T { return self.value }\n'
            '  fn describe(self: Box<T>) -> Text '
            '{ return "box:" + text(self.value) }\n'
            '}\n'
            'let b: Box<Int> = Box(7)\n'
            'print b.get()\n'
            'print b.describe()\n')
        self.assertEqual(it.out, ["7", "box:7"])

    def test_method_self_arity_is_checked(self):
        with self.assertRaisesRegex(
                G.GamaTypeError,
                r"self type Box<Int, Int> expects 1 type argument, got 2"):
            G.check(
                'record Box<T> {\n'
                '  value: T\n'
                '  fn get(self: Box<Int, Int>) -> T { return self.value }\n'
                '}\n')

    def test_methods_cannot_declare_type_parameters(self):
        with self.assertRaisesRegex(
                G.GamaError,
                r"methods cannot declare their own type parameters"):
            G.check(
                'record Box<T> {\n'
                '  value: T\n'
                '  fn m<U>(self: Box<T>) -> Int { return 1 }\n'
                '}\n')

    # -- namespaced generic records -------------------------------------
    def _module(self, directory, name, source):
        path = pathlib.Path(directory) / name
        path.write_text(source, encoding="utf-8")
        return str(path)

    def test_namespaced_generic_record(self):
        with tempfile.TemporaryDirectory() as d:
            self._module(d, "geo.gm", "record Box<T> { value: T }\n")
            main = self._module(
                d, "main.gm",
                'import "geo.gm" as geo\n'
                'let b: geo.Box<Int> = geo.Box(5)\n'
                'print b.value\n')
            self.assertEqual(G.run_file(main).out, ["5"])
            ast = G.check_file(main)
            self.assertEqual(gama_ir.run_program(ast).out, ["5"])

    def test_namespaced_annotation_arity_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            self._module(d, "geo.gm", "record Box<T> { value: T }\n")
            bad = self._module(
                d, "bad.gm",
                'import "geo.gm" as geo\n'
                'let b: geo.Box<Int, Text> = geo.Box(5)\n')
            with self.assertRaisesRegex(
                    G.GamaTypeError,
                    r"geo\.Box expects 1 type argument, got 2"):
                G.check_file(bad)

    def test_namespaced_explicit_constructor_args_are_not_supported(self):
        # Documented limit: explicit type args are only recognised for
        # locally declared records (the parser pre-scans local record names).
        with tempfile.TemporaryDirectory() as d:
            self._module(d, "geo.gm", "record Box<T> { value: T }\n")
            main = self._module(
                d, "main.gm",
                'import "geo.gm" as geo\n'
                'let b: geo.Box<Int> = geo.Box<Int>(5)\n')
            with self.assertRaises(G.GamaError):
                G.check_file(main)

    # -- formatting ------------------------------------------------------
    def test_formatter_emits_record_type_parameters(self):
        src = (
            'record Box<T> { value: T }\n'
            'record Pair<A,B> { first: A, second: B }\n')
        out = gama_fmt.format_source(src)
        self.assertIn("record Box<T> { value: T }", out)
        self.assertIn("record Pair<A, B> { first: A, second: B }", out)

    def test_formatter_keeps_method_self_type_and_explicit_args(self):
        src = (
            'record Box<T> {\n'
            '  value: T\n'
            '  fn get(self: Box<T>) -> T { return self.value }\n'
            '}\n'
            'let b: Box<Int> = Box<Int>(5)\n'
            'print b.get()\n')
        out = gama_fmt.format_source(src)
        self.assertIn("fn get(self: Box<T>) -> T", out)
        self.assertIn("Box<Int>(5)", out)
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_formatter_round_trips_record_type_parameters(self):
        src = 'record Pair<A, B> { first: A, second: B }\n'
        out = gama_fmt.format_source(src)
        self.assertEqual(G.parse(out)[0].type_params, ["A", "B"])

    # -- IR --------------------------------------------------------------
    def test_ir_matches_interpreter_for_generic_records(self):
        src = (
            'record Box<T> { value: T }\n'
            'record Pair<A, B> { first: A, second: B }\n'
            'let b: Box<Int> = Box<Int>(5)\n'
            'print b.value\n'
            'print Box(Box(2))\n'
            'print Pair("x", 2)\n')
        expected = G.run(src).out
        got = gama_ir.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    def test_ir_erases_type_parameters(self):
        ast = G.check(
            'record Box<T> { value: T }\n'
            'print Box(9)\n')
        self.assertEqual(gama_ir.run_program(ast).out, ["Box { value: 9 }"])

    def test_ir_rejects_record_methods(self):
        ast = G.check(
            'record Box<T> {\n'
            '  value: T\n'
            '  fn get(self: Box<T>) -> T { return self.value }\n'
            '}\n'
            'let b: Box<Int> = Box(1)\n'
            'print b.get()\n')
        with self.assertRaisesRegex(gama_ir.IRUnsupported,
                                    r"record methods"):
            gama_ir.run_program(ast)


if __name__ == "__main__":
    unittest.main()
