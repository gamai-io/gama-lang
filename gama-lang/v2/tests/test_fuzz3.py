"""Deterministic property/fuzz round 3 for GAMA-L 2.0.

Round 1 (``test_fuzz.py``) covered the core language leaks, round 2
(``test_fuzz2.py``) the newer builtins/imports/limits.  This round adds
property-style checks that did not exist yet, all driven by fixed seeds and
without touching the core:

* **Formatter**: for every generated valid program, ``parse(fmt(src))``
  succeeds, ``fmt`` is idempotent, the run output is unchanged, and every
  comment body and string literal survives verbatim.
* **IR parity**: for every generated program inside the IR-supported subset,
  ``gama_ir.run_program`` output equals the reference interpreter output;
  constructs outside the subset are rejected with ``IRUnsupported``.
* **Namespaced constants/functions**: generated temp modules are imported
  under an alias and their constants/functions read from a generated main
  file; missing names fail with the public ``GamaError`` family.
* **Limits**: small randomly chosen ``GAMA_L_MAX_STEPS``,
  ``GAMA_L_MAX_EXPR_STEPS``, ``GAMA_L_LOOP_LIMIT`` and
  ``GAMA_L_MAX_CALL_DEPTH`` must surface as ``GamaError``, never as a bare
  Python exception.

There are 238 deterministic cases (90 formatter, 80 IR, 24 namespaced,
32 limit, 12 unsupported).  The formatter regression found by this round (a
block opened on the same line as a multi-line string opener lost the
indentation of the following lines) is pinned as a normal regression test
below; both the AST formatter and the legacy fallback now keep the depth.
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
import gama_fmt
import gama_ir as IR


def _env(**kw):
    """Clean GAMA-L environment with deterministic overrides."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("GAMA_L_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


_CAPPED = {
    "GAMA_L_MAX_STEPS": "100000",
    "GAMA_L_MAX_EXPR_STEPS": "100000",
    "GAMA_L_LOOP_LIMIT": "100000",
    "GAMA_L_MAX_CALL_DEPTH": "60",
    "GAMA_L_MAX_OUTPUT_CHARS": "100000",
}


class _Gen3:
    """Seeded generator of small valid programs for the round-3 properties."""

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

    def bool_expr(self):
        if self.r.random() < 0.35:
            return self.r.choice(["true", "false"])
        op = self.r.choice(["<", "<=", ">", ">=", "==", "!="])
        return "(%s %s %s)" % (self.int_expr(1), op, self.int_expr(1))

    def text_lit(self):
        # Includes a space, a '#', and an escaped quote: all must survive.
        return self.r.choice(['"high"', '"low"', '"a b"', '"x#y"', '"q\\"q"'])

    def int_list(self):
        return "[" + ", ".join(self.int_lit()
                               for _ in range(self.r.randint(1, 4))) + "]"

    # -------------------------------------------------- IR-safe templates
    def t_arithmetic(self):
        n = self.r.randint(1, 3)
        lines = ["let a%d = %s" % (i, self.int_expr()) for i in range(n)]
        lines.append("print (%s)" % " + ".join("a%d" % i for i in range(n)))
        return "\n".join(lines) + "\n"

    def t_while(self):
        return ("let i = 0\n"
                "while (i < %d) {\n  i := i + %d\n}\nprint i\n"
                % (self.r.randint(1, 8), self.r.randint(1, 3)))

    def t_if_chain(self):
        x = self.r.randint(0, 12)
        return ("let x = %d\n"
                "if (x > 10) {\n  print %s\n"
                "} else if (x > 5) {\n  print %s\n"
                "} else {\n  print %s\n}\n"
                % (x, self.text_lit(), self.text_lit(), self.text_lit()))

    def t_list(self):
        xs = self.int_list()
        idx = self.r.randrange(xs.count(",") + 1)
        return ("let xs = %s\n"
                "print xs[%d]\n"
                "print len(xs)\n" % (xs, idx))

    def t_map(self):
        return ("let m = {a: %d, b: %d}\n"
                "print m\nprint len(m)\n"
                % (self.r.randint(0, 9), self.r.randint(0, 9)))

    def t_function(self):
        return ("fn add(x: Int, y: Int = %d) -> Int {\n"
                "  return x + y\n}\n"
                "print add(%s)\n"
                % (self.r.randint(0, 9), self.int_expr()))

    def t_for(self):
        return ("for x in %s {\n  print x\n}\n" % self.int_list())

    def t_match(self):
        return ("let r = %s(%d)\n"
                "match r {\n  Ok(v) { print v }\n"
                "  Err(e) { print \"err\" }\n}\n"
                % (self.r.choice(["Ok", "Err"]), self.r.randint(0, 9)))

    def t_prints(self):
        return ("print %s\nprint %s\nprint %s\n"
                % (self.bool_expr(), self.int_expr(), self.text_lit()))

    def t_mixed(self):
        return ("let base = %s\n"
                "let xs = %s\n"
                "if (base > %d) {\n  print xs[0]\n"
                "} else {\n  print base\n}\n"
                % (self.int_expr(), self.int_list(), self.r.randint(0, 9)))

    def t_unary(self):
        return ("let x = -%d\n"
                "print -%d + %d\n"
                "print %d - -%d\n"
                "print x * -%d\n"
                % (self.r.randint(1, 9), self.r.randint(1, 9),
                   self.r.randint(0, 9), self.r.randint(0, 9),
                   self.r.randint(1, 9), self.r.randint(1, 9)))

    IR_TEMPLATES = (
        t_arithmetic, t_while, t_if_chain, t_list, t_map, t_function,
        t_for, t_match, t_prints, t_mixed, t_unary,
    )

    # ------------------------------------------------ formatter-only extras
    def t_lambda(self):
        return ("let f = fn(x: Int) -> Int {\n"
                "  return x * %d\n}\n"
                "print f(%s)\n"
                % (self.r.randint(1, 5), self.int_expr()))

    def t_try(self):
        return ("fn f(x: Int) -> Result {\n"
                "  let v = Ok(x)?\n  return Ok(v)\n}\n"
                "print f(%s)\n" % self.int_expr())

    def t_pipe(self):
        return ("print %s | count\n" % self.int_list())

    def t_text_builtins(self):
        return ('print upper("abc")\n'
                'print contains("abc", "b")\n'
                'print len("a b")\n')

    def t_multiline_string(self):
        return ('let s = "first line\n'
                'second } { # line"\n'
                'print s\n')

    FMT_ONLY = (t_lambda, t_try, t_pipe, t_text_builtins,
                t_multiline_string)

    def program(self, ir_safe=False):
        pool = self.IR_TEMPLATES if ir_safe else (
            self.IR_TEMPLATES + self.FMT_ONLY)
        return self.r.choice(pool)(self)


def _tag(seed):
    return '"fmt-%d { } # = + - a b"' % seed


def _has_multiline_string(src):
    quote = None
    i = 0
    while i < len(src):
        c = src[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            elif c == "\n":
                return True
        elif c in "\"'":
            quote = c
        elif c == "#":
            while i < len(src) and src[i] != "\n":
                i += 1
            continue
        i += 1
    return False


def _add_comments(src, seed):
    """Add one top-level comment and, when safe, one inline comment."""
    r = random.Random(seed * 7 + 3)
    lines = src.split("\n")
    out = ["# top-%d" % seed]
    multiline = _has_multiline_string(src)
    for i, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        if (not multiline and stripped and not stripped.startswith("#")
                and not stripped.endswith("{") and r.random() < 0.12):
            out[-1] = line + " # inline-%d-%d" % (seed, i)
    return "\n".join(out)


def _fmt_source(seed):
    return _add_comments(
        _Gen3(seed).program() + "print %s\n" % _tag(seed), seed)


def _ir_source(seed):
    return _Gen3(seed).program(ir_safe=True)


def _scan_comment_and_string_tokens(src):
    """Return (comment texts, raw string literals) outside comments."""
    comments, strings = [], []
    i, n, quote = 0, len(src), None
    while i < n:
        c = src[i]
        if quote is not None:
            start = i
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            strings.append(src[start:i])
            quote = None
            continue
        if c in "\"'":
            quote = c
            i += 1
            continue
        if c == "#":
            start = i
            while i < n and src[i] != "\n":
                i += 1
            comments.append(src[start:i].rstrip())
            continue
        i += 1
    return comments, strings


# ------------------------------------------------------------ case tables
_N_FMT = 90
_N_IR = 80
_N_NS = 24
_N_LIMIT_PER_KEY = 8
_N_UNSUPPORTED = 12

_FMT_CASES = [_fmt_source(seed) for seed in range(_N_FMT)]
_IR_CASES = [_ir_source(seed) for seed in range(_N_IR)]
_TOTAL_CASES = _N_FMT + _N_IR + _N_NS + 4 * _N_LIMIT_PER_KEY + _N_UNSUPPORTED


# ------------------------------------------------------- property helpers
def _assert_formatter_property(test, src, label):
    try:
        G.check(src)
    except G.GamaError as ex:
        test.fail("%s: generator produced an invalid source: %s\n%s"
                  % (label, ex, src))
        return

    out = gama_fmt.format_source(src)
    try:
        G.check(out)
    except G.GamaError as ex:
        test.fail("%s: formatted source no longer parses: %s\n%s\n---\n%s"
                  % (label, ex, src, out))
        return
    except Exception as ex:  # noqa: BLE001 - property harness
        test.fail("%s: formatted parse leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return

    test.assertEqual(gama_fmt.format_source(out), out,
                     "%s: formatter is not idempotent" % label)

    comments, strings = _scan_comment_and_string_tokens(src)
    for comment in comments:
        test.assertIn(comment, out,
                      "%s: formatter lost comment %r" % (label, comment))
    for literal in strings:
        test.assertIn(literal, out,
                      "%s: formatter lost string literal %r" % (label, literal))

    with _env(**_CAPPED):
        test.assertEqual(G.run(out).out, G.run(src).out,
                         "%s: formatting changed program output" % label)


def _assert_ir_parity(test, src, label):
    with _env(**_CAPPED):
        try:
            ast = G.check(src)
        except G.GamaError as ex:
            test.fail("%s: generator produced an invalid source: %s\n%s"
                      % (label, ex, src))
            return
        expected = G.run(src).out
        try:
            got = IR.run_program(ast).out
        except IR.IRUnsupported as ex:
            test.fail("%s: IR-safe case was rejected: %s\n%s"
                      % (label, ex, src))
            return
        except Exception as ex:  # noqa: BLE001 - property harness
            test.fail("%s: IR leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
    test.assertEqual(got, expected, "%s: IR output differs" % label)


def _assert_ir_unsupported(test, src, label):
    try:
        ast = G.parse(src)
    except G.GamaError as ex:
        test.fail("%s: unsupported sample is not parseable: %s\n%s"
                  % (label, ex, src))
        return
    try:
        IR.compile_program(ast)
    except IR.IRUnsupported:
        return
    except Exception as ex:  # noqa: BLE001
        test.fail("%s: expected IRUnsupported, got %s: %s"
                  % (label, type(ex).__name__, ex))
        return
    test.fail("%s: unsupported construct compiled instead of raising" % label)


def _assert_limit_error(test, key, value, src, label):
    env = dict(_CAPPED)
    env.pop("GAMA_L_MAX_STEPS", None)
    env.pop("GAMA_L_MAX_EXPR_STEPS", None)
    env.pop("GAMA_L_LOOP_LIMIT", None)
    env.pop("GAMA_L_MAX_CALL_DEPTH", None)
    env[key] = str(value)
    try:
        with _env(**env):
            G.run(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001 - the invariant under test
        test.fail("%s leaked %s: %s" % (label, type(ex).__name__, ex))
        return
    test.fail("%s did not raise GamaError at %s=%r" % (label, key, value))


# ------------------------------------------------------------ test classes
class Fuzz3MetaTests(unittest.TestCase):
    def test_generator_is_deterministic(self):
        for seed in range(_N_FMT):
            self.assertEqual(_fmt_source(seed), _fmt_source(seed))
        for seed in range(_N_IR):
            self.assertEqual(_ir_source(seed), _ir_source(seed))

    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_formatter_cases_are_valid(self):
        for idx, src in enumerate(_FMT_CASES):
            with self.subTest(case=idx):
                try:
                    G.check(src)
                except G.GamaError as ex:
                    self.fail("case %d invalid: %s\n%s" % (idx, ex, src))

    def test_ir_cases_are_valid(self):
        for idx, src in enumerate(_IR_CASES):
            with self.subTest(case=idx):
                try:
                    G.check(src)
                except G.GamaError as ex:
                    self.fail("case %d invalid: %s\n%s" % (idx, ex, src))

    def test_generated_cases_contain_comments_and_strings(self):
        for idx, src in enumerate(_FMT_CASES):
            comments, strings = _scan_comment_and_string_tokens(src)
            with self.subTest(case=idx):
                self.assertTrue(comments, "case %d has no comment" % idx)
                self.assertTrue(strings, "case %d has no string" % idx)


class GeneratedFormatterTests(unittest.TestCase):
    """Dynamically populated: 90 deterministic formatter property cases."""


class GeneratedIRTests(unittest.TestCase):
    """Dynamically populated: 80 deterministic IR parity cases."""


def _make_fmt_case(src, idx):
    def test(self):
        _assert_formatter_property(self, src, "fmt case %d" % idx)
    test.__doc__ = "Formatter property #%d" % idx
    return test


def _make_ir_case(src, idx):
    def test(self):
        _assert_ir_parity(self, src, "IR case %d" % idx)
    test.__doc__ = "IR parity #%d" % idx
    return test


for _i, _src in enumerate(_FMT_CASES):
    setattr(GeneratedFormatterTests, "test_fmt_%03d" % _i,
            _make_fmt_case(_src, _i))

for _i, _src in enumerate(_IR_CASES):
    setattr(GeneratedIRTests, "test_ir_%03d" % _i,
            _make_ir_case(_src, _i))


class NamespacedConstantsFuzzTests(unittest.TestCase):
    """Generated temp modules: namespaced constants and functions."""

    def _case(self, seed):
        r = random.Random(seed)
        base = r.randint(0, 20)
        factor = r.randint(1, 9)
        default = r.randint(0, 9)
        arg = r.randint(0, 20)
        module = (
            "let base = %d\n"
            "let factor = %d\n"
            "fn scale(x: Int) -> Int effects {} { return x * factor }\n"
            "fn quad(x: Int) -> Int effects {} { return scale(scale(x)) }\n"
            "fn add(a: Int, b: Int = %d) -> Int effects {} "
            "{ return a + b + base }\n"
            % (base, factor, default))
        main = ("import \"lib.gm\" as ns\n"
                "print ns.base\n"
                "print ns.scale(%d)\n"
                "print ns.quad(%d)\n"
                "print ns.add(%d)\n" % (arg, arg, arg))
        expected = [str(base), str(arg * factor),
                    str(arg * factor * factor), str(arg + default + base)]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "lib.gm").write_text(module, encoding="utf-8")
            (root / "main.gm").write_text(main, encoding="utf-8")
            with _env(**_CAPPED):
                it = G.run_file(str(root / "main.gm"))
        self.assertEqual(it.out, expected)

    def test_two_aliases_of_same_module(self):
        module = ("let base = 3\n"
                  "fn twice(x: Int) -> Int effects {} { return x * 2 }\n")
        main = ('import "lib.gm" as a\n'
                'import "lib.gm" as b\n'
                "print a.base + b.base\n"
                "print a.twice(4) + b.twice(1)\n")
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "lib.gm").write_text(module, encoding="utf-8")
            (root / "main.gm").write_text(main, encoding="utf-8")
            with _env(**_CAPPED):
                it = G.run_file(str(root / "main.gm"))
        self.assertEqual(it.out, ["6", "10"])

    def test_same_function_name_in_two_modules(self):
        main = ('import "one.gm" as a\n'
                'import "two.gm" as b\n'
                "print a.value()\nprint b.value()\n")
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "one.gm").write_text(
                "fn value() -> Int effects {} { return 1 }\n", encoding="utf-8")
            (root / "two.gm").write_text(
                "fn value() -> Int effects {} { return 2 }\n", encoding="utf-8")
            (root / "main.gm").write_text(main, encoding="utf-8")
            with _env(**_CAPPED):
                it = G.run_file(str(root / "main.gm"))
        self.assertEqual(it.out, ["1", "2"])

    def test_missing_names_and_aliases_raise_gama_error(self):
        lib = ("let base = 1\n"
               "fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n")
        cases = [
            'import "lib.gm" as ns\nprint ns.missing\n',
            'import "lib.gm" as ns\nprint ns.missing()\n',
            'import "lib.gm" as ns\nimport "lib.gm" as ns\nprint ns.base\n',
        ]
        for main in cases:
            with self.subTest(main=main):
                with tempfile.TemporaryDirectory() as td:
                    root = pathlib.Path(td)
                    (root / "lib.gm").write_text(lib, encoding="utf-8")
                    (root / "main.gm").write_text(main, encoding="utf-8")
                    with _env(**_CAPPED):
                        with self.assertRaises(G.GamaError):
                            G.run_file(str(root / "main.gm"))


class GeneratedNamespaceTests(unittest.TestCase):
    """Dynamically populated: 24 generated namespaced-access cases."""


def _make_ns_case(case, idx):
    def test(self):
        case._case(idx)
    test.__doc__ = "Namespaced constants/functions #%d" % idx
    return test


_NS_PROTO = NamespacedConstantsFuzzTests("test_missing_names_and_aliases_raise_gama_error")
for _i in range(_N_NS):
    setattr(GeneratedNamespaceTests, "test_ns_%03d" % _i,
            _make_ns_case(_NS_PROTO, _i))
del _NS_PROTO


class LimitEnvTests(unittest.TestCase):
    """Small random caps must surface as GamaError, never Python errors."""

    _PROGRAMS = {
        "GAMA_L_MAX_STEPS": (
            "let i = 0\n"
            "while (i < 30) {\n  i := i + 1\n  print i\n}\n"),
        "GAMA_L_MAX_EXPR_STEPS": (
            "print %s\n" % " + ".join(str(i) for i in range(1, 20))),
        "GAMA_L_LOOP_LIMIT": (
            "let i = 0\n"
            "while (i < 30) {\n  i := i + 1\n}\nprint i\n"),
        "GAMA_L_MAX_CALL_DEPTH": (
            "fn down(n: Int) -> Int {\n"
            "  if (n <= 0) { return 0 }\n"
            "  return down(n - 1)\n}\n"
            "print down(25)\n"),
    }


def _make_limit_case(key, value, idx):
    src = LimitEnvTests._PROGRAMS[key]

    def test(self):
        _assert_limit_error(self, key, value, src,
                            "%s case %d (value=%r)" % (key, idx, value))
    test.__doc__ = "%s=%r" % (key, value)
    return test


for _key in LimitEnvTests._PROGRAMS:
    _r = random.Random(9000 + sum(map(ord, _key)))
    for _i in range(_N_LIMIT_PER_KEY):
        _value = _r.randrange(0, 8)
        setattr(LimitEnvTests, "test_%s_%02d" % (_key[7:].lower(), _i),
                _make_limit_case(_key, _value, _i))


class IRUnsupportedTests(unittest.TestCase):
    """Constructs outside the documented IR subset must raise IRUnsupported."""

    _CASES = (
        ("prove", "prove p = 1\n"),
        ("verify", "verify true\n"),
        ("import", 'import "lib.gm" as m\nprint 1\n'),
        # Dotted names (field access / enum variants) are supported now; this
        # slot keeps a construct that is still outside the IR subset.
        ("wait_user", 'wait user "ok?"\n'),
        ("ask", 'print ask("q")\n'),
        ("try", "print Ok(1)?\n"),
        ("lambda", "let f = fn(x: Int) -> Int { return x }\nprint f(1)\n"),
        ("unsupported builtin", 'print upper("x")\n'),
        ("unsupported helper", "print is_ok(Ok(1))\n"),
        ("break outside loop", "break\n"),
        ("return outside function", "return 1\n"),
        ("expression callee", "print (fn(x: Int) -> Int { return x })(1)\n"),
    )

    def test_case_count(self):
        self.assertEqual(len(self._CASES), _N_UNSUPPORTED)


def _make_unsupported_case(src, label):
    def test(self):
        _assert_ir_unsupported(self, src, "unsupported %s" % label)
    test.__doc__ = "IRUnsupported: %s" % label
    return test


for _label, _src in IRUnsupportedTests._CASES:
    setattr(IRUnsupportedTests, "test_unsupported_%s"
            % _label.replace(" ", "_"),
            _make_unsupported_case(_src, _label))


class FormatterDepthRegressionTests(unittest.TestCase):
    """Regression for the multi-line-string block depth defect.

    A block ``{`` sharing a line with a multi-line string opener must still
    indent later statements and the closing ``}`` correctly.  The AST formatter
    keeps the first statement on the opener line; the legacy fallback tracks
    braces outside string/comment text.
    """

    def test_block_opened_on_multiline_string_line_keeps_indent(self):
        src = ('if (true) { let s = "a\n'
               'b"\n'
               'print s\n'
               '}\n')
        out = gama_fmt.format_source(src)
        lines = out.splitlines()
        self.assertEqual(lines[2], "  print s")
        self.assertEqual(lines[3], "}")
        self.assertEqual(out, gama_fmt.format_source(out))

    def test_legacy_formatter_tracks_depth_on_string_opener_line(self):
        src = ('if (true) { let s = "a\n'
               'b"\n'
               'print s\n'
               '}\n')
        self.assertEqual(
            gama_fmt._legacy_format_source(src),
            'if (true) { let s = "a\n'
            'b"\n'
            '  print s\n'
            '}\n',
        )


if __name__ == "__main__":
    unittest.main()
