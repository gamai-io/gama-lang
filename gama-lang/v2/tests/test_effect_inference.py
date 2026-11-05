"""Effect inference for functions that omit the ``effects`` clause.

An omitted clause (``effects is None``) is inferred from the body and from the
effects of called functions/methods.  An explicit set -- including ``effects
{}`` -- remains the checked contract.  Inferred declarations stay without an
effects clause in source and in the formatter output.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G  # noqa: E402


def _checker(src):
    ast = G.parse(src)
    checker = G.Checker()
    checker.program(ast)
    return checker, ast


def _fn(ast, name):
    for node in ast:
        if isinstance(node, G.Fn) and node.name == name:
            return node
    raise AssertionError(f"no function {name}")


def _method(ast, record, name):
    for node in ast:
        if isinstance(node, G.RecordDecl) and node.name == record:
            for m in node.methods:
                if m.name == name:
                    return m
    raise AssertionError(f"no method {record}.{name}")


class EffectInferenceTests(unittest.TestCase):
    def test_print_is_inferred(self):
        checker, ast = _checker('fn log(x: Text) { print x }\nlog("hi")\n')
        fn = _fn(ast, "log")
        self.assertIsNone(fn.effects)
        self.assertEqual(fn.inferred_effects, {"io.print"})
        self.assertEqual(checker.inferred_fn_effects["log"], {"io.print"})

    def test_effect_propagates_through_calls(self):
        src = 'fn a() { print "a" }\nfn b() { a() }\nb()\n'
        checker, ast = _checker(src)
        self.assertEqual(_fn(ast, "a").inferred_effects, {"io.print"})
        self.assertEqual(_fn(ast, "b").inferred_effects, {"io.print"})

    def test_pure_function_infers_empty_set(self):
        checker, ast = _checker(
            "fn pure(x: Int) -> Int { return x }\nprint pure(1)\n")
        self.assertEqual(_fn(ast, "pure").inferred_effects, set())

    def test_explicit_empty_still_rejects(self):
        with self.assertRaises(G.GamaEffectError) as caught:
            G.check('fn f() effects {} { print "x" }\nf()\n')
        self.assertIn("io.print", caught.exception.msg)

    def test_explicit_declaration_accepts_and_rejects(self):
        G.check('fn ok() effects { io.print } { print "x" }\nok()\n')
        with self.assertRaises(G.GamaEffectError):
            G.check('fn bad() effects { io.print } { sql_write("x") }\nbad()\n')

    def test_uncovered_effect_rejected_in_omitted_caller(self):
        # The callee declares io.print; a caller declaring an empty set must
        # still reject the propagated effect.
        src = ('fn a() effects { io.print } { print "a" }\n'
               'fn b() effects {} { a() }\nb()\n')
        with self.assertRaises(G.GamaEffectError) as caught:
            G.check(src)
        self.assertIn("io.print", caught.exception.msg)

    def test_mutual_recursion_fixpoint(self):
        src = (
            "fn a(n: Int) {\n"
            "  if n > 0 {\n"
            "    print n\n"
            "    b(n - 1)\n"
            "  }\n"
            "}\n"
            "fn b(n: Int) {\n"
            "  if n > 0 {\n"
            "    a(n - 1)\n"
            "  }\n"
            "}\n"
            "a(3)\n"
        )
        checker, ast = _checker(src)
        self.assertEqual(_fn(ast, "a").inferred_effects, {"io.print"})
        self.assertEqual(_fn(ast, "b").inferred_effects, {"io.print"})

    def test_self_recursion_keeps_inferred_effect(self):
        src = (
            "fn r(n: Int) {\n"
            "  if n > 0 {\n"
            '    print n\n'
            "    r(n - 1)\n"
            "  }\n"
            "}\n"
            "r(2)\n"
        )
        checker, ast = _checker(src)
        self.assertEqual(_fn(ast, "r").inferred_effects, {"io.print"})

    def test_method_effects_inferred_and_propagated(self):
        src = (
            "record R {\n"
            "  x: Int\n"
            "  fn show(self: R) { print self.x }\n"
            "}\n"
            "fn call(r: R) { r.show() }\n"
            "let r = R { x: 1 }\n"
            "call(r)\n"
        )
        checker, ast = _checker(src)
        self.assertIsNone(_method(ast, "R", "show").effects)
        self.assertEqual(_method(ast, "R", "show").inferred_effects,
                         {"io.print"})
        self.assertEqual(_fn(ast, "call").inferred_effects, {"io.print"})

    def test_explicit_empty_method_still_rejects(self):
        src = (
            "record R {\n"
            "  fn show(self: R) effects {} { print 1 }\n"
            "}\n"
        )
        with self.assertRaises(G.GamaEffectError):
            G.check(src)

    def test_top_level_effects_unchanged(self):
        G.check('print "top"\n')
        G.check('sql_write("x")\n')
        self.assertEqual(G.run('print "top"\n').out, ["top"])

    def test_formatter_keeps_explicit_and_omits_inferred(self):
        explicit = 'fn send(x: Text) effects { io.print } { print x }\n'
        out = G.fmt_source(explicit)
        self.assertIn("effects {io.print}", out.replace(", ", ""))
        G.check(out)

        inferred = "fn log(x: Text) { print x }\n"
        out = G.fmt_source(inferred)
        self.assertNotIn("effects", out)
        self.assertEqual(out, G.fmt_source(out))
        G.check(out)


if __name__ == "__main__":
    unittest.main()
