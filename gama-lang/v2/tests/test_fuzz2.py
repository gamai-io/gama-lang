"""Deterministic fuzz round 2 for GAMA-L 2.0 (features added since round 1).

Round 1 (``test_fuzz.py``) covered the original core.  This round targets the
newer surface: ``else if`` chains, list/map literals and indexing, the pure
numeric/text/collection builtins, namespaced imports, the expression/step
limits, and intent/lease proof environment combinations.

Guarantees exercised here:

* For generated *valid* sources, ``parse``/``check``/``run`` may only raise the
  public ``GamaError`` family, never a bare Python exception.  Under small
  deterministic step/depth/loop/output/expression caps, ``run`` must not leak
  ``IndexError``/``KeyError``/``TypeError``/``decimal.InvalidOperation``/
  ``RecursionError`` or any other Python exception.
* Malformed mutations must fail in the parser with ``GamaError``.
* Namespaced import failures (unsupported top level, duplicate/invalid alias,
  missing module, cycles, path escapes, missing exports) must be ``GamaError``.
* Malformed/missing limit env values must never leak Python exceptions.
* Intent/lease proof env combinations must only succeed or raise ``GamaError``.

The generator is deterministic: 140 valid programs, 45 structural deletions and
45 duplicated closers (230 cases), plus targeted import/limit/proof matrices.

The core leaks found by this round were fixed and the former
``expectedFailure`` pins are now plain regression tests at the bottom of the
file, so they fail loudly if any leak returns.
"""
import os
import pathlib
import random
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


def _env(**kw):
    """Clean GAMA-L environment with deterministic overrides."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("GAMA_L_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


_CAPPED = {
    "GAMA_L_MAX_STEPS": "200",
    "GAMA_L_LOOP_LIMIT": "100",
    "GAMA_L_MAX_CALL_DEPTH": "40",
    "GAMA_L_MAX_OUTPUT_CHARS": "10000",
    "GAMA_L_MAX_EXPR_STEPS": "10000",
}


def _capped_env(**kw):
    base = dict(_CAPPED)
    base.update(kw)
    return _env(**base)


class _Gen2:
    """Seeded generator of small, well-typed programs for the new features."""

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

    def bool_expr(self):
        if self.r.random() < 0.35:
            return self.r.choice(["true", "false"])
        op = self.r.choice(["<", "<=", ">", ">=", "==", "!="])
        return "(%s %s %s)" % (self.int_expr(1), op, self.int_expr(1))

    def text_lit(self):
        return self.r.choice(['"high"', '"low"', '"one"', '"other"', '"ok"'])

    def int_values(self, n=None):
        n = n or self.r.randint(1, 5)
        return [self.r.randint(0, 9) for _ in range(n)]

    def int_list(self, values=None):
        values = self.int_values() if values is None else values
        return "[" + ", ".join(str(v) for v in values) + "]"

    # ---------------------------------------------------- extra expressions
    def t_else_if2(self):
        x = self.r.randint(0, 12)
        a = self.r.randint(2, 10)
        b = self.r.randint(0, a - 1)
        return ("let x = %d\n"
                "if (x > %d) {\n  print %s\n"
                "} else if (x > %d) {\n  print %s\n"
                "} else {\n  print %s\n}\n"
                % (x, a, self.text_lit(), b, self.text_lit(), self.text_lit()))

    def t_else_if3(self):
        x = self.r.randint(0, 12)
        return ("let x = %d\n"
                "if (x > 10) {\n  print \"a\"\n"
                "} else if (x > 8) {\n  let y = x + 1\n  print y\n"
                "} else if (x > 2) {\n  print \"c\"\n"
                "} else {\n  print \"d\"\n}\n" % x)

    def t_else_if_no_else(self):
        x = self.r.randint(0, 9)
        t = self.r.randint(1, 8)
        return ("let x = %d\n"
                "if (x > %d) {\n  print \"a\"\n"
                "} else if (x > 0) {\n  print \"b\"\n}\n" % (x, t))

    def t_list_index(self):
        values = self.int_values()
        idx = self.r.randrange(len(values))
        return ("let xs = %s\n"
                "let i = %d\n"
                "print xs[i]\n"
                "print xs[%d]\n"
                "print len(xs)\n"
                "print reverse(xs)\n"
                "print sort(xs)\n"
                "print index_of(xs, %d)\n"
                % (self.int_list(values), idx, idx, values[0]))

    def t_list_nested(self):
        inner = self.int_values()
        return ("let xs = [%s, [%d, %d]]\n"
                "print xs[0][%d]\n"
                "print len(xs[1])\n"
                "print reverse(xs[0])\n"
                % (self.int_list(inner).strip("[]"),
                   self.r.randint(0, 9), self.r.randint(0, 9),
                   self.r.randrange(len(inner))))

    def t_map_literal(self):
        return ("let m = {a: %d, b: %d}\n"
                "print m.a\n"
                "print m.b\n"
                "print keys(m)\n"
                "print values(m)\n"
                "print has(m, \"a\")\n"
                % (self.r.randint(0, 9), self.r.randint(0, 9)))

    def t_map_nested(self):
        return ("let m = {a: {b: %d}, c: %d}\n"
                "print m.a.b\n"
                "print m.c\n"
                % (self.r.randint(0, 9), self.r.randint(0, 9)))

    def t_abs(self):
        return ("let a = abs(-%d)\n"
                "print a\n"
                "let b = abs(number(\"2.5\"))\n"
                "print b\n" % self.r.randint(0, 9))

    def t_minmax(self):
        vals = [self.r.randint(-9, 9) for _ in range(self.r.randint(2, 4))]
        return ("print min(%s)\n"
                "print max(%s)\n"
                "print min(number(\"1.5\"), 2)\n"
                % (", ".join(map(str, vals)), ", ".join(map(str, vals))))

    def t_floor_ceil(self):
        return ("print floor(number(\"2.9\"))\n"
                "print ceil(number(\"2.1\"))\n"
                "print floor(%d)\n"
                "print ceil(-%d)\n"
                % (self.r.randint(0, 9), self.r.randint(0, 9)))

    def t_reverse(self):
        return ("print reverse(\"abcdef\")\n"
                "print reverse(%s)\n"
                "let s = \"hello\"\n"
                "print reverse(s)\n" % self.int_list())

    def t_slice(self):
        values = self.int_values()
        end = self.r.randint(0, len(values))
        start = self.r.randint(0, end)
        text = "abcdef"
        tend = self.r.randint(0, len(text))
        tstart = self.r.randint(0, tend)
        return ("let xs = %s\n"
                "print slice(xs, %d, %d)\n"
                "print slice(\"%s\", %d, %d)\n"
                % (self.int_list(values), start, end, text, tstart, tend))

    def t_index_of(self):
        values = self.int_values()
        needle = self.r.choice(values + [99])
        return ("print index_of(\"abcdef\", \"cd\")\n"
                "print index_of(%s, %d)\n"
                "print index_of(%s, 99)\n"
                % (self.int_list(values), needle, self.int_list(values)))

    def t_sort(self):
        return ("print sort(%s)\n"
                "print sort([\"b\", \"a\", \"c\"])\n"
                "print sort([])\n" % self.int_list())

    def t_to_int(self):
        return ("print to_int(%d)\n"
                "print to_int(number(\"%d\"))\n"
                "print to_int(\"42\")\n"
                "print to_int(\"-7\")\n"
                % (self.r.randint(0, 9), self.r.randint(0, 9)))

    def t_mixed_new_features(self):
        x = self.r.randint(0, 9)
        values = self.int_values()
        return ("let xs = %s\n"
                "let m = {a: %d, b: %d}\n"
                "let x = %d\n"
                "if (x > 6) {\n  print sort(xs)\n"
                "} else if (x > 3) {\n  print slice(xs, 0, 2)\n"
                "} else {\n  print m.a\n}\n"
                "print min(m.a, m.b)\n"
                "print reverse(\"ok\")\n"
                % (self.int_list(values), self.r.randint(0, 9),
                   self.r.randint(0, 9), x))

    TEMPLATES = (
        t_else_if2, t_else_if3, t_else_if_no_else, t_list_index,
        t_list_nested, t_map_literal, t_map_nested, t_abs, t_minmax,
        t_floor_ceil, t_reverse, t_slice, t_index_of, t_sort, t_to_int,
        t_mixed_new_features,
    )

    def program(self):
        return self.r.choice(self.TEMPLATES)(self)


def _valid_source(seed):
    return _Gen2(seed).program()


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
_N_VALID = 140
_N_MUTATED = 45

_VALID = [_valid_source(seed) for seed in range(_N_VALID)]
_MALFORMED_DELETE = [_delete_first_structural(_valid_source(seed))
                     for seed in range(1000, 1000 + _N_MUTATED)]
_MALFORMED_DUPLICATE = [_duplicate_last_closer(_valid_source(seed))
                        for seed in range(2000, 2000 + _N_MUTATED)]
_TOTAL_CASES = len(_VALID) + len(_MALFORMED_DELETE) + len(_MALFORMED_DUPLICATE)


# ----------------------------------------------------------- test classes
class Fuzz2MetaTests(unittest.TestCase):
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
            self.assertNotEqual(src, _valid_source(1000 + idx))
        for idx, src in enumerate(_MALFORMED_DUPLICATE):
            self.assertNotEqual(src, _valid_source(2000 + idx))


class GeneratedFuzz2Tests(unittest.TestCase):
    """Dynamically populated: 230 deterministic fuzz cases."""


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
    setattr(GeneratedFuzz2Tests, "test_valid_%03d" % _i,
            _make_valid_case(_src, _i))

for _i, _src in enumerate(_MALFORMED_DELETE):
    setattr(GeneratedFuzz2Tests, "test_malformed_delete_%03d" % _i,
            _make_delete_case(_src, _i))

for _i, _src in enumerate(_MALFORMED_DUPLICATE):
    setattr(GeneratedFuzz2Tests, "test_malformed_duplicate_%03d" % _i,
            _make_duplicate_case(_src, _i))


class FeatureSemanticsTests(unittest.TestCase):
    """A few deterministic checks for the new feature families."""

    def test_else_if_chain_selects_expected_branch(self):
        for x, want in ((11, "a"), (9, "b"), (5, "c"), (1, "d")):
            src = ("let x = %d\n"
                   "if (x > 10) {\n  print \"a\"\n"
                   "} else if (x > 8) {\n  print \"b\"\n"
                   "} else if (x > 2) {\n  print \"c\"\n"
                   "} else {\n  print \"d\"\n}\n" % x)
            with self.subTest(x=x):
                self.assertEqual(G.run(src).out, [want])

    def test_list_and_map_indexing(self):
        src = ("let xs = [4, 5, 6]\n"
               "let m = {a: {b: 7}}\n"
               "print xs[1]\n"
               "print m.a.b\n")
        self.assertEqual(G.run(src).out, ["5", "7"])

    def test_new_builtins_expected_values(self):
        src = ('print abs(-3)\n'
               'print min(4, 2, 9)\n'
               'print max(4, 2, 9)\n'
               'print floor(number("2.9"))\n'
               'print ceil(number("2.1"))\n'
               'print reverse([1, 2, 3])\n'
               'print slice("abcdef", 1, 4)\n'
               'print index_of("abcdef", "cd")\n'
               'print sort([3, 1, 2])\n'
               'print to_int("42")\n')
        self.assertEqual(
            G.run(src).out,
            ["3", "2", "9", "2", "3", "[3, 2, 1]", "bcd", "2", "[1, 2, 3]", "42"],
        )


class NamespacedImportFuzzTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def _run_main(self, main, modules, env=None):
        for name, text in modules.items():
            self._write(name, text)
        mainp = self._write("main.gm", main)
        with _env(**(env or _CAPPED)):
            return G.run_file(str(mainp))

    def test_generated_namespaced_imports(self):
        for seed in range(24):
            r = random.Random(seed)
            fn = "f%d" % r.randint(0, 9)
            op = r.choice(["+", "-", "*"])
            a = r.randint(0, 20)
            b = r.randint(0, 20)
            module = ("fn %s(a: Int, b: Int) -> Int effects {} "
                      "{ return a %s b }\n" % (fn, op))
            main = ('import "lib.gm" as ns\n'
                    "print ns.%s(%d, %d)\n" % (fn, a, b))
            with self.subTest(seed=seed):
                it = self._run_main(main, {"lib.gm": module})
                self.assertEqual(it.out, [str(eval("%d %s %d" % (a, op, b)))])

    def test_generated_module_with_helper_import_and_default(self):
        for seed in range(12):
            r = random.Random(100 + seed)
            base = r.randint(0, 20)
            module = ('import "helper.gm"\n'
                      "fn add(a: Int, b: Int = %d) -> Int effects {} "
                      "{ return twice(a) + b }\n" % r.randint(0, 9))
            helper = ("fn twice(x: Int) -> Int effects {} { return x * 2 }\n")
            main = ('import "lib.gm" as ns\n'
                    "print ns.add(%d)\n" % base)
            with self.subTest(seed=seed):
                it = self._run_main(
                    main, {"lib.gm": module, "helper.gm": helper})
                self.assertIsInstance(it.out, list)

    def test_malformed_namespaced_imports_raise_gama_error(self):
        lib = 'fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n'
        cases = [
            ("top-level let",
             {"lib.gm": 'let hidden = 7\n'},
             'import "lib.gm" as math\nprint math.add(1, 2)\n'),
            ("top-level print",
             {"lib.gm": 'print "side"\n'},
             'import "lib.gm" as math\nprint 1\n'),
            ("top-level prove",
             {"lib.gm": 'prove p = 1 + 1\n'},
             'import "lib.gm" as math\nprint 1\n'),
            ("top-level verify",
             {"lib.gm": 'verify true\n'},
             'import "lib.gm" as math\nprint 1\n'),
            ("duplicate alias",
             {"lib.gm": lib},
             'import "lib.gm" as math\n'
             'import "lib.gm" as math\nprint math.add(1, 2)\n'),
            ("invalid alias",
             {"lib.gm": lib},
             'import "lib.gm" as a.b\nprint a.b.add(1, 2)\n'),
            ("alias collides with fn",
             {"lib.gm": lib},
             'fn math() -> Int effects {} { return 1 }\n'
             'import "lib.gm" as math\nprint math.add(1, 2)\n'),
            ("missing export",
             {"lib.gm": lib},
             'import "lib.gm" as math\nprint math.nope()\n'),
            ("missing module",
             {},
             'import "nope.gm" as math\nprint 1\n'),
        ]
        for label, modules, main in cases:
            with self.subTest(label=label):
                with self.assertRaises(G.GamaError):
                    self._run_main(main, modules)

    def test_import_cycle_raises_gama_error(self):
        main = 'import "a.gm" as ns\nprint 1\n'
        modules = {
            "a.gm": 'import "b.gm" as b\n'
                    "fn one() -> Int effects {} { return 1 }\n",
            "b.gm": 'import "a.gm" as a\n'
                    "fn two() -> Int effects {} { return 2 }\n",
        }
        with self.assertRaises(G.GamaError):
            self._run_main(main, modules)

    def test_path_outside_project_root_raises_gama_error(self):
        self._write("outside.gm",
                    'fn leak() -> Int effects {} { return 1 }\n')
        app = self.root / "app"
        app.mkdir()
        mainp = app / "main.gm"
        mainp.write_text(
            'import "../outside.gm" as x\nprint x.leak()\n', encoding="utf-8")
        with _env(**_CAPPED):
            with self.assertRaises(G.GamaError):
                G.run_file(str(mainp))


class LimitEnvFuzzTests(unittest.TestCase):
    """Limit env values are fuzzed; only success or GamaError is allowed."""

    _LOOP50 = ("let i = 0\n"
               "while (i < 50) {\n  i := i + 1\n}\n"
               "print i\n")
    _NESTED = "print %s\n" % " + ".join(str(i) for i in range(1, 15))
    _REC20 = ("fn down(n: Int) -> Int {\n"
              "  if (n <= 0) { return 0 }\n"
              "  return down(n - 1)\n}\n"
              "print down(20)\n")
    _BIGOUT = 'print "%s"\n' % ("x" * 200)

    def _cases(self):
        values = ("", "abc", "-1", "0", "1", "5", "1000000")
        programs = {
            "GAMA_L_MAX_STEPS": self._LOOP50,
            "GAMA_L_MAX_EXPR_STEPS": self._NESTED,
            # Malformed GAMA_L_LOOP_LIMIT is a confirmed core leak pinned by
            # KnownCoreLeakRegressionTests; the invariant matrix only probes
            # values the core already parses.
            "GAMA_L_LOOP_LIMIT": (self._LOOP50,
                                  ("-1", "0", "1", "5", "1000000")),
            "GAMA_L_MAX_CALL_DEPTH": self._REC20,
            "GAMA_L_MAX_OUTPUT_CHARS": self._BIGOUT,
        }
        out = []
        for key, spec in programs.items():
            if isinstance(spec, tuple):
                src, key_values = spec
            else:
                src, key_values = spec, values
            for value in key_values:
                env = dict(_CAPPED)
                env[key] = value
                out.append(("%s=%r" % (key, value), src, env))
        return out

    def test_limit_env_matrix_never_leaks(self):
        cases = self._cases()
        self.assertGreaterEqual(len(cases), 30)
        for label, src, env in cases:
            with self.subTest(label=label):
                try:
                    with _env(**env):
                        it = G.run(src)
                except G.GamaError:
                    continue
                except Exception as ex:  # noqa: BLE001
                    self.fail("%s leaked %s: %s"
                              % (label, type(ex).__name__, ex))
                self.assertIsInstance(it.out, list)


class IntentLeaseProofFuzzTests(unittest.TestCase):
    """Intent/lease env combinations must only succeed or raise GamaError."""

    _SRC = ('prove p = { a: 1 }\n'
            'verify p\n')

    def test_intent_lease_env_matrix_never_leaks(self):
        combos = 0
        for intent in (None, "", "intent-A", "intent-B"):
            for lease in (None, "", "lease-7"):
                for require in (None, "0", "1"):
                    for key in (None, "", "s3cret"):
                        combos += 1
                        label = "intent=%r lease=%r require=%r key=%r" % (
                            intent, lease, require, key)
                        with self.subTest(label=label):
                            with tempfile.TemporaryDirectory() as td:
                                ledger = pathlib.Path(td) / "proofs.jsonl"
                                env = dict(_CAPPED)
                                env["GAMA_L_PROOF_LEDGER"] = str(ledger)
                                if intent is not None:
                                    env["GAMA_L_PROOF_INTENT"] = intent
                                if lease is not None:
                                    env["GAMA_L_PROOF_LEASE"] = lease
                                if require is not None:
                                    env["GAMA_L_REQUIRE_INTENT_LEASE"] = require
                                if key is not None:
                                    env["GAMA_L_PROOF_KEY"] = key
                                try:
                                    with _env(**env):
                                        it = G.run(self._SRC)
                                except G.GamaError:
                                    continue
                                except Exception as ex:  # noqa: BLE001
                                    self.fail("%s leaked %s: %s"
                                              % (label, type(ex).__name__, ex))
                                self.assertIsInstance(it.out, list)
                                self.assertIn("p", it.proofs)
        self.assertGreaterEqual(combos, 20)

    def test_require_mode_without_env_marks_verify_not_ok(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            env = dict(_CAPPED)
            env["GAMA_L_PROOF_LEDGER"] = str(ledger)
            env["GAMA_L_REQUIRE_INTENT_LEASE"] = "1"
            with _env(**env):
                it = G.run(self._SRC)
            self.assertFalse(it.env.v["_verify"]["ok"])
            self.assertIn("intent_not_configured",
                          it.env.v["_verify"]["errors"])
            self.assertIn("lease_not_configured",
                          it.env.v["_verify"]["errors"])


def _has_int_str_limit():
    return hasattr(sys, "get_int_max_str_digits")


class KnownCoreLeakRegressionTests(unittest.TestCase):
    """Regression tests for the round-2 core leaks.

    Each test asserts the contract: a bad value must surface as ``GamaError``.
    The leaks are fixed, so these run as plain regression tests; a regression
    fails here instead of crashing with a bare Python exception.
    """

    def test_number_non_finite_should_raise_gama_error(self):
        for text in ("NaN", "Infinity", "-Infinity", "sNaN"):
            with self.subTest(text=text):
                with self.assertRaises(G.GamaError):
                    G.run('print number("%s")\n' % text)

    def test_abs_nan_should_raise_gama_error(self):
        with self.assertRaises(G.GamaError):
            G.run('print abs(number("NaN"))\n')

    def test_floor_ceil_nan_should_raise_gama_error(self):
        for src in ('print floor(number("NaN"))\n',
                    'print ceil(number("NaN"))\n'):
            with self.subTest(src=src):
                with self.assertRaises(G.GamaError):
                    G.run(src)

    def test_min_max_nan_should_raise_gama_error(self):
        for src in ('print min(number("NaN"), 1)\n',
                    'print max(number("NaN"), 1)\n'):
            with self.subTest(src=src):
                with self.assertRaises(G.GamaError):
                    G.run(src)

    def test_sort_nan_list_should_raise_gama_error(self):
        with self.assertRaises(G.GamaError):
            G.run('print sort([number("NaN"), 1])\n')

    def test_malformed_loop_limit_should_raise_gama_error(self):
        for value in ("", "abc"):
            with self.subTest(value=value):
                env = dict(_CAPPED)
                env["GAMA_L_LOOP_LIMIT"] = value
                with _env(**env):
                    with self.assertRaises(G.GamaError):
                        G.run("let i = 0\nwhile (i < 3) { i := i + 1 }\n")

    @unittest.skipUnless(_has_int_str_limit(),
                         "requires the Python 3.11+ int string digit limit")
    def test_to_int_oversized_digits_should_raise_gama_error(self):
        src = 'print to_int("%s")\n' % ("9" * 5000)
        with self.assertRaises(G.GamaError):
            G.run(src)

    @unittest.skipUnless(_has_int_str_limit(),
                         "requires the Python 3.11+ int string digit limit")
    def test_big_integer_literal_parse_should_raise_gama_error(self):
        src = "print %s\n" % ("9" * 5000)
        with self.assertRaises(G.GamaError):
            G.parse(src)

    @unittest.skipUnless(_has_int_str_limit(),
                         "requires the Python 3.11+ int string digit limit")
    def test_big_integer_print_should_raise_gama_error(self):
        src = "print %s + 1\n" % ("9" * 4300)
        with self.assertRaises(G.GamaError):
            G.run(src)

    @unittest.skipUnless(sys.getrecursionlimit() <= 1000,
                         "default Python recursion budget is larger here")
    def test_default_call_depth_recursion_should_raise_gama_error(self):
        # An explicit high GAMA_L_MAX_CALL_DEPTH must still be clamped to the
        # safe cap derived from the CPython recursion budget, so the guard
        # fires instead of hitting RecursionError.
        src = ("fn down(n: Int) -> Int {\n"
               "  if (n <= 0) { return 0 }\n"
               "  return down(n - 1)\n}\n"
               "print down(400)\n")
        env = dict(_CAPPED)
        env["GAMA_L_MAX_CALL_DEPTH"] = "1000"
        # Keep the other caps out of the way so the only remaining guard is the
        # (too high) call-depth limit.
        env["GAMA_L_MAX_STEPS"] = "1000000"
        env["GAMA_L_MAX_EXPR_STEPS"] = "1000000"
        env["GAMA_L_LOOP_LIMIT"] = "1000000"
        with _env(**env):
            with self.assertRaises(G.GamaError):
                G.run(src)


if __name__ == "__main__":
    unittest.main()
