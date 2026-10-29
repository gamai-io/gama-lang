import sys, unittest, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G

class V2Tests(unittest.TestCase):
    def test_arithmetic(self):
        it = G.run('print 6*7\nprint 117.00 + 17.00\n')
        self.assertEqual(it.out, ['42', '134.00'])
    def test_invoice_pipe(self):
        it = G.run(open(pathlib.Path(__file__).resolve().parents[1] / 'examples' / 'invoice.gm', encoding='utf-8').read())
        self.assertEqual(it.out[0], '1')
        self.assertEqual(it.proofs['invoice']['value']['count'], 1)
    def test_filter_select(self):
        it = G.run('let r = sql("x")\nlet s = r | filter mjesto == "Sarajevo" | select konto, naziv\nprint s | count\n')
        self.assertEqual(it.out, ['1'])
    def test_type_error(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let x: Int = "text"\n')
    def test_effect_violation(self):
        with self.assertRaises(G.GamaEffectError):
            G.check('fn f() -> Int effects {} {\n  return len(sql("x"))\n}\nprint f()\n')
    def test_prove_verify(self):
        it = G.run('prove p = { a: 1, b: "x" }\nverify p\n')
        self.assertEqual(it.out[0][:1], '{')
        self.assertTrue(it.env.get('_verify')['ok'])
    def test_tamper(self):
        it = G.run('prove p = { a: 1 }\nlet q = { name: "p", value: { a: 2 }, sha256: p.sha256, prev: p.prev }\nverify q\n')
        self.assertFalse(it.env.get('_verify')['ok'])
    def test_memory(self):
        it = G.run('remember("k", "v")\nprint recall("k")\n')
        self.assertEqual(it.out, ['v'])
    def test_hitl_noninteractive(self):
        it = G.run('print wait_user("ok?")\n')
        self.assertEqual(it.out, ['False'])
    def test_if(self):
        it = G.run('let x = 5\nif x > 3 {\n print "big"\n} else {\n print "small"\n}\n')
        self.assertEqual(it.out, ['big'])
    def test_function(self):
        it = G.run('fn add(a: Int, b: Int) -> Int effects {} {\n return a+b\n}\nprint add(2,3)\n')
        self.assertEqual(it.out, ['5'])

if __name__ == '__main__': unittest.main()
