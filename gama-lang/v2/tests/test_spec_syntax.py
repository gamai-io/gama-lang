import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G

SPEC = '''customers := sql("SELECT konto, naziv, mjesto FROM kupdob")
active    := customers | filter mjesto == "Sarajevo" | select konto, naziv

summary := ask "Summarize these customers" with active

remember customer.default = "ALFA"
name := recall customer.default

ok := wait user "Post this invoice?"

prove invoice.posted {
  invoice: "MOCK-2026-001",
  total: 75.47,
  erp_id: "MOCK-2026-001"
}

use sql
use proof

make total(net: Decimal, rate: Decimal = 0.17) -> Decimal effects {} {
  return round(net * (1 + rate), 2)
}
print total(100)
'''


class SpecSyntaxTests(unittest.TestCase):
    def test_spec_example_checks(self):
        G.check(SPEC)

    def test_spec_example_runs(self):
        it = G.run(SPEC)
        self.assertIn("117.00", it.out)

    def test_make_default_and_explicit(self):
        src = '''make f(a: Int, b: Int = 2) -> Int effects {} { return a + b }
print f(1)
print f(1, 5)
'''
        self.assertEqual(G.run(src).out, ["3", "6"])

    def test_remember_recall_dotted(self):
        src = 'remember customer.default = "ALFA"\nprint recall customer.default\n'
        self.assertEqual(G.run(src).out, ["ALFA"])

    def test_wait_user_denied(self):
        self.assertEqual(G.run('print wait user "ok?"\n').out, ["Denied"])

    def test_use_is_noop(self):
        G.check("use sql\nuse proof\nprint 1\n")


if __name__ == "__main__":
    unittest.main()
