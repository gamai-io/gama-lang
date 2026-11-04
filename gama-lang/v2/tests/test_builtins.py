import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class ResultBuiltinTests(unittest.TestCase):
    def test_unwrap_ok(self):
        self.assertEqual(G.run('print unwrap(Ok(5))\n').out, ['5'])

    def test_unwrap_err_raises(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print unwrap(Err("bad"))\n')

    def test_unwrap_or_ok_and_err(self):
        it = G.run('print unwrap_or(Ok(5), 7)\nprint unwrap_or(Err("bad"), 7)\n')
        self.assertEqual(it.out, ['5', '7'])

    def test_is_ok_is_err(self):
        it = G.run(
            'print is_ok(Ok(1))\nprint is_ok(Err("x"))\n'
            'print is_err(Ok(1))\nprint is_err(Err("x"))\n')
        self.assertEqual(it.out, ['True', 'False', 'False', 'True'])

    def test_unwrap_non_result_type_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print unwrap(1)\n')

    def test_is_ok_non_result_type_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print is_ok("x")\n')

    def test_unwrap_non_result_runtime(self):
        # `recall` is typed Any, so the checker lets it through; the runtime
        # still has to reject a non-Result value.
        with self.assertRaises(G.GamaRuntimeError):
            G.run('let r = recall("missing")\nprint unwrap(r)\n')


class TextBuiltinTests(unittest.TestCase):
    def test_split(self):
        it = G.run('let parts = split("a,b,c", ",")\nprint len(parts)\nprint parts[1]\n')
        self.assertEqual(it.out, ['3', 'b'])

    def test_split_empty_separator_raises(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print split("abc", "")\n')

    def test_join(self):
        it = G.run('print join(["a", "b", "c"], "-")\nprint join([1, 2], "+")\n')
        self.assertEqual(it.out, ['a-b-c', '1+2'])

    def test_contains_text(self):
        it = G.run('print contains("hello", "ell")\nprint contains("hello", "xyz")\n')
        self.assertEqual(it.out, ['True', 'False'])

    def test_contains_list(self):
        it = G.run('print contains([1, 2, 3], 2)\nprint contains([1, 2, 3], 9)\n')
        self.assertEqual(it.out, ['True', 'False'])

    def test_trim_upper_lower(self):
        it = G.run('print trim("  hi  ")\nprint upper("ab")\nprint lower("AB")\n')
        self.assertEqual(it.out, ['hi', 'AB', 'ab'])

    def test_replace(self):
        self.assertEqual(G.run('print replace("a-b-c", "-", "+")\n').out, ['a+b+c'])

    def test_starts_with_ends_with(self):
        it = G.run(
            'print starts_with("hello", "he")\nprint starts_with("hello", "lo")\n'
            'print ends_with("hello", "lo")\nprint ends_with("hello", "he")\n')
        self.assertEqual(it.out, ['True', 'False', 'True', 'False'])

    def test_split_wrong_arg_type_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print split("a", 1)\n')

    def test_join_wrong_arg_type_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print join("abc", "-")\n')

    def test_arity_errors_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print trim()\n')
        with self.assertRaises(G.GamaTypeError):
            G.check('print replace("a", "b")\n')

    def test_contains_non_text_needle_runtime(self):
        # Second argument is intentionally untyped, so this passes the checker
        # and fails at runtime.
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print contains("a", 1)\n')


class CollectionBuiltinTests(unittest.TestCase):
    def test_keys(self):
        it = G.run('let m = { a: 1, b: 2 }\nprint len(keys(m))\nprint keys(m)[0]\n')
        self.assertEqual(it.out, ['2', 'a'])

    def test_values(self):
        it = G.run('let m = { a: 1, b: 2 }\nprint len(values(m))\nprint values(m)[0]\n')
        self.assertEqual(it.out, ['2', '1'])

    def test_has(self):
        it = G.run('let m = { a: 1 }\nprint has(m, "a")\nprint has(m, "b")\n')
        self.assertEqual(it.out, ['True', 'False'])

    def test_keys_requires_json(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print keys([1, 2])\n')

    def test_has_requires_json(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('print has(1, "a")\n')

    def test_keys_non_map_runtime(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('let x = recall("missing")\nprint keys(x)\n')


class BuiltinMetadataTests(unittest.TestCase):
    def test_effects_and_return_types(self):
        expected = {
            'unwrap': 'Any', 'unwrap_or': 'Any', 'is_ok': 'Bool', 'is_err': 'Bool',
            'split': 'List', 'join': 'Text', 'contains': 'Bool', 'trim': 'Text',
            'upper': 'Text', 'lower': 'Text', 'replace': 'Text',
            'starts_with': 'Bool', 'ends_with': 'Bool',
            'keys': 'List', 'values': 'List', 'has': 'Bool',
        }
        for name, rt in expected.items():
            eff, ret = G.BUILTINS[name]
            self.assertEqual(eff, set(), name)
            self.assertEqual(ret, rt, name)

    def test_pure_builtins_need_no_effects(self):
        src = (
            'fn norm(s: Text) -> Text effects {} { return upper(trim(s)) }\n'
            'print norm("  hi  ")\n')
        self.assertEqual(G.run(src).out, ['HI'])


if __name__ == '__main__':
    unittest.main()
