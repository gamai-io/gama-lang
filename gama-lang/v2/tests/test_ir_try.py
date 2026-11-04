"""IR-vs-interpreter tests for the Result propagation operator (``expr?``).

Successful programs must print exactly what the reference interpreter prints.
Compile/type errors are compared to the checker's wording, and the IR-only
runtime guard for unchecked ASTs must fail as a ``GamaError`` instead of
leaking a Python exception.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR


class IRTryMatchesInterpreter(unittest.TestCase):
    def assert_same(self, src):
        expected = G.run(src).out
        got = IR.run_program(G.check(src)).out
        self.assertEqual(got, expected)

    # -- happy paths -----------------------------------------------------
    def test_ok_path_unwraps_payload(self):
        self.assert_same(
            'fn get(ok: Bool) -> Result effects {} {\n'
            '  if ok { return Ok(42) }\n'
            '  return Err("nope")\n'
            '}\n'
            'fn run_it(ok: Bool) -> Result effects {} {\n'
            '  v := get(ok)?\n'
            '  return Ok(v + 1)\n'
            '}\n'
            'print run_it(true)\n')

    def test_err_propagation_returns_err(self):
        self.assert_same(
            'fn get(ok: Bool) -> Result effects {} {\n'
            '  if ok { return Ok(42) }\n'
            '  return Err("nope")\n'
            '}\n'
            'fn run_it(ok: Bool) -> Result effects {} {\n'
            '  v := get(ok)?\n'
            '  return Ok(v + 1)\n'
            '}\n'
            'print run_it(false)\n')

    def test_err_payload_is_preserved(self):
        self.assert_same(
            'fn fail() -> Result effects {} {\n'
            '  return Err(123)\n'
            '}\n'
            'fn f() -> Result effects {} {\n'
            '  let v = fail()?\n'
            '  return Ok(v)\n'
            '}\n'
            'print f()\n')

    def test_try_in_return_expression(self):
        self.assert_same(
            'fn get() -> Result effects {} { return Ok(7) }\n'
            'fn f() -> Result effects {} {\n'
            '  return Ok(get()? + 1)\n'
            '}\n'
            'print f()\n')

    # -- unwinding through nested calls ---------------------------------
    def test_nested_calls_unwind_to_function_return(self):
        self.assert_same(
            'fn inner(n: Int) -> Result effects {} {\n'
            '  if n > 0 { return Err("neg") }\n'
            '  return Ok(n)\n'
            '}\n'
            'fn mid(n: Int) -> Result effects {} {\n'
            '  let v = inner(n)?\n'
            '  return Ok(v + 10)\n'
            '}\n'
            'fn outer(n: Int) -> Result effects {} {\n'
            '  let v = mid(n)?\n'
            '  return Ok(v + 100)\n'
            '}\n'
            'print outer(0)\n'
            'print outer(1)\n')

    def test_only_enclosing_function_returns_on_err(self):
        # The caller of a propagating function receives a normal Err value and
        # can keep running; only the function containing ``?`` returns early.
        self.assert_same(
            'fn fail() -> Result effects {} {\n'
            '  return Err("bad")\n'
            '}\n'
            'fn f() -> Result effects {} {\n'
            '  let v = fail()?\n'
            '  return Ok(v)\n'
            '}\n'
            'let r = f()\n'
            'print "after"\n'
            'print r\n')

    # -- loops and branches ---------------------------------------------
    def test_loop_ok_path_accumulates(self):
        self.assert_same(
            'fn add_all(rs: List) -> Result effects {} {\n'
            '  let total = 0\n'
            '  for r in rs {\n'
            '    v := r?\n'
            '    total := total + v\n'
            '  }\n'
            '  return Ok(total)\n'
            '}\n'
            'print add_all([Ok(1), Ok(2), Ok(3)])\n')

    def test_loop_unwinds_on_first_err(self):
        self.assert_same(
            'fn add_all(rs: List) -> Result effects {} {\n'
            '  let total = 0\n'
            '  for r in rs {\n'
            '    v := r?\n'
            '    total := total + v\n'
            '  }\n'
            '  return Ok(total)\n'
            '}\n'
            'print add_all([Ok(1), Err("bad"), Ok(3)])\n')

    def test_while_loop_unwinds_on_err(self):
        self.assert_same(
            'fn f() -> Result effects {} {\n'
            '  let n = 0\n'
            '  while n < 3 {\n'
            '    n := n + 1\n'
            '    if n == 2 { let v = Err("stop")?\n return Ok(v) }\n'
            '  }\n'
            '  return Ok(n)\n'
            '}\n'
            'print f()\n')

    def test_if_branches_propagate(self):
        self.assert_same(
            'fn f(flag: Bool) -> Result effects {} {\n'
            '  if flag {\n'
            '    let v = Ok(5)?\n'
            '    return Ok(v)\n'
            '  } else {\n'
            '    let v = Err("e")?\n'
            '    return Ok(v)\n'
            '  }\n'
            '}\n'
            'print f(true)\n'
            'print f(false)\n')

    def test_match_arms_propagate(self):
        self.assert_same(
            'fn f(r: Result) -> Result effects {} {\n'
            '  match r {\n'
            '    Ok(x) { let v = r?\n return Ok(v + x) }\n'
            '    Err(e) { return Err("handled") }\n'
            '  }\n'
            '}\n'
            'print f(Ok(9))\n'
            'print f(Err("x"))\n')

    # -- allowed and rejected return types -------------------------------
    def test_any_return_type_is_allowed(self):
        # The checker treats a missing return annotation as ``Any`` and permits
        # ``?`` there; the IR must match that behaviour.
        self.assert_same(
            'fn f() {\n'
            '  let v = Err("x")?\n'
            '  return v\n'
            '}\n'
            'print f()\n')

    def test_question_outside_function_is_rejected(self):
        src = 'let x = Ok(1)?\n'
        with self.assertRaises(G.GamaTypeError):
            G.check(src)
        with self.assertRaises(IR.IRUnsupported) as cm:
            IR.compile_program(G.parse(src))
        self.assertIn("? outside function", str(cm.exception))

    def test_question_requires_result_returning_function(self):
        src = (
            'fn f() -> Int effects {} {\n'
            '  let x = Ok(1)?\n'
            '  return x\n'
            '}\n')
        with self.assertRaises(G.GamaTypeError):
            G.check(src)
        with self.assertRaises(IR.IRUnsupported) as cm:
            IR.compile_program(G.parse(src))
        self.assertIn("? requires function returning Result", str(cm.exception))

    # -- unchecked AST runtime guard --------------------------------------
    def test_non_result_operand_raises_language_error(self):
        # Bypass the checker to prove the VM still refuses ``1?`` with a
        # GamaError instead of a Python exception or a wrong value.
        ast = G.parse(
            'fn f() -> Result effects {} {\n'
            '  let x = 1?\n'
            '  return Ok(x)\n'
            '}\n'
            'print f()\n')
        with self.assertRaises(IR.IRRuntimeError) as cm:
            IR.run_program(ast)
        self.assertIsInstance(cm.exception, G.GamaError)
        self.assertIn("? expects Result", str(cm.exception))

    def test_err_propagation_does_not_raise_python_exception(self):
        # A propagating Err must be a normal function return; the VM reports it
        # as printed output rather than bubbling an exception to the caller.
        src = (
            'fn fail() -> Result effects {} {\n'
            '  return Err("boom")\n'
            '}\n'
            'fn f() -> Result effects {} {\n'
            '  let v = fail()?\n'
            '  return Ok(v)\n'
            '}\n'
            'print f()\n')
        result = IR.run_program(G.check(src))
        self.assertEqual(result.out, ['Err(boom)'])


if __name__ == '__main__':
    unittest.main()
