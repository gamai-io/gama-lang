"""Return type inference for omitted ``-> Type`` annotations.

Semantics under test:

* A function, record method or lambda without a return annotation infers its
  type from every ``return expr`` lexically in its body (nested functions and
  lambdas are separate scopes and are excluded).
* Identical branch types stay as-is; ``Int`` and ``Decimal`` merge to
  ``Decimal``; any other combination is a ``GamaTypeError`` reported at the
  later return.
* A body with no ``return`` infers ``Any`` (the historical permissive result).
  Direct or mutual recursion without an annotation also uses ``Any`` at the
  recursive position rather than looping.
* A bare parameter return whose annotation names a type parameter keeps that
  parameter (``fn id<T>(x: T) { return x }`` behaves like ``-> T``), so generic
  call sites bind it from the arguments.
* Inferred types are visible to callers, generic functions, methods and bound
  lambdas, and call-site mismatches are static ``GamaTypeError``s.
* The AST's ``ret`` field is never rewritten: the formatter preserves the
  omission and the IR continues to treat a missing annotation as ``Any``.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR
import gama_fmt as F


def fn_node(ast, name):
    for node in ast:
        if isinstance(node, G.Fn) and node.name == name:
            return node
    raise AssertionError("function %r not found" % name)


class ReturnInferenceTypes(unittest.TestCase):
    def test_simple_int(self):
        ast = G.check('fn add1(x: Int) { return x + 1 }\nprint add1(2)\n')
        self.assertEqual(fn_node(ast, "add1")._inferred_ret, "Int")

    def test_simple_text(self):
        ast = G.check('fn greet(name: Text) { return "hi " + name }\n'
                      'print greet("bob")\n')
        self.assertEqual(fn_node(ast, "greet")._inferred_ret, "Text")

    def test_if_branches_same_type(self):
        ast = G.check(
            'fn pick(c: Bool) { if c { return 1 } else { return 2 } }\n'
            'let y: Int = pick(true)\nprint y\n')
        self.assertEqual(fn_node(ast, "pick")._inferred_ret, "Int")

    def test_numeric_branches_merge_to_decimal(self):
        ast = G.check(
            'fn mix(c: Bool) { if c { return 1 } else { return 2.5 } }\n'
            'let d: Decimal = mix(true)\nprint d\n')
        self.assertEqual(fn_node(ast, "mix")._inferred_ret, "Decimal")

    def test_incompatible_branches_are_rejected(self):
        with self.assertRaises(G.GamaTypeError) as cm:
            G.check('fn bad(c: Bool) { if c { return 1 } else { return "x" } }\n'
                    'print 1\n')
        self.assertIn("incompatible return types", str(cm.exception))

    def test_no_return_infers_any(self):
        ast = G.check('fn noret(x: Int) { let y = x }\nprint noret(1)\n')
        self.assertEqual(fn_node(ast, "noret")._inferred_ret, "Any")

    def test_direct_recursion_infers_any(self):
        ast = G.check('fn loop(x: Int) { return loop(x) }\nprint 1\n')
        self.assertEqual(fn_node(ast, "loop")._inferred_ret, "Any")

    def test_mutual_recursion_infers_any(self):
        ast = G.check('fn a(x: Int) { return b(x) }\n'
                      'fn b(x: Int) { return a(x) }\nprint 1\n')
        self.assertEqual(fn_node(ast, "a")._inferred_ret, "Any")
        self.assertEqual(fn_node(ast, "b")._inferred_ret, "Any")

    def test_nested_function_returns_are_excluded(self):
        ast = G.check(
            'fn outer() {\n'
            '  fn inner() { return "text" }\n'
            '  return 1\n'
            '}\n'
            'let y: Int = outer()\nprint y\n')
        outer = fn_node(ast, "outer")
        self.assertEqual(outer._inferred_ret, "Int")
        inner = next(n for n in outer.body
                     if isinstance(n, G.Fn) and n.name == "inner")
        self.assertEqual(inner._inferred_ret, "Text")

    def test_forward_reference_uses_inferred_callee(self):
        ast = G.check(
            'fn outer(x: Int) { return inner(x) + 1 }\n'
            'fn inner(x: Int) { return x }\n'
            'let y: Int = outer(2)\nprint y\n')
        self.assertEqual(fn_node(ast, "outer")._inferred_ret, "Int")
        self.assertEqual(fn_node(ast, "inner")._inferred_ret, "Int")

    def test_generic_bare_parameter_keeps_type_parameter(self):
        ast = G.check('fn id<T>(x: T) { return x }\n'
                      'let y: Int = id(5)\nprint y\n')
        self.assertEqual(fn_node(ast, "id")._inferred_ret, "T")

    def test_annotated_function_is_unchanged(self):
        ast = G.check('fn f(x: Int) -> Text { return "ok" }\nprint f(1)\n')
        node = fn_node(ast, "f")
        self.assertEqual(node.ret, "Text")
        self.assertFalse(hasattr(node, "_inferred_ret"))
        with self.assertRaises(G.GamaTypeError):
            G.check('fn f(x: Int) -> Text { return x }\nprint f(1)\n')


class ReturnInferenceCallers(unittest.TestCase):
    def test_function_result_is_usable_as_annotated_value(self):
        self.assertEqual(
            G.run('fn add1(x: Int) { return x + 1 }\n'
                  'let y: Int = add1(2)\nprint y\n').out, ["3"])

    def test_function_return_mismatch_at_call_site(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('fn add1(x: Int) { return x + 1 }\n'
                    'let y: Text = add1(2)\nprint y\n')

    def test_method_can_omit_return_type(self):
        ast = G.check(
            'record Point {\n'
            '  x: Int\n'
            '  fn scaled(self: Point) { return self.x * 2 }\n'
            '}\n'
            'let p = Point(3)\n'
            'let y: Int = p.scaled()\n'
            'print y\n')
        rec = next(n for n in ast if isinstance(n, G.RecordDecl))
        self.assertEqual(rec.methods[0]._inferred_ret, "Int")

    def test_method_return_mismatch_at_call_site(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('record Point {\n'
                    '  x: Int\n'
                    '  fn scaled(self: Point) { return self.x * 2 }\n'
                    '}\n'
                    'let p = Point(3)\n'
                    'let y: Text = p.scaled()\n'
                    'print y\n')

    def test_lambda_can_omit_return_type(self):
        ast = G.check('let f = fn(x: Int) { return x * 2 }\n'
                      'let y: Int = f(3)\nprint y\n')
        lam = ast[0].expr
        self.assertEqual(lam._inferred_ret, "Int")
        self.assertEqual(
            G.run('let f = fn(x: Int) { return x * 2 }\n'
                  'let y: Int = f(3)\nprint y\n').out, ["6"])

    def test_lambda_return_mismatch_at_call_site(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let f = fn(x: Int) { return x * 2 }\n'
                    'let y: Text = f(3)\nprint y\n')

    def test_generic_call_binds_inferred_parameter(self):
        self.assertEqual(
            G.run('fn id<T>(x: T) { return x }\n'
                  'let y: Int = id(5)\nprint y\n').out, ["5"])
        with self.assertRaises(G.GamaTypeError):
            G.check('fn id<T>(x: T) { return x }\n'
                    'let y: Text = id(5)\nprint y\n')


class ReturnInferenceFormatterAndIR(unittest.TestCase):
    def test_formatter_preserves_omitted_annotation(self):
        src = ('fn add1(x: Int) { return x + 1 }\n'
               'let f = fn(x: Int) { return x * 2 }\n'
               'print add1(f(3))\n')
        out = F.format_source(src)
        self.assertIn("fn add1(x: Int) {", out)
        self.assertIn("fn(x: Int) {", out)
        self.assertNotIn("->", out)
        self.assertEqual(F.format_source(out), out)
        self.assertEqual(G.run(out).out, ["7"])

    def test_ir_uses_any_for_missing_annotation(self):
        src = ('fn add1(x: Int) { return x + 1 }\n'
               'record Point {\n'
               '  x: Int\n'
               '  fn scaled(self: Point) { return self.x * 2 }\n'
               '}\n'
               'let p = Point(3)\n'
               'let f = fn(x: Int) { return x * 2 }\n'
               'print add1(f(3))\nprint p.scaled()\n')
        ast = G.check(src)
        self.assertIsNone(fn_node(ast, "add1").ret)
        self.assertEqual(IR.run_program(ast).out, G.run(src).out)
        self.assertEqual(IR.run_program(ast).out, ["7", "6"])


if __name__ == '__main__':
    unittest.main()
