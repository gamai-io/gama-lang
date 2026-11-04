import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class NumericBuiltinTests(unittest.TestCase):
    def test_abs_int_and_decimal(self):
        it = G.run('print abs(-5)\nprint abs(5)\nprint abs(-2.5)\nprint abs(2.5)\n')
        self.assertEqual(it.out, ['5', '5', '2.5', '2.5'])

    def test_min_max_variadic(self):
        it = G.run('print min(3, 1, 2)\nprint max(3, 1, 2)\nprint min(1.5, 2)\nprint max(1, 2.5)\n')
        self.assertEqual(it.out, ['1', '3', '1.5', '2.5'])

    def test_min_max_single_arg(self):
        it = G.run('print min(7)\nprint max(7)\n')
        self.assertEqual(it.out, ['7', '7'])

    def test_floor_ceil(self):
        it = G.run('print floor(2.9)\nprint floor(-2.1)\nprint ceil(2.1)\nprint ceil(-2.9)\n'
                   'print floor(3)\nprint ceil(3)\n')
        self.assertEqual(it.out, ['2', '-3', '3', '-2', '3', '3'])

    def test_numeric_wrong_type_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print abs("x")\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print floor(true)\n')

    def test_numeric_wrong_arity_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print abs()\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print min()\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print ceil(1, 2)\n')

    def test_numeric_non_number_runtime(self):
        # `recall` is typed Any; the runtime still validates the value.
        with self.assertRaises(G.GamaRuntimeError):
            G.run('let x = recall("missing")\nprint abs(x)\n')


class TextListBuiltinTests(unittest.TestCase):
    def test_reverse_text_and_list(self):
        it = G.run('print reverse("abc")\nprint reverse("")\n'
                   'print reverse([1, 2, 3])\nprint reverse([])\n')
        self.assertEqual(it.out, ['cba', '', '[3, 2, 1]', '[]'])

    def test_slice_text_and_list(self):
        it = G.run('print slice("abcdef", 1, 4)\nprint slice("abc", 0, 0)\n'
                   'print slice([1, 2, 3, 4], 1, 3)\nprint slice([], 0, 0)\n')
        self.assertEqual(it.out, ['bcd', '', '[2, 3]', '[]'])

    def test_slice_bounds_errors(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print slice([1, 2, 3], 0, 5)\n')
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print slice([1, 2, 3], -1, 2)\n')
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print slice([1, 2, 3], 2, 1)\n')

    def test_index_of_text(self):
        it = G.run('print index_of("banana", "na")\nprint index_of("banana", "zz")\n'
                   'print index_of("abc", "")\n')
        self.assertEqual(it.out, ['2', '-1', '0'])

    def test_index_of_list_and_empty(self):
        it = G.run('print index_of([1, 2, 3], 2)\nprint index_of([1, 2, 3], 9)\n'
                   'print index_of([], 1)\n')
        self.assertEqual(it.out, ['1', '-1', '-1'])

    def test_text_list_wrong_type_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print reverse(1)\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print sort("abc")\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print slice([1], "a", 1)\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print index_of(1, 2)\n')

    def test_text_list_wrong_arity_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print reverse()\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print slice([1], 0)\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print index_of([1])\n')

    def test_index_of_text_wrong_needle_runtime(self):
        # The needle is intentionally untyped, so this passes the checker
        # and fails at runtime.
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print index_of("abc", 1)\n')


class SortBuiltinTests(unittest.TestCase):
    def test_sort_numbers(self):
        it = G.run('print sort([3, 1, 2])\nprint sort([2.5, 1.5])\n')
        self.assertEqual(it.out, ['[1, 2, 3]', '["1.5", "2.5"]'])

    def test_sort_text(self):
        self.assertEqual(G.run('print sort(["b", "a", "c"])\n').out, ['["a", "b", "c"]'])

    def test_sort_empty(self):
        self.assertEqual(G.run('print sort([])\n').out, ['[]'])

    def test_sort_does_not_mutate_input(self):
        it = G.run('let xs = [3, 1, 2]\nlet ys = sort(xs)\nprint xs\nprint ys\n')
        self.assertEqual(it.out, ['[3, 1, 2]', '[1, 2, 3]'])

    def test_sort_is_stable_for_equal_keys(self):
        # 1 and 1.0 compare equal but render differently, so the two runs
        # reveal whether the original order of equal elements was preserved.
        it = G.run('print sort([1, 1.0])\nprint sort([1.0, 1])\n')
        self.assertEqual(it.out, ['[1, "1.0"]', '["1.0", 1]'])

    def test_sort_rejects_mixed_types_runtime(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print sort([1, "a"])\n')

    def test_sort_wrong_type_and_arity_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print sort("abc")\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print sort()\n')


class ConversionBuiltinTests(unittest.TestCase):
    def test_to_int_accepted_forms(self):
        it = G.run('print to_int(5)\nprint to_int(-5)\nprint to_int(5.0)\n'
                   'print to_int("42")\nprint to_int(" 42 ")\nprint to_int("-7")\n')
        self.assertEqual(it.out, ['5', '-5', '5', '42', '42', '-7'])

    def test_to_int_rejects_non_integral_decimal(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print to_int(5.5)\n')

    def test_to_int_rejects_bad_text(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print to_int("4.5")\n')
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print to_int("abc")\n')

    def test_to_int_wrong_type_and_arity_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print to_int(true)\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print to_int()\n')


class StaticTypingTests(unittest.TestCase):
    def test_declared_return_types_are_satisfied(self):
        src = (
            'fn a() -> Int { return abs(-5) }\n'
            'fn d() -> Decimal { return abs(-2.5) }\n'
            'fn f() -> Int { return floor(2.9) }\n'
            'fn r() -> Text { return reverse("ab") }\n'
            'fn s() -> List { return sort([2, 1]) }\n'
            'fn i() -> Int { return index_of([1, 2], 2) }\n'
            'print a()\nprint d()\nprint f()\nprint r()\nprint s()\nprint i()\n')
        self.assertEqual(G.run(src).out, ['5', '2.5', '2', 'ba', '[1, 2]', '1'])


class BuiltinMetadataTests(unittest.TestCase):
    def test_new_builtins_are_pure_and_typed(self):
        expected = {
            'abs': 'Any', 'min': 'Any', 'max': 'Any',
            'floor': 'Int', 'ceil': 'Int',
            'reverse': 'Any', 'slice': 'Any', 'index_of': 'Int', 'sort': 'List',
            'to_int': 'Int',
        }
        for name, rt in expected.items():
            eff, ret = G.BUILTINS[name]
            self.assertEqual(eff, set(), name)
            self.assertEqual(ret, rt, name)


if __name__ == '__main__':
    unittest.main()
