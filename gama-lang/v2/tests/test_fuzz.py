"""Deterministic fuzz/property tests for the GAMA-L 2.0 core.

These tests do not use Hypothesis.  A fixed-seed ``random.Random`` generator
produces syntactically valid programs, and a second family of mutations
(deleting/duplicating one structural token) produces malformed sources.

Guarantees exercised here:

* For generated *valid* sources, ``parse``/``check`` may only raise
  ``GamaError``/``GamaTypeError``/``GamaRuntimeError`` (the public error
  family), never a bare Python exception.
* For generated valid sources that pass the checker, ``run`` (under small
  step/depth/loop caps) may only raise those same public errors, never
  ``IndexError``/``KeyError``/``ZeroDivisionError``/``RecursionError``/... .
* Malformed mutations must fail in the parser with ``GamaError``, not crash.

The generator is intentionally small and deterministic: 120 valid programs,
60 deletions and 60 duplications (240 cases), all well under the 10 s budget.
Former core leaks are covered by focused regression tests next to the fuzz
cases instead of being hidden.
"""
import os
import pathlib
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G

# Environment keys that could make a run depend on the host.
_ENV_KEYS = (
    "GAMA_L_MAX_STEPS",
    "GAMA_L_LOOP_LIMIT",
    "GAMA_L_MAX_CALL_DEPTH",
    "GAMA_L_MAX_OUTPUT_CHARS",
    "GAMA_L_DB",
    "GAMA_L_AI_URL",
    "GAMA_L_ASK_URL",
    "GAMA_L_ASK_MODEL",
    "GAMA_L_ASK_KEY",
    "GAMA_L_PROOF_LEDGER",
    "GAMA_L_PROOF_KEY",
    "GAMA_L_PROOF_INTENT",
    "GAMA_L_PROOF_LEASE",
    "GAMA_L_REQUIRE_INTENT_LEASE",
)


def _capped_env():
    """Small deterministic resource caps for every runtime fuzz call."""
    base = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    base.update({
        "GAMA_L_MAX_STEPS": "200",
        "GAMA_L_LOOP_LIMIT": "100",
        "GAMA_L_MAX_CALL_DEPTH": "40",
        "GAMA_L_MAX_OUTPUT_CHARS": "10000",
    })
    return mock.patch.dict(os.environ, base, clear=True)


class _Gen:
    """Seeded generator of small, well-typed GAMA-L programs."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    # ---------------------------------------------------------- expressions
    def int_lit(self):
        return str(self.r.randint(0, 9))

    def int_expr(self, depth=2):
        if depth <= 0 or self.r.random() < 0.45:
            return self.int_lit()
        op = self.r.choice(["+", "-", "*"])
        return "(%s %s %s)" % (
            self.int_expr(depth - 1), op, self.int_expr(depth - 1))

    def num_expr(self, depth=1):
        if depth <= 0 or self.r.random() < 0.5:
            return self.r.choice([self.int_lit(), "0.5", "1.5", "2.25"])
        op = self.r.choice(["+", "-", "*", "/"])
        return "(%s %s %s)" % (
            self.num_expr(depth - 1), op, self.num_expr(depth - 1))

    def bool_simple(self):
        if self.r.random() < 0.35:
            return self.r.choice(["true", "false"])
        op = self.r.choice(["<", "<=", ">", ">=", "==", "!="])
        return "(%s %s %s)" % (self.int_expr(1), op, self.int_expr(1))

    def bool_expr(self):
        if self.r.random() < 0.6:
            return self.bool_simple()
        op = self.r.choice(["&&", "||"])
        return "(%s %s %s)" % (self.bool_simple(), op, self.bool_simple())

    def text_lit(self):
        return self.r.choice(['"high"', '"low"', '"one"', '"other"', '"ok"'])

    def int_list(self):
        return "[" + ", ".join(str(self.r.randint(0, 9))
                               for _ in range(self.r.randint(1, 4))) + "]"

    # ----------------------------------------------------------- templates
    def t_arithmetic(self):
        n = self.r.randint(1, 3)
        lines = ["let a%d = %s" % (i, self.int_expr()) for i in range(n)]
        expr = " + ".join("a%d" % i for i in range(n))
        lines.append("print (%s)" % expr)
        return "\n".join(lines) + "\n"

    def t_while(self):
        limit = self.r.randint(1, 8)
        step = self.r.randint(1, 3)
        return ("let i = 0\n"
                "while (i < %d) {\n"
                "  i := i + %d\n"
                "}\n"
                "print i\n" % (limit, step))

    def t_if(self):
        return ("let x = %s\n"
                "if (x > %d) {\n"
                "  print %s\n"
                "} else {\n"
                "  print %s\n"
                "}\n" % (self.int_expr(), self.r.randint(0, 9),
                          self.text_lit(), self.text_lit()))

    def t_list(self):
        xs = self.int_list()
        idx = self.r.randint(0, 2)
        tail = self.r.choice(["print xs | count",
                              "print xs | limit %d" % self.r.randint(0, 3)])
        return ("let xs = %s\n"
                "let v = xs[%d]\n"
                "print v\n"
                "%s\n" % (xs, idx, tail))

    def t_map(self):
        key = self.r.choice(["a", "b"])
        return ("let m = {a: %s, b: %s}\n"
                "print m.%s\n" % (self.int_expr(), self.int_expr(), key))

    def t_function(self):
        default = self.r.choice([True, False])
        if default:
            sig = "fn add(x: Int, y: Int = %d) -> Int {" % self.r.randint(0, 9)
            call = "print add(%s)\n" % self.int_expr()
        else:
            sig = "fn add(x: Int, y: Int) -> Int {"
            call = "print add(%s, %s)\n" % (self.int_expr(), self.int_expr())
        return ("%s\n"
                "  return x + y\n"
                "}\n"
                "%s" % (sig, call))

    def t_lambda(self):
        return ("let f = fn(x: Int) -> Int {\n"
                "  return x * %d\n"
                "}\n"
                "print f(%s)\n" % (self.r.randint(1, 5), self.int_expr()))

    def t_result_try(self):
        return ("fn f(x: Int) -> Result {\n"
                "  let v = Ok(x)?\n"
                "  return Ok(v)\n"
                "}\n"
                "print f(%s)\n" % self.int_expr())

    def t_result_helpers(self):
        n = self.r.randint(0, 9)
        return ("print is_ok(Ok(%d))\n"
                "print is_err(Err(%d))\n"
                "print unwrap_or(Err(%d), %d)\n" % (n, n, n, n))

    def t_division(self):
        denom = self.r.choice(["0", "1", "2", "3"])
        return ("let a = (%s)\n"
                "let b = (%s)\n"
                "print (a / b)\n" % (self.num_expr(), denom))

    def t_for(self):
        return ("for i in %s {\n"
                "  print i\n"
                "}\n" % self.int_list())

    def t_recursion(self):
        n = self.r.randint(0, 5)
        return ("fn down(n: Int) -> Int {\n"
                "  if (n <= 0) { return 0 }\n"
                "  return down(n - 1)\n"
                "}\n"
                "print down(%d)\n" % n)

    def t_bool_logic(self):
        return ("let t = true\n"
                "print t && %s\n"
                "print t || %s\n" % (self.bool_expr(), self.bool_expr()))

    def t_text(self):
        return ("let s = \"abc\"\n"
                "print upper(s)\n"
                "print contains(s, \"b\")\n"
                "print len(s)\n")

    def t_nested_loop_if(self):
        return ("let i = 0\n"
                "while (i < %d) {\n"
                "  if (i == 1) {\n"
                "    print %s\n"
                "  } else {\n"
                "    print %s\n"
                "  }\n"
                "  i := i + 1\n"
                "}\n" % (self.r.randint(2, 5), self.text_lit(), self.text_lit()))

    def t_match(self):
        n = self.r.randint(0, 9)
        return ("let r = %s(%d)\n"
                "match r {\n"
                "  Ok(v) { print v }\n"
                "  Err(e) { print \"err\" }\n"
                "}\n" % (self.r.choice(["Ok", "Err"]), n))

    def t_prove_verify(self):
        return ("prove p = (%s)\n"
                "verify p\n" % self.int_expr())

    def t_call_lambda_inline(self):
        return ("print (fn(x: Int) -> Int { return x + %d })(%s)\n"
                % (self.r.randint(0, 5), self.int_expr()))

    def t_mixed(self):
        return ("let base = %s\n"
                "let xs = %s\n"
                "let m = {a: base, b: %s}\n"
                "let f = fn(x: Int) -> Int { return x + base }\n"
                "if (base > %d) {\n"
                "  print f(%s)\n"
                "} else {\n"
                "  print xs | count\n"
                "}\n" % (self.int_expr(), self.int_list(), self.int_expr(),
                          self.r.randint(0, 9), self.int_expr()))

    TEMPLATES = (
        t_arithmetic, t_while, t_if, t_list, t_map,
        t_function, t_lambda, t_result_try, t_result_helpers, t_division,
        t_for, t_recursion, t_bool_logic, t_text, t_nested_loop_if,
        t_match, t_prove_verify, t_call_lambda_inline, t_mixed,
    )

    def program(self):
        return self.r.choice(self.TEMPLATES)(self)


def _valid_source(seed):
    return _Gen(seed).program()


# --------------------------------------------------------------- mutations
def _structural_positions(src):
    """Indices of ``()[]{}:`` outside strings and comments, in order."""
    out = []
    i, n, quote = 0, len(src), None
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        elif c in "()[]{}:":
            out.append(i)
        i += 1
    return out


def _delete_first_structural(src):
    positions = _structural_positions(src)
    if not positions:
        raise AssertionError("generated source has no structural token")
    p = positions[0]
    return src[:p] + src[p + 1:]


def _duplicate_last_closer(src):
    closers = [i for i, c in enumerate(src) if c in ")]}"]
    if not closers:
        raise AssertionError("generated source has no closing delimiter")
    p = closers[-1]
    return src[:p + 1] + src[p] + src[p + 1:]


# ----------------------------------------------------------------- helpers
def _parse_only(test, src, label):
    try:
        G.parse(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001 - fuzz harness must catch anything
        test.fail("%s: parse leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
    else:
        test.fail("%s: malformed source parsed without GamaError\n%s"
                  % (label, src))


def _assert_valid_runs(test, src, label):
    try:
        G.parse(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001
        test.fail("%s: parse leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return
    try:
        G.check(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001
        test.fail("%s: check leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return
    try:
        with _capped_env():
            it = G.run(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001 - the actual invariant under test
        test.fail("%s: run leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return
    test.assertIsInstance(it.out, list)


# ------------------------------------------------------------ case tables
_N_VALID = 120
_N_MUTATED = 60

_VALID = [_valid_source(seed) for seed in range(_N_VALID)]
_MALFORMED_DELETE = [_delete_first_structural(_valid_source(seed))
                     for seed in range(_N_MUTATED)]
_MALFORMED_DUPLICATE = [_duplicate_last_closer(_valid_source(seed))
                        for seed in range(_N_MUTATED)]
_TOTAL_CASES = len(_VALID) + len(_MALFORMED_DELETE) + len(_MALFORMED_DUPLICATE)


# ----------------------------------------------------------- test classes
class FuzzMetaTests(unittest.TestCase):
    def test_generator_is_deterministic(self):
        for seed in range(_N_VALID):
            self.assertEqual(_valid_source(seed), _valid_source(seed))

    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_generated_valid_programs_pass_check(self):
        for idx, src in enumerate(_VALID):
            with self.subTest(case=idx):
                try:
                    G.check(src)
                except G.GamaError as ex:
                    self.fail("case %d failed check: %s\n%s" % (idx, ex, src))

    def test_mutations_are_structurally_malformed(self):
        for idx, src in enumerate(_MALFORMED_DELETE):
            self.assertNotEqual(src, _valid_source(idx))
        for idx, src in enumerate(_MALFORMED_DUPLICATE):
            self.assertNotEqual(src, _valid_source(idx))


class GeneratedFuzzTests(unittest.TestCase):
    """Dynamically populated: 240 deterministic fuzz cases."""


def _make_valid_case(src, idx):
    def test(self):
        _assert_valid_runs(self, src, "valid case %d" % idx)
    test.__doc__ = "Generated valid program #%d" % idx
    return test


def _make_delete_case(src, idx):
    def test(self):
        _parse_only(self, src, "delete case %d" % idx)
    test.__doc__ = "Deleted structural token, program #%d" % idx
    return test


def _make_duplicate_case(src, idx):
    def test(self):
        _parse_only(self, src, "duplicate case %d" % idx)
    test.__doc__ = "Duplicated closing token, program #%d" % idx
    return test


for _i, _src in enumerate(_VALID):
    setattr(GeneratedFuzzTests, "test_valid_%03d" % _i,
            _make_valid_case(_src, _i))

for _i, _src in enumerate(_MALFORMED_DELETE):
    setattr(GeneratedFuzzTests, "test_malformed_delete_%03d" % _i,
            _make_delete_case(_src, _i))

for _i, _src in enumerate(_MALFORMED_DUPLICATE):
    setattr(GeneratedFuzzTests, "test_malformed_duplicate_%03d" % _i,
            _make_duplicate_case(_src, _i))


class KnownCoreLeakRegressionTests(unittest.TestCase):
    """Regression pins for confirmed core leaks that are now closed.

    Each test asserts the *contract*: a bad call must surface as a
    ``GamaError`` subclass, and an ``else if`` chain must run and check both
    branches.  These were previously pinned with ``expectedFailure`` while the
    core was left untouched.
    """

    def test_zero_arity_builtin_raises_gama_error(self):
        # len()/text()/number()/round() used to index e.args[0] directly and
        # leak IndexError; the static arity guard now raises GamaTypeError.
        for src in ("print len()\n", "print text()\n",
                    "print number()\n", "print round()\n"):
            with self.subTest(src=src):
                with self.assertRaises(G.GamaError):
                    G.run(src)

    def test_len_non_sized_raises_gama_error(self):
        # len(5) / len(null) used to pass the checker and leak TypeError.
        for src in ("print len(5)\n", "print len(null)\n"):
            with self.subTest(src=src):
                with self.assertRaises(G.GamaError):
                    G.run(src)

    def test_number_non_numeric_raises_gama_error(self):
        # number("abc") / number(null) used to leak decimal.InvalidOperation.
        for src in ('print number("abc")\n', "print number(null)\n"):
            with self.subTest(src=src):
                with self.assertRaises(G.GamaError):
                    G.run(src)

    def test_round_non_numeric_raises_gama_error(self):
        # round("x") used to leak decimal.InvalidOperation (ArithmeticError).
        with mock.patch.dict(os.environ, {"GAMA_L_MAX_STEPS": "50"}):
            with self.assertRaises(G.GamaError):
                G.run('print round("x")\n')

    def test_else_if_chain_runs_and_prints_b(self):
        # The parser represents ``else if`` as an ``If`` node in ``If.other``;
        # Checker.stmt/Interpreter.stmt used to iterate it directly, leaking
        # ``TypeError: 'If' object is not iterable``.
        src = ('let x = 7\n'
               'if (x > 8) {\n  print "a"\n'
               '} else if (x > 2) {\n  print "b"\n'
               '} else {\n  print "c"\n}\n')
        it = G.run(src)
        self.assertEqual(it.out, ["b"])

    def test_else_if_chain_checks_nested_branch(self):
        # Both the nested else-if branch and its final else must be checked.
        src = ('if (true) {\n  print "a"\n'
               '} else if (true) {\n  print 1 + "x"\n'
               '} else {\n  print "c"\n}\n')
        with self.assertRaises(G.GamaTypeError):
            G.check(src)


if __name__ == "__main__":
    unittest.main()
