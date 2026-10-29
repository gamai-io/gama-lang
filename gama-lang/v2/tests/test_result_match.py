import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class ResultMatchTests(unittest.TestCase):
    def test_ok_err_values(self):
        self.assertEqual(G.run('print Ok(5)\nprint Err("bad")\n').out, ['Ok(5)', 'Err(bad)'])

    def test_match_ok(self):
        self.assertEqual(G.run('match Ok(5) { Ok(v) { print v } Err(e) { print e } }\n').out, ['5'])

    def test_match_err(self):
        self.assertEqual(G.run('match Err("bad") { Ok(v) { print v } Err(e) { print e } }\n').out, ['bad'])

    def test_match_wildcard(self):
        self.assertEqual(G.run('match Err("x") { Ok(v) { print v } _ { print "fallback" } }\n').out, ['fallback'])

    def test_function_returns_result(self):
        src = '''fn f(x: Int) -> Result effects {} {
  if x < 0 { return Err("neg") }
  return Ok(x)
}
print f(2)
print f(-1)
'''
        self.assertEqual(G.run(src).out, ['Ok(2)', 'Err(neg)'])

    def test_match_requires_result(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('match 1 { _ { print 1 } }\n')


if __name__ == '__main__':
    unittest.main()
