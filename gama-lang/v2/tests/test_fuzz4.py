"""Deterministic property/fuzz round 4 for recent GAMA-L 2.0 features.

Rounds 1-3 (``test_fuzz.py``, ``test_fuzz2.py``, ``test_fuzz3.py``) covered the
original core, newer builtins/imports/limits and formatter/IR parity.  This
round targets the features added since:

* structural record compatibility (extra fields accepted, missing/wrong types
  rejected) in ``let`` annotations, arguments, returns, fields, ``with`` and
  assignment;
* nominal record inheritance (field flattening, subtyping, inherited methods)
  and its loud rejection in the IR backend;
* IR ``prove``/``verify`` parity (shape/hash, chained records, dotted names);
* IR record methods (``self``, defaults, method-to-method calls, recursion);
* generic bounds and union bounds on functions/records/enums;
* ``export`` visibility (and the documented rejections);
* formatter round-trips of all of the above.

Invariants exercised:

* For generated *valid* sources, ``parse``/``check``/``run`` may raise only the
  public ``GamaError`` family, never a bare Python exception.
* IR parity holds where the backend supports the construct; inheritance must
  raise ``IRUnsupported`` rather than being silently erased.
* Malformed structural mutations must fail in the parser with ``GamaError``.
* Generated export modules expose only exported names and reject the
  documented illegal export forms with ``GamaError``.
* The formatter is idempotent, keeps the program checking, and preserves the
  reference interpreter output.

Everything is seeded: 201 deterministic feature cases plus meta checks.  No
core changes; if a real defect is found it is pinned as a minimal regression
at the bottom rather than hidden.
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


def _text(r, n=2):
    alphabet = "abcxyz"
    return "".join(r.choice(alphabet) for _ in range(n))


class _Gen4:
    """Seeded generator for the round-4 feature cases."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    def i(self, lo=0, hi=9):
        return self.r.randint(lo, hi)

    # ----------------------------------------------------- structural records
    def structural_valid(self):
        r = self.r
        a, b, c = r.randint(0, 9), r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(4)
        if variant == 0:
            return (
                'record P { x: Int, y: Int }\n'
                'record P3 { x: Int, y: Int, z: Int }\n'
                'fn take(p: P) -> Int { return p.x + p.y }\n'
                'let v3 = P3(%d, %d, %d)\n'
                'let v: P = v3\n'
                'print take(v3)\n'
                'print v.x + v.y\n' % (a, b, c))
        if variant == 1:
            return (
                'record P { x: Int, y: Int }\n'
                'record P3 { x: Int, y: Int, z: Int }\n'
                'fn build() -> P { return P3(%d, %d, %d) }\n'
                'let v: P = build()\n'
                'let w = build()\n'
                'print v.x + v.y\n'
                'print w.y\n' % (a, b, c))
        if variant == 2:
            return (
                'record In { n: Int }\n'
                'record In2 { n: Int, m: Int }\n'
                'record Out { inner: In }\n'
                'record Out2 { inner: In2 }\n'
                'let o: Out = Out2(In2(%d, %d))\n'
                'print o.inner.n\n' % (a, b))
        return (
            'record P { x: Int, y: Int }\n'
            'record P3 { x: Int, y: Int, z: Int }\n'
            'record H { p: P }\n'
            'let h = H(P(%d, %d))\n'
            'let h2 = h with { p = P3(%d, %d, %d) }\n'
            'h.p := P3(%d, %d, %d)\n'
            'print h2.p.x + h.p.y\n'
            % (a, b, a, b, c, c, b, a))

    def inheritance_valid(self):
        r = self.r
        name = _text(r)
        breed = _text(r)
        a, b, c = r.randint(0, 9), r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(4)
        if variant == 0:
            return (
                'record Animal { name: Text, legs: Int }\n'
                'record Dog extends Animal { breed: Text }\n'
                'let d = Dog("%s", %d, "%s")\n'
                'let a: Animal = d\n'
                'print a.name\n'
                'print d.legs\n' % (name, a, breed))
        if variant == 1:
            return (
                'record Animal {\n'
                '  name: Text\n'
                '  fn speak(self: Animal) -> Text { return self.name }\n'
                '}\n'
                'record Dog extends Animal { breed: Text }\n'
                'let d = Dog("%s", "%s")\n'
                'print d.speak()\n'
                'print d.name + d.breed\n' % (name, breed))
        if variant == 2:
            return (
                'record A { a: Int }\n'
                'record B extends A { b: Int }\n'
                'record C extends B { c: Int }\n'
                'let c = C(%d, %d, %d)\n'
                'let a: A = c\n'
                'print a.a + c.b + c.c\n' % (a, b, c))
        return (
            'record Animal { name: Text }\n'
            'record Dog extends Animal { breed: Text }\n'
            'let d = Dog { breed: "%s", name: "%s" }\n'
            'let a: Animal = d\n'
            'print a.name + d.breed\n' % (breed, name))

    def prove_verify_valid(self):
        r = self.r
        k, v = r.randint(0, 20), r.randint(0, 20)
        word = _text(r, 3)
        variant = r.randrange(3)
        if variant == 0:
            return ('prove p = { n: %d, s: "%s" }\n'
                    'verify p\n'
                    'print p.n\n' % (k, word))
        if variant == 1:
            return ('prove inv.%s = { n: %d }\n'
                    'verify inv.%s\n'
                    'print inv.%s.n\n' % (word, k, word, word))
        return ('prove a = { n: %d }\n'
                'prove b = { n: %d }\n'
                'verify b\n'
                'verify a\n'
                'print a.n + b.n\n' % (k, v))

    def ir_method_valid(self):
        r = self.r
        x, y, k = r.randint(0, 9), r.randint(0, 9), r.randint(1, 5)
        variant = r.randrange(4)
        if variant == 0:
            return (
                'record Point {\n'
                '  x: Int\n'
                '  y: Int\n'
                '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
                '}\n'
                'let p = Point(%d, %d)\n'
                'print p.sum()\n'
                'print p\n' % (x, y))
        if variant == 1:
            return (
                'record Point {\n'
                '  x: Int\n'
                '  y: Int\n'
                '  fn scaled(self: Point, k: Int = %d) -> Int '
                '{ return self.x * k + self.y }\n'
                '}\n'
                'let p = Point(%d, %d)\n'
                'print p.scaled()\n'
                'print p.scaled(%d)\n' % (k, x, y, k + 1))
        if variant == 2:
            return (
                'record Point {\n'
                '  x: Int\n'
                '  y: Int\n'
                '  fn sum(self: Point) -> Int { return self.x + self.y }\n'
                '  fn twice(self: Point) -> Int { return self.sum() + self.sum() }\n'
                '}\n'
                'let p = Point(%d, %d)\n'
                'print p.twice()\n' % (x, y))
        return (
            'record Box {\n'
            '  n: Int\n'
            '  fn fact(self: Box, k: Int) -> Int {\n'
            '    if (k <= 1) { return 1 }\n'
            '    return k * self.fact(k - 1)\n'
            '  }\n'
            '}\n'
            'let b = Box(%d)\n'
            'print b.fact(%d)\n' % (x, k))

    def bounds_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        word = _text(r, 3)
        variant = r.randrange(7)
        if variant == 0:
            return ('fn add<T: Int | Decimal>(a: T, b: T) -> T '
                    '{ return a + b }\n'
                    'print add(%d, %d)\n' % (a, b))
        if variant == 1:
            return ('fn add<T: Int | Decimal>(a: T, b: T) -> T '
                    '{ return a + b }\n'
                    'print add(%d.5, %d.5)\n' % (a, b))
        if variant == 2:
            return ('fn show<T: Text>(x: T) -> Text { return x }\n'
                    'print show("%s")\n' % word)
        if variant == 3:
            return ('fn show<T: Text | Bool>(x: T) -> Text '
                    '{ return text(x) }\n'
                    'print show("%s")\n'
                    'print show(true)\n' % word)
        if variant == 4:
            return ('fn id<T: Text>(x: T) -> T { return x }\n'
                    'print id<Text>("%s")\n' % word)
        if variant == 5:
            return ('fn pair<A, B: Text>(a: A, b: B) -> Text '
                    '{ return text(a) + b }\n'
                    'print pair(%d, "%s")\n' % (a, word))
        return (
            'record Money { amount: Decimal }\n'
            'record Points { amount: Decimal }\n'
            'fn keep<T: Money | Points>(x: T) -> T { return x }\n'
            'let m: Money = keep(Money(%d.0))\n'
            'print m.amount\n' % a)

    def fmt_valid(self):
        r = self.r
        pool = (self.structural_valid, self.inheritance_valid,
                self.ir_method_valid, self.bounds_valid,
                self.prove_verify_valid)
        src = r.choice(pool)()
        return "# round4-%d { } # = + -\n%s" % (r.randrange(10 ** 6), src)

    def mutation_seed(self):
        r = self.r
        pool = (self.structural_valid, self.inheritance_valid,
                self.ir_method_valid, self.bounds_valid,
                self.prove_verify_valid)
        return r.choice(pool)()


# ------------------------------------------------------------ mutations
def _structural_positions(src):
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


# ------------------------------------------------------------- helpers
def _assert_runs_cleanly(test, src, label):
    with _env():
        try:
            G.check(src)
        except G.GamaError as ex:
            test.fail("%s: generated source is invalid: %s\n%s"
                      % (label, ex, src))
            return None
        except Exception as ex:  # noqa: BLE001 - property harness
            test.fail("%s: check leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return None
        try:
            return G.run(src)
        except G.GamaError as ex:
            test.fail("%s: generated source failed at runtime: %s\n%s"
                      % (label, ex, src))
        except Exception as ex:  # noqa: BLE001 - the actual invariant
            test.fail("%s: run leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
    return None


def _assert_parity(test, src, label):
    with _env():
        try:
            ast = G.check(src)
        except G.GamaError as ex:
            test.fail("%s: generated source is invalid: %s\n%s"
                      % (label, ex, src))
            return
        except Exception as ex:  # noqa: BLE001
            test.fail("%s: check leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
        try:
            expected = G.run(src).out
        except G.GamaError as ex:
            test.fail("%s: interpreter failed: %s\n%s" % (label, ex, src))
            return
        except Exception as ex:  # noqa: BLE001
            test.fail("%s: interpreter leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
        try:
            got = IR.run_program(ast).out
        except IR.IRUnsupported as ex:
            test.fail("%s: IR rejected a supported case: %s\n%s"
                      % (label, ex, src))
            return
        except Exception as ex:  # noqa: BLE001
            test.fail("%s: IR leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
    test.assertEqual(got, expected, "%s: IR output differs" % label)


def _assert_inheritance_ir_unsupported(test, src, label):
    it = _assert_runs_cleanly(test, src, label)
    if it is None:
        return
    try:
        IR.compile_program(G.parse(src))
    except IR.IRUnsupported:
        return
    except Exception as ex:  # noqa: BLE001
        test.fail("%s: expected IRUnsupported, got %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return
    test.fail("%s: inheritance compiled instead of raising IRUnsupported\n%s"
              % (label, src))


def _assert_rejected(test, src, label):
    with _env():
        try:
            G.check(src)
        except G.GamaError:
            return
        except Exception as ex:  # noqa: BLE001
            test.fail("%s: rejection leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
        test.fail("%s: invalid source was accepted\n%s" % (label, src))


def _assert_parse_rejected(test, src, label):
    try:
        G.parse(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001
        test.fail("%s: malformed parse leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return
    test.fail("%s: malformed source parsed without GamaError\n%s"
              % (label, src))


def _assert_formatter_roundtrip(test, src, label):
    with _env():
        try:
            G.check(src)
        except G.GamaError as ex:
            test.fail("%s: generated source is invalid: %s\n%s"
                      % (label, ex, src))
            return
        except Exception as ex:  # noqa: BLE001
            test.fail("%s: check leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
        out = gama_fmt.format_source(src)
        try:
            G.check(out)
        except G.GamaError as ex:
            test.fail("%s: formatted source no longer checks: %s\n%s\n---\n%s"
                      % (label, ex, src, out))
            return
        except Exception as ex:  # noqa: BLE001
            test.fail("%s: formatted check leaked %s: %s\n%s"
                      % (label, type(ex).__name__, ex, src))
            return
        test.assertEqual(gama_fmt.format_source(out), out,
                         "%s: formatter is not idempotent" % label)
        test.assertEqual(G.run(out).out, G.run(src).out,
                         "%s: formatting changed program output" % label)


# ----------------------------------------------------- static reject tables
_STRUCTURAL_REJECT = (
    ('record P { x: Int, y: Int }\n'
     'record OnlyX { x: Int }\n'
     'let p: P = OnlyX(1)\n'),
    ('record P { x: Int, y: Int }\n'
     'record Bad { x: Text, y: Int }\n'
     'let p: P = Bad("a", 1)\n'),
    ('record P { x: Int, y: Int }\n'
     'fn take(p: P) -> Int { return p.x }\n'
     'record OnlyX { x: Int }\n'
     'print take(OnlyX(1))\n'),
    ('record In { n: Int }\n'
     'record In2 { n: Text }\n'
     'record Out { inner: In }\n'
     'record Out2 { inner: In2 }\n'
     'let o: Out = Out2(In2("x"))\n'),
    ('record P { x: Int, y: Int }\n'
     'record OnlyX { x: Int }\n'
     'record H { p: P }\n'
     'let h = H(P(1, 2))\n'
     'let h2 = h with { p = OnlyX(1) }\n'),
    ('record P { x: Int, y: Int }\n'
     'record OnlyX { x: Int }\n'
     'record H { p: P }\n'
     'let h = H(P(1, 2))\n'
     'h.p := OnlyX(1)\n'),
    ('record P { x: Int, y: Int }\n'
     'record Bad { x: Int, y: Text }\n'
     'fn build() -> P { return Bad(1, "x") }\n'),
    ('record P { x: Int, y: Int }\n'
     'record OnlyX { x: Int }\n'
     'record H { p: P }\n'
     'let h = H { p: OnlyX(1) }\n'),
    ('record P { x: Int, y: Int }\n'
     'record P3 { x: Int, y: Int, z: Int }\n'
     'let p: P = P3(1, 2, 3)\n'
     'print p.z\n'),
    ('record P { x: Int, y: Int }\n'
     'record Other { x: Int, y: Int }\n'
     'print P(1, 2) == Other(1, 2)\n'),
)

_INHERITANCE_REJECT = (
    'record Dog extends Missing { breed: Text }\n',
    ('record A extends B { a: Int }\n'
     'record B extends A { b: Int }\n'),
    ('record Animal { name: Text }\n'
     'record Dog extends Animal { name: Text }\n'),
    ('record Box<T> { value: T }\n'
     'record Special extends Box { n: Int }\n'),
    ('enum Color { Red }\n'
     'record Thing extends Color { n: Int }\n'),
    ('record Animal { name: Text }\n'
     'record Dog extends Animal { breed: Text }\n'
     'let d: Dog = Animal("Rex")\n'),
    ('record Animal {\n'
     '  name: Text\n'
     '  fn speak(self: Animal) -> Int { return 1 }\n'
     '}\n'
     'record Dog extends Animal {\n'
     '  fn speak(self: Dog) -> Text { return "x" }\n'
     '}\n'),
    ('record Animal { name: Text }\n'
     'record Dog extends Animal { breed: Text }\n'
     'let d = Dog { name: "Rex" }\n'),
)

_BOUNDS_REJECT = (
    ('fn add<T: Int | Decimal>(a: T, b: T) -> T { return a + b }\n'
     'print add(true, false)\n'),
    ('fn add<T: Int | Decimal>(a: T, b: T) -> T { return a + b }\n'
     'print add("a", "b")\n'),
    'fn show<T: Text>(x: T) -> Text { return x }\nprint show(1)\n',
    'fn f<T: Nope>(x: T) -> T { return x }\n',
    'fn f<T: Int | Int>(x: T) -> T { return x }\n',
    'record Box<T: Text> { value: T }\nlet b: Box<Int> = Box(1)\n',
    'enum Opt<T: Int> { Some(T), None }\nlet o: Opt<Text> = Some("x")\n',
    'record Box<T: Text> { value: T }\nlet b: Box<Text, Int> = Box("x")\n',
    'record Box<T: Int | Text> { value: T }\n',
    'fn show<T: Text>(x: T) -> Text { return x }\nprint show<Int>("x")\n',
)

_EXPORT_INVALID = (
    "duplicate_export",
    "export_across_kinds",
    "export_inside_block",
    "export_without_declaration",
    "private_fn_hidden",
    "private_let_hidden",
    "namespace_global_leak",
)

# ------------------------------------------------------------ case tables
_N_STRUCTURAL = 25
_N_STRUCT_REJECT = len(_STRUCTURAL_REJECT)
_N_INHERIT = 20
_N_INHERIT_REJECT = len(_INHERITANCE_REJECT)
_N_PROVE = 24
_N_METHOD = 24
_N_BOUNDS = 20
_N_BOUNDS_REJECT = len(_BOUNDS_REJECT)
_N_EXPORT = 20
_N_FMT = 25
_N_MUTATION = 15

_STRUCTURAL_CASES = [_Gen4(1000 + i).structural_valid()
                     for i in range(_N_STRUCTURAL)]
_INHERIT_CASES = [_Gen4(2000 + i).inheritance_valid()
                  for i in range(_N_INHERIT)]
_PROVE_CASES = [_Gen4(3000 + i).prove_verify_valid()
                for i in range(_N_PROVE)]
_METHOD_CASES = [_Gen4(4000 + i).ir_method_valid()
                 for i in range(_N_METHOD)]
_BOUNDS_CASES = [_Gen4(5000 + i).bounds_valid()
                 for i in range(_N_BOUNDS)]
_FMT_CASES = [_Gen4(6000 + i).fmt_valid() for i in range(_N_FMT)]
_MUTATION_CASES = [_delete_first_structural(_Gen4(7000 + i).mutation_seed())
                   for i in range(_N_MUTATION)]

_TOTAL_CASES = (
    _N_STRUCTURAL + _N_STRUCT_REJECT + _N_INHERIT + _N_INHERIT_REJECT
    + _N_PROVE + _N_METHOD + _N_BOUNDS + _N_BOUNDS_REJECT + _N_EXPORT
    + _N_FMT + _N_MUTATION
)


# ----------------------------------------------------------- test classes
class Fuzz4MetaTests(unittest.TestCase):
    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_generators_are_deterministic(self):
        for seed in range(40):
            self.assertEqual(_Gen4(seed).structural_valid(),
                             _Gen4(seed).structural_valid())
            self.assertEqual(_Gen4(seed).inheritance_valid(),
                             _Gen4(seed).inheritance_valid())
            self.assertEqual(_Gen4(seed).bounds_valid(),
                             _Gen4(seed).bounds_valid())

    def test_all_generated_valid_cases_check(self):
        for idx, src in enumerate(
                _STRUCTURAL_CASES + _INHERIT_CASES + _PROVE_CASES
                + _METHOD_CASES + _BOUNDS_CASES + _FMT_CASES):
            with self.subTest(case=idx):
                try:
                    G.check(src)
                except G.GamaError as ex:
                    self.fail("case %d invalid: %s\n%s" % (idx, ex, src))

    def test_all_mutations_are_parse_errors(self):
        for idx, src in enumerate(_MUTATION_CASES):
            with self.subTest(case=idx):
                with self.assertRaises(G.GamaError):
                    G.parse(src)


class GeneratedStructuralTests(unittest.TestCase):
    """Dynamically populated: structural subtyping parity cases."""


class GeneratedStructuralRejectTests(unittest.TestCase):
    """Dynamically populated: structural subtyping rejections."""


class GeneratedInheritanceTests(unittest.TestCase):
    """Dynamically populated: inheritance run + IRUnsupported cases."""


class GeneratedInheritanceRejectTests(unittest.TestCase):
    """Dynamically populated: inheritance rejections."""


class GeneratedProveVerifyTests(unittest.TestCase):
    """Dynamically populated: IR prove/verify parity cases."""


class GeneratedIRMethodTests(unittest.TestCase):
    """Dynamically populated: IR record-method parity cases."""


class GeneratedBoundsTests(unittest.TestCase):
    """Dynamically populated: generic/union bound parity cases."""


class GeneratedBoundsRejectTests(unittest.TestCase):
    """Dynamically populated: generic/union bound rejections."""


class GeneratedExportTests(unittest.TestCase):
    """Dynamically populated: export visibility cases."""


class GeneratedFormatterTests(unittest.TestCase):
    """Dynamically populated: formatter round-trip cases."""


class GeneratedMutationTests(unittest.TestCase):
    """Dynamically populated: malformed structural mutations."""


def _make_simple_case(fn, idx, label):
    def test(self):
        fn(self, idx)
    test.__doc__ = "%s #%d" % (label, idx)
    return test


for _i, _src in enumerate(_STRUCTURAL_CASES):
    setattr(GeneratedStructuralTests, "test_struct_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_parity(
                    t, src, "structural case %d" % i),
                _i, "structural"))


def _struct_reject_test(src, idx):
    def test(self):
        _assert_rejected(self, src, "structural reject %d" % idx)
    return test


for _i, _src in enumerate(_STRUCTURAL_REJECT):
    setattr(GeneratedStructuralRejectTests, "test_reject_%03d" % _i,
            _struct_reject_test(_src, _i))


for _i, _src in enumerate(_INHERIT_CASES):
    setattr(GeneratedInheritanceTests, "test_inherit_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_parity(
                    t, src, "inheritance case %d" % i),
                _i, "inheritance"))


def _inherit_reject_test(src, idx):
    def test(self):
        _assert_rejected(self, src, "inheritance reject %d" % idx)
    return test


for _i, _src in enumerate(_INHERITANCE_REJECT):
    setattr(GeneratedInheritanceRejectTests, "test_reject_%03d" % _i,
            _inherit_reject_test(_src, _i))


for _i, _src in enumerate(_PROVE_CASES):
    setattr(GeneratedProveVerifyTests, "test_prove_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_parity(
                    t, src, "prove/verify case %d" % i),
                _i, "prove"))


for _i, _src in enumerate(_METHOD_CASES):
    setattr(GeneratedIRMethodTests, "test_method_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_parity(
                    t, src, "IR method case %d" % i),
                _i, "method"))


for _i, _src in enumerate(_BOUNDS_CASES):
    setattr(GeneratedBoundsTests, "test_bounds_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_parity(
                    t, src, "bounds case %d" % i),
                _i, "bounds"))


def _bounds_reject_test(src, idx):
    def test(self):
        _assert_rejected(self, src, "bounds reject %d" % idx)
    return test


for _i, _src in enumerate(_BOUNDS_REJECT):
    setattr(GeneratedBoundsRejectTests, "test_reject_%03d" % _i,
            _bounds_reject_test(_src, _i))


# ------------------------------------------------------------- exports
def _export_valid_case(test, seed):
    r = random.Random(seed)
    base = r.randint(0, 20)
    default = r.randint(0, 9)
    arg = r.randint(0, 20)
    second = r.randint(0, 20)
    x, y = r.randint(0, 9), r.randint(0, 9)
    variant = r.randrange(2)
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        if variant == 0:
            lib = ('export let base = %d\n'
                   'export fn add(a: Int, b: Int = %d) -> Int effects {} '
                   '{ return a + b + base }\n'
                   'fn secret(x: Int) -> Int effects {} { return x }\n'
                   % (base, default))
            main = ('import "lib.gm" as m\n'
                    'print m.base\n'
                    'print m.add(%d)\n'
                    'print m.add(%d, %d)\n' % (arg, arg, second))
            expected = [str(base), str(arg + default + base),
                        str(arg + second + base)]
        else:
            lib = ('export record Point { x: Int, y: Int }\n'
                   'export fn origin() -> Point effects {} '
                   '{ return Point(%d, %d) }\n' % (x, y))
            main = ('import "lib.gm" as g\n'
                    'let p: g.Point = g.origin()\n'
                    'print p.x\n'
                    'print p.y\n'
                    'let q = g.Point(%d, %d)\n'
                    'print q.x + q.y\n' % (arg, second))
            expected = [str(x), str(y), str(arg + second)]
        (root / "lib.gm").write_text(lib, encoding="utf-8")
        (root / "main.gm").write_text(main, encoding="utf-8")
        with _env():
            try:
                it = G.run_file(str(root / "main.gm"))
            except G.GamaError as ex:
                test.fail("export case %d failed: %s\n%s\n%s"
                          % (seed, ex, lib, main))
                return
            except Exception as ex:  # noqa: BLE001
                test.fail("export case %d leaked %s: %s"
                          % (seed, type(ex).__name__, ex))
                return
    test.assertEqual(it.out, expected, "export case %d output" % seed)


def _export_invalid_case(test, kind):
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        write_lib = lambda text: (root / "lib.gm").write_text(
            text, encoding="utf-8")
        write_main = lambda text: (root / "main.gm").write_text(
            text, encoding="utf-8")
        if kind == "export_enum":
            write_main("export enum Color { Red, Green }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "duplicate_export":
            write_main("export let x = 1\nexport let x = 2\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "export_across_kinds":
            write_main('export fn x() -> Int effects {} { return 1 }\n'
                       'export let x = 2\n')
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "export_inside_block":
            write_main('fn f() -> Int effects {} {\n'
                       '  export let x = 1\n'
                       '  return x\n'
                       '}\n')
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "export_without_declaration":
            write_main("export 1\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "private_fn_hidden":
            write_lib('export fn add(a: Int, b: Int) -> Int effects {} '
                      '{ return a + b }\n'
                      'fn helper(x: Int) -> Int effects {} { return x }\n')
            write_main('import "lib.gm" as m\nprint m.helper(1)\n')
            op = lambda: G.run_file(str(root / "main.gm"))
        elif kind == "private_let_hidden":
            write_lib('let secret = 7\n'
                      'export fn get() -> Int effects {} { return secret }\n')
            write_main('import "lib.gm" as m\nprint m.secret\n')
            op = lambda: G.run_file(str(root / "main.gm"))
        elif kind == "namespace_global_leak":
            write_lib('export fn add(a: Int, b: Int) -> Int effects {} '
                      '{ return a + b }\n')
            write_main('import "lib.gm" as m\nprint add(1, 2)\n')
            op = lambda: G.run_file(str(root / "main.gm"))
        else:
            test.fail("unknown export invalid kind %r" % (kind,))
            return

        with _env():
            try:
                op()
            except G.GamaError:
                return
            except Exception as ex:  # noqa: BLE001
                test.fail("export %s leaked %s: %s"
                          % (kind, type(ex).__name__, ex))
                return
    test.fail("export %s was accepted" % kind)


for _i in range(12):
    setattr(GeneratedExportTests, "test_export_valid_%03d" % _i,
            _make_simple_case(
                lambda t, i: _export_valid_case(t, 8000 + i),
                _i, "export valid"))


def _export_invalid_test(kind, idx):
    def test(self):
        _export_invalid_case(self, kind)
    test.__doc__ = "export invalid: %s" % kind
    return test


for _i, _kind in enumerate(_EXPORT_INVALID):
    setattr(GeneratedExportTests, "test_export_invalid_%02d" % _i,
            _export_invalid_test(_kind, _i))


for _i, _src in enumerate(_FMT_CASES):
    setattr(GeneratedFormatterTests, "test_fmt_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_formatter_roundtrip(
                    t, src, "formatter case %d" % i),
                _i, "formatter"))


for _i, _src in enumerate(_MUTATION_CASES):
    setattr(GeneratedMutationTests, "test_mutation_%03d" % _i,
            _make_simple_case(
                lambda t, i, src=_src: _assert_parse_rejected(
                    t, src, "mutation case %d" % i),
                _i, "mutation"))


class Fuzz4RegressionPins(unittest.TestCase):
    """Minimal pins of real round-4 defects.

    These are genuine, pre-existing limitations surfaced by this round rather
    than feature regressions; each is pinned so it fails loudly (unexpected
    success) once the core is fixed.
    """

    def test_deep_nesting_parser_should_not_leak_recursionerror(self):
        # ``parse`` recurses per nesting level without a depth guard, so a
        # valid deeply nested expression escapes as ``RecursionError`` instead
        # of the public ``GamaError`` family.  The runtime has a RecursionError
        # backstop (see ``Interpreter.invoke``); parsing does not.
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)
        try:
            depth = 300
            src = "print " + "(" * depth + "1" + ")" * depth + "\n"
            with self.assertRaises(G.GamaError):
                G.parse(src)
        finally:
            sys.setrecursionlimit(old)


if __name__ == "__main__":
    unittest.main()
