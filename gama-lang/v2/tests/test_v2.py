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
        self.assertEqual(it.out, ['Denied'])
    def test_if(self):
        it = G.run('let x = 5\nif x > 3 {\n print "big"\n} else {\n print "small"\n}\n')
        self.assertEqual(it.out, ['big'])
    def test_import(self):
        it = G.run_file(str(pathlib.Path(__file__).resolve().parents[1] / 'examples' / 'with_import.gm'))
        self.assertEqual(it.out[0], '42')
        self.assertTrue(it.env.get('_verify')['ok'])
    def test_sqlite_read(self):
        import os, sqlite3, tempfile
        d = tempfile.mkdtemp(); p = os.path.join(d, 'demo.db')
        con = sqlite3.connect(p)
        con.execute('create table t (a int, b text)')
        con.execute("insert into t values (1, 'x')")
        con.commit(); con.close()
        old = os.environ.get('GAMA_L_DB'); os.environ['GAMA_L_DB'] = p
        try:
            it = G.run('let r = sql("select a,b from t")\nprint r | count\n')
            self.assertEqual(it.out, ['1'])
        finally:
            if old is None: os.environ.pop('GAMA_L_DB', None)
            else: os.environ['GAMA_L_DB'] = old
    def test_newline_literals(self):
        it = G.run('let m = {\n a: 1,\n b: 2\n}\nprint m.a\n')
        self.assertEqual(it.out, ['1'])
    def test_assign_declares(self):
        it = G.run('x := 5\nprint x\n')
        self.assertEqual(it.out, ['5'])
    def test_else_newline(self):
        it = G.run('if true {\n print "a"\n}\nelse {\n print "b"\n}\n')
        self.assertEqual(it.out, ['a'])
    def test_block_scope(self):
        it = G.run('if true {\n let a = 1\n print a\n}\n')
        self.assertEqual(it.out, ['1'])
    def test_dotted_proof(self):
        it = G.run('prove inv.posted = { a: 1 }\nprint inv.posted.a\n')
        self.assertEqual(it.out, ['1'])
    def test_sum_type(self):
        it = G.run('let r = sql("x")\nlet s = r | sum neto\nprint s + 1\n')
        self.assertEqual(it.out, ['351.00'])
    def test_division_decimal(self):
        it = G.run('print 1/3\n')
        self.assertEqual(it.out[0][:5], '0.333')
    def test_return_type(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('fn f() -> Int effects {} { return "x" }\n')
    def test_statement_terminator(self):
        with self.assertRaises(G.GamaError):
            G.check('print 1 2\n')
    def test_duplicate_prove(self):
        with self.assertRaises(G.GamaRuntimeError):
            G.run('prove p = {a:1}\nprove p = {a:2}\n')
    def test_import_diamond(self):
        import tempfile
        d = tempfile.mkdtemp(); pathlib.Path(d,'d.gm').write_text('fn d() -> Int effects {} { return 1 }\n')
        pathlib.Path(d,'b.gm').write_text('import "d.gm"\nfn b() -> Int effects {} { return d()+1 }\n')
        pathlib.Path(d,'c.gm').write_text('import "d.gm"\nfn c() -> Int effects {} { return d()+2 }\n')
        pathlib.Path(d,'a.gm').write_text('import "b.gm"\nimport "c.gm"\nprint b()+c()\n')
        it = G.run_file(str(pathlib.Path(d,'a.gm')))
        self.assertEqual(it.out, ['5'])
    def test_ask_offline(self):
        it = G.run('print ask("hi")\n')
        self.assertEqual(it.out, ['Unavailable'])
    def test_effect_write_stub(self):
        with self.assertRaises(G.GamaEffectError):
            G.check('fn f() -> Bool effects {} { return sql_write("x") }\n')
    def test_unknown_column_select(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let r = sql("x")\nlet s = r | select kontoX\n')
    def test_unknown_column_filter(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let r = sql("x")\nlet s = r | filter mjsta == "Sarajevo"\n')
    def test_unknown_column_sum(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('let r = sql("x")\nlet s = r | sum netoX\n')
    def test_proof_signature(self):
        import os
        old = os.environ.get('GAMA_L_PROOF_KEY'); os.environ['GAMA_L_PROOF_KEY'] = 'test-key'
        try:
            it = G.run('prove p = {a:1}\nverify p\n')
            self.assertTrue(it.env.get('_verify')['ok'])
            rec = it.proofs['p']; rec['value']['a'] = 2
            self.assertFalse(it.verify(rec)['ok'])
        finally:
            if old is None: os.environ.pop('GAMA_L_PROOF_KEY', None)
            else: os.environ['GAMA_L_PROOF_KEY'] = old
    def test_fmt_idempotent(self):
        a = G.fmt_source('print 1   \n\n')
        self.assertEqual(G.fmt_source(a), a)
    def test_corpus(self):
        cases = {
            'print 1+2*3\n': '7', 'print (1+2)*3\n': '9', 'print 17%5\n': '2',
            'print 10/4\n': '2.5', 'print 1 == 1\n': 'True', 'print 1 < 2\n': 'True',
            'print true && false\n': 'False', 'print !true\n': 'False',
            'let r = sql("x")\nprint r | count\n': '2',
            'let r = sql("x")\nprint r | select konto | count\n': '2',
            'let r = sql("x")\nprint r | filter mjesto == "Sarajevo" | count\n': '1',
            'let r = sql("x")\nprint r | sum neto\n': '350.00',
            'let r = sql("x")\nprint r | map naziv | count\n': '2',
            'print "a" + "b"\n': 'ab', 'print 5 - 2\n': '3', 'print 2 * 6\n': '12',
            'print null == null\n': 'True', 'print 1 != 2\n': 'True', 'print 10 >= 10\n': 'True',
            'print 1 < 2 && 2 < 3\n': 'True',
        }
        for src, exp in cases.items():
            self.assertEqual(G.run(src).out[-1], exp, src)
    def test_examples_run(self):
        import pathlib
        for p in sorted((pathlib.Path(__file__).resolve().parents[1] / 'examples').glob('*.gm')):
            if p.name == 'lib.gm':
                continue
            G.run_file(str(p))
    def test_function(self):
        it = G.run('fn add(a: Int, b: Int) -> Int effects {} {\n return a+b\n}\nprint add(2,3)\n')
        self.assertEqual(it.out, ['5'])

if __name__ == '__main__': unittest.main()
