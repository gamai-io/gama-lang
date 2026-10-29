import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class LoopTests(unittest.TestCase):
    def test_for_list(self):
        self.assertEqual(G.run('for i in [1,2,3] { print i }\n').out, ['1','2','3'])

    def test_for_accumulate(self):
        self.assertEqual(G.run('let s = 0\nfor i in [1,2,3] { s := s + i }\nprint s\n').out, ['6'])

    def test_while(self):
        self.assertEqual(G.run('let i = 0\nwhile i < 3 { print i\n i := i + 1 }\n').out, ['0','1','2'])

    def test_break(self):
        src = 'let i = 0\nwhile i < 5 { i := i + 1\n if i == 3 { break }\n print i }\n'
        self.assertEqual(G.run(src).out, ['1','2'])

    def test_continue(self):
        src = 'let i = 0\nwhile i < 4 { i := i + 1\n if i == 2 { continue }\n print i }\n'
        self.assertEqual(G.run(src).out, ['1','3','4'])

    def test_index_list_and_text(self):
        self.assertEqual(G.run('let xs = [10,20,30]\nprint xs[1]\nprint "abc"[2]\n').out, ['20','c'])

    def test_break_outside_loop_rejected(self):
        with self.assertRaises(G.GamaTypeError):
            G.check('break\n')


if __name__ == '__main__':
    unittest.main()
