"""Explicit call type arguments for generic named functions.

Syntax::

    fn id<T>(x: T) -> T { return x }
    let n: Int = id<Int>(5)
    let s: Text = id<Text>("x")

    fn pair<A, B>(a: A, b: B) -> Text { return text(a) + ":" + text(b) }
    pair<Int, Text>(1, "a")

Type arguments are erased at runtime (the interpreter ignores them) and are
validated by the checker: the count must match the function's type parameters,
each name must be a concrete type or an in-scope outer type parameter, and
ordinary argument type checking still applies with the explicit bindings.
Comparisons such as ``a < b`` remain comparison operators.
"""
import dataclasses
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt


def shape(node):
    """Structural AST shape ignoring source positions and set ordering."""
    if dataclasses.is_dataclass(node):
        items = []
        for f in dataclasses.fields(node):
            if f.name in ("line", "col"):
                continue
            items.append((f.name, shape(getattr(node, f.name))))
        return (type(node).__name__, tuple(items))
    if isinstance(node, (list, tuple)):
        return tuple(shape(x) for x in node)
    if isinstance(node, dict):
        return tuple((k, shape(v)) for k, v in node.items())
    if isinstance(node, set):
        return tuple(sorted(shape(x) for x in node))
    return node


ID = 'fn id<T>(x: T) -> T { return x }\n'
PAIR = ('fn pair<A, B>(a: A, b: B) -> Text '
        '{ return text(a) + ":" + text(b) }\n')


class ExplicitTypeArgumentTests(unittest.TestCase):
    # -- accepted forms ---------------------------------------------------
    def test_identity_int(self):
        it = G.run(ID + 'let n: Int = id<Int>(5)\nprint n\n')
        self.assertEqual(it.out, ["5"])

    def test_identity_text(self):
        it = G.run(ID + 'let s: Text = id<Text>("x")\nprint s\n')
        self.assertEqual(it.out, ["x"])

    def test_pair_two_type_arguments(self):
        it = G.run(PAIR + 'print pair<Int, Text>(1, "a")\n')
        self.assertEqual(it.out, ["1:a"])

    def test_result_type_comes_from_explicit_argument(self):
        it = G.run(ID +
                   'fn plus<U>(x: U) -> Int { return id<Int>(1) }\n'
                   'let n: Int = plus<Text>("ignored")\n'
                   'print n\n')
        self.assertEqual(it.out, ["1"])

    def test_runtime_ignores_type_args(self):
        # Erasure: the value passes through unchanged; Any is a valid
        # concrete-ish type argument that does not constrain the payload.
        it = G.run(ID + 'print id<Any>("still text")\n')
        self.assertEqual(it.out, ["still text"])

    # -- generic-to-generic and outer type parameters ---------------------
    def test_nested_generic_call_uses_outer_type_parameter(self):
        it = G.run(ID +
                   'fn wrap<U>(x: U) -> U { return id<U>(x) }\n'
                   'print wrap<Text>("z")\n'
                   'print wrap<Int>(7)\n')
        self.assertEqual(it.out, ["z", "7"])

    def test_generic_to_generic_without_explicit_args_unchanged(self):
        it = G.run(ID +
                   'fn wrap<U>(x: U) -> U { return id(x) }\n'
                   'print wrap("q")\n')
        self.assertEqual(it.out, ["q"])

    # -- checker diagnostics ---------------------------------------------
    def test_extra_type_argument_is_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(ID + 'print id<Int, Text>(5)\n')

    def test_missing_type_argument_is_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(PAIR + 'print pair<Int>(1, "a")\n')

    def test_empty_type_argument_list_is_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(ID + 'print id<>(5)\n')

    def test_unknown_type_argument_is_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(ID + 'print id<NoSuchType>(5)\n')

    def test_non_type_parameter_name_is_unknown(self):
        # A type parameter from another function is not in scope here.
        with self.assertRaises(G.GamaTypeError):
            G.check(ID + 'fn other<T>(x: T) -> T { return x }\n'
                        'fn bad<U>(x: U) -> U { return id<T>(x) }\n')

    def test_explicit_concrete_argument_is_checked(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(ID + 'print id<Int>("x")\n')

    def test_pair_argument_types_are_checked(self):
        with self.assertRaises(G.GamaTypeError):
            G.check(PAIR + 'print pair<Int, Text>(1, 2)\n')

    # -- comparisons are untouched ---------------------------------------
    def test_comparison_operators_still_work(self):
        it = G.run('print 1 < 2\nprint 2 > 1\n'
                   'print 1 <= 1\nprint 1 >= 2\n'
                   'let a: Int = 3\nlet b: Int = 4\nprint a < b\n')
        self.assertEqual(it.out, ["True", "True", "True", "False", "True"])

    def test_generic_name_in_comparison_is_not_type_arguments(self):
        it = G.run(ID + 'print id(1) < 2\n')
        self.assertEqual(it.out, ["True"])

    def test_comparison_expression_keeps_binary_ast(self):
        ast = G.parse('fn id<T>(x: T) -> T { return x }\n'
                      'let n: Int = id(1)\nprint n < 2\n')
        printed = ast[-1]
        self.assertIsInstance(printed.expr, G.Binary)
        self.assertEqual(printed.expr.op, "<")

    # -- formatter --------------------------------------------------------
    def test_formatter_round_trips_explicit_type_args(self):
        src = ID + PAIR + 'let n: Int = id<Int>(5)\n' \
                          'let s: Text = id<Text>("x")\n' \
                          'print pair<Int, Text>(1, "a")\n'
        out = gama_fmt.format_source(src)
        self.assertIn("id<Int>(5)", out)
        self.assertIn('id<Text>("x")', out)
        self.assertIn('pair<Int, Text>(1, "a")', out)
        self.assertEqual(out, gama_fmt.format_source(out))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))

    def test_formatter_keeps_comparison_with_generic_name(self):
        src = ID + 'let n: Int = id(1)\nprint n < 2\n'
        out = gama_fmt.format_source(src)
        self.assertIn("print n < 2", out)
        self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))

    def test_formatter_wraps_call_with_type_args(self):
        src = ID + 'let n: Int = id<Int>(' \
                   'text(1) + text(2) + text(3) + text(4) + text(5) + ' \
                   'text(6) + text(7) + text(8) + text(9) + text(10))\n'
        out = gama_fmt.format_source(src, width=40)
        self.assertIn("id<Int>(", out)
        self.assertIn("\n", out)
        self.assertEqual(out, gama_fmt.format_source(out, width=40))
        self.assertEqual(shape(G.parse(src)), shape(G.parse(out)))


if __name__ == "__main__":
    unittest.main()
