import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class TryPropagationTests(unittest.TestCase):
    def test_question_unwraps_ok_and_propagates_err(self):
        src = '''fn get(ok: Bool) -> Result effects {} {
  if ok { return Ok(42) }
  return Err("nope")
}
fn run_it(ok: Bool) -> Result effects {} {
  v := get(ok)?
  return Ok(v + 1)
}
print run_it(true)
print run_it(false)
'''
        self.assertEqual(G.run(src).out, ['Ok(43)', 'Err(nope)'])

    def test_question_outside_function_is_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let x = Ok(1)?\n')

    def test_question_requires_result(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('fn f() -> Result effects {} {\n  let x = 1?\n  return Ok(x)\n}\n')

    def test_question_requires_result_returning_function(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('fn f() -> Int effects {} {\n  let x = Ok(1)?\n  return x\n}\n')


if __name__ == '__main__':
    unittest.main()
