"""Deterministic property/fuzz round 9 for the current GAMA-L 2.0 surface.

Rounds 1-8 covered the core, builtins/imports/limits, formatter/IR parity,
structural records, bounds, prove/verify, record methods, nominal
inheritance, export enums/renames, runtime-limit parity, selective
re-exports, type aliases, generic/union aliases, nested ``with`` paths,
intersection bounds and the first return/effect inference probes.

This round re-samples the newest surface with fresh seeds and focuses on:

* return type inference (``fn f(x: Int) { return x + 1 }``) across
  arithmetic, records, enums, lists, Result, ``?`` propagation, forward
  references, methods, lambdas, IIFEs, generic calls, aliases and ``with``;
* effect inference (omitted ``effects { ... }``) through direct prints,
  callees, chains, recursion/mutual recursion, match/if/while/for, methods
  and function values, with explicit contracts still enforced;
* record/list patterns and guards: enum and Result arm shapes are exercised
  directly, while the documented limitations (no record destructuring, no
  list patterns, no arm guards) are probed with ``GamaError`` assertions
  instead of being skipped;
* nested record ``with`` paths combined with aliases, inheritance, generic
  fields, inferred constructors and chained updates;
* intersection / union / generic aliases (including generic union aliases,
  aliases to enums/Result/List and aliases crossing ``with``/bounds);
* formatter round-trips and IR parity for every new construct;
* malformed mutations of inference/pattern/``with``/alias sources that must
  stay inside the ``GamaError`` family.

Invariants exercised:

* Generated *valid* sources raise only ``GamaError`` from
  ``parse``/``check``/``run``; a bare Python exception is a failure.
* IR parity holds for the supported constructs above; an unexpected
  ``IRUnsupported`` for a supported case is a failure.
* Malformed mutations fail the parser/checker with ``GamaError``.
* The formatter is idempotent, keeps the program checking, and preserves the
  reference interpreter output.

Everything is seeded.  Two genuine effect-checking defects found while
writing this round are pinned as minimal ``expectedFailure`` tests at the
bottom; an unexpected success means they were fixed.
"""
import os
import pathlib
import random
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_fmt
import gama_ir as IR


_WIDE = {
    "GAMA_L_MAX_STEPS": "1000000",
    "GAMA_L_MAX_EXPR_STEPS": "1000000",
    "GAMA_L_LOOP_LIMIT": "1000000",
    "GAMA_L_MAX_CALL_DEPTH": "1000",
    "GAMA_L_MAX_OUTPUT_CHARS": "1000000",
}


def _env(**kw):
    base = {k: v for k, v in os.environ.items() if not k.startswith("GAMA_L_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


class _Gen9:
    """Seeded generator for round-9 cases.

    The variant index is derived from the seed (``seed % N``) rather than from
    ``random`` so every case table visits every variant exactly once.
    """

    def __init__(self, seed):
        self.seed = seed
        self.r = random.Random(seed)

    # ---------------------------------------- return/effect inference
    def inference_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        v = self.seed % 40
        if v == 0:
            src = "fn f(x: Int) { return x + 1 }\nprint f(%d)\n" % a
            expected = [str(a + 1)]
        elif v == 1:
            src = "fn f(x: Int) { let y = x + 1 }\nprint f(%d)\n" % a
            expected = ["None"]
        elif v == 2:
            src = ("fn f(x: Int) { return [x, x] }\n"
                   "let xs: List = f(%d)\nprint xs\n" % a)
            expected = ["[%d, %d]" % (a, a)]
        elif v == 3:
            src = ("fn f(b: Bool) { if b { return 1 } else { return 2 } }\n"
                   "let y: Int = f(true)\nprint y\n")
            expected = ["1"]
        elif v == 4:
            src = ("fn f(b: Bool) { if b { return %d } return %d }\n"
                   "let y: Int = f(false)\nprint y\n" % (a, b))
            expected = [str(b)]
        elif v == 5:
            src = ("fn f(n: Int) { let s = 0\n"
                   "  while s < n { s := s + 1 }\n  return s }\n"
                   "let y: Int = f(%d)\nprint y\n" % a)
            expected = [str(a)]
        elif v == 6:
            src = ("fn f(xs: List) { let s = 0\n"
                   "  for x in xs { s := s + x }\n  return s }\n"
                   "let y: Int = f([%d, %d])\nprint y\n" % (a, b))
            expected = [str(a + b)]
        elif v == 7:
            src = ("fn g(x: Int) -> Int { return x + 1 }\n"
                   "fn f(x: Int) { return g(x) }\n"
                   "let y: Int = f(%d)\nprint y\n" % a)
            expected = [str(a + 1)]
        elif v == 8:
            src = ("fn a(n: Int) { return b(n) }\n"
                   "fn b(n: Int) { return n + 2 }\n"
                   "let y: Int = a(%d)\nprint y\n" % a)
            expected = [str(a + 2)]
        elif v == 9:
            src = ("record R { v: Int\n"
                   "  fn get(self: R) { return self.v + 1 }\n}\n"
                   "let r = R(%d)\nlet y: Int = r.get()\nprint y\n" % a)
            expected = [str(a + 1)]
        elif v == 10:
            src = ("let f = fn(x: Int) { return x + %d }\n"
                   "let y: Int = f(%d)\nprint y\n" % (b, a))
            expected = [str(a + b)]
        elif v == 11:
            src = ("let p = (fn(n: Int) { return n * 2 })(%d)\n"
                   "let y: Int = p\nprint y\n" % a)
            expected = [str(a * 2)]
        elif v == 12:
            src = ("record P { x: Int }\n"
                   "fn mk(n: Int) { return P(n) }\n"
                   "let p: P = mk(%d)\nprint p.x\n" % a)
            expected = [str(a)]
        elif v == 13:
            src = ("enum E { A(Int), B }\n"
                   "fn mk(n: Int) { return E.A(n) }\n"
                   "let e: E = mk(%d)\nprint e\n" % a)
            expected = ["E.A(%d)" % a]
        elif v == 14:
            src = ("fn f(n: Int) { return text(n) }\n"
                   'let s: Text = f(%d)\nprint s\n' % a)
            expected = [str(a)]
        elif v == 15:
            src = ("fn f(n: Int) { return n > %d }\n"
                   "let b: Bool = f(%d)\nprint b\n" % (b, a))
            expected = [str(a > b)]
        elif v == 16:
            src = ("fn f(b: Bool) { if b { return %d } return 1.5 }\n"
                   "let d: Decimal = f(false)\nprint d\n" % a)
            expected = ["1.5"]
        elif v == 17:
            src = ("fn f(a: Text, b: Text) { return a + b }\n"
                   'let s: Text = f("%s", "%s")\nprint s\n' % (s, s))
            expected = [s + s]
        elif v == 18:
            src = ("fn f(n: Int) { return [n, n, n] }\n"
                   "let xs: List = f(%d)\nprint len(xs)\n" % a)
            expected = ["3"]
        elif v == 19:
            src = ("fn ok(x: Int) { return Ok(x) }\n"
                   "let r: Result = ok(%d)\nprint r\n" % a)
            expected = ["Ok(%d)" % a]
        elif v == 20:
            src = ("fn id<T>(x: T) -> T { return x }\n"
                   "fn f(n: Int) { return id(id(n)) }\n"
                   "let y: Int = f(%d)\nprint y\n" % a)
            expected = [str(a)]
        elif v == 21:
            src = ("fn f(n: Int) {\n"
                   "  let g = fn(x: Int) { return x + n }\n"
                   "  return g(1) }\n"
                   "let y: Int = f(%d)\nprint y\n" % a)
            expected = [str(a + 1)]
        elif v == 22:
            src = ("record P { x: Int }\n"
                   "fn mk(n: Int) { return P(n) }\n"
                   "let r = mk(%d) with { x = %d }\nprint r.x\n" % (a, b))
            expected = [str(b)]
        elif v == 23:
            src = ("type I = Int\n"
                   "fn f(n: Int) { return n + 1 }\n"
                   "let y: I = f(%d)\nprint y\n" % a)
            expected = [str(a + 1)]
        elif v == 24:
            src = ("enum E { P(Int), Q }\n"
                   "record R { e: E\n"
                   "  fn val(self: R) { match self.e {"
                   " P(x) => { return x } Q => { return %d } } }\n}\n"
                   "let r = R(E.P(%d))\nlet y: Int = r.val()\nprint y\n"
                   % (b, a))
            expected = [str(a)]
        elif v == 25:
            src = ("fn g(x: Int) -> Int { print x\n return x + 1 }\n"
                   "fn f(x: Int) { return g(x) }\nprint f(%d)\n" % a)
            expected = [str(a), str(a + 1)]
        elif v == 26:
            src = ("fn a(x: Int) { return b(x) }\n"
                   "fn b(x: Int) { print x\n return c(x) }\n"
                   "fn c(x: Int) { return x + 1 }\n"
                   "let y: Int = a(%d)\nprint y\n" % a)
            expected = [str(a), str(a + 1)]
        elif v == 27:
            src = ("fn f(n: Int) -> Int { if n > 0 { print n }\n"
                   "  let s = 0\n"
                   "  for i in [1, 2] { print i\n s := s + i }\n"
                   "  return s }\nprint f(%d)\n" % (a + 1))
            expected = [str(a + 1), "1", "2", "3"]
        elif v == 28:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { print x\n"
                   "  return x } Q => { return 0 } } }\n"
                   "print f(E.P(%d))\n" % a)
            expected = [str(a), str(a)]
        elif v == 29:
            src = ("fn f(x: Int) -> Int effects { io.print } "
                   "{ print x\n return x + 1 }\nprint f(%d)\n" % a)
            expected = [str(a), str(a + 1)]
        elif v == 30:
            src = ("fn f(x: Int) -> Int effects {} { return x * 2 }\n"
                   "print f(%d)\n" % a)
            expected = [str(a * 2)]
        elif v == 31:
            src = ("fn fact(n: Int) { if n <= 1 { return 1 } "
                   "return n * fact(n - 1) }\nprint fact(5)\n")
            expected = ["120"]
        elif v == 32:
            src = ("fn a(n: Int) { if n <= 0 { return 0 } print n\n"
                   "  return b(n - 1) }\n"
                   "fn b(n: Int) { return a(n) }\nprint a(2)\n")
            expected = ["2", "1", "0"]
        elif v == 33:
            src = ("fn g(x: Int) -> Int { return x + 1 }\n"
                   "record R { v: Int\n"
                   "  fn run(self: R) -> Int { return g(self.v) }\n}\n"
                   "let r = R(%d)\nlet y: Int = r.run()\nprint y\n" % a)
            expected = [str(a + 1)]
        elif v == 34:
            src = ("record R { v: Int\n"
                   "  fn get(self: R) -> Int { return self.v }\n}\n"
                   "fn f(r: R) -> Int { return r.get() + 1 }\n"
                   "let y: Int = f(R(%d))\nprint y\n" % a)
            expected = [str(a + 1)]
        elif v == 35:
            src = ("record P { x: Int }\n"
                   "let p = (fn(n: Int) { return P(n) })(%d)\n"
                   "print p.x\n" % a)
            expected = [str(a)]
        elif v == 36:
            src = ("type Maybe<T> = T | Text\n"
                   "fn f(n: Int) -> Maybe<Int> { return n }\n"
                   "let x: Maybe<Int> = f(%d)\nprint x\n" % a)
            expected = [str(a)]
        elif v == 37:
            src = ("enum E { A, B }\n"
                   "type U = E | Int\n"
                   "fn f(b: Bool) -> U { if b { return E.A }\n return 3 }\n"
                   "print f(true)\nprint f(false)\n")
            expected = ["E.A", "3"]
        elif v == 38:
            src = ("fn safe(x: Int) { if x < 0 { return Err(\"bad\") }\n"
                   "  return Ok(x) }\n"
                   "fn g(x: Int) { return safe(x)? }\n"
                   "print g(%d)\nprint g(-1)\n" % a)
            expected = [str(a), "Err(bad)"]
        else:
            src = ("fn f(x: Int) { }\n"
                   "let y: Any = f(%d)\nprint y\n" % a)
            expected = ["None"]
        return src, expected

    # --------------------------------------------- record/list patterns
    def pattern_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        v = self.seed % 30
        if v == 0:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return x } "
                   "Q => { return 0 } } }\n"
                   "print f(E.P(%d))\nprint f(E.Q)\n" % a)
            expected = [str(a), "0"]
        elif v == 1:
            src = ("enum E { A, B, C }\n"
                   "fn f(e: E) -> Text { match e { A => { return \"a\" } "
                   "B => { return \"b\" } C => { return \"c\" } } }\n"
                   "print f(E.A)\nprint f(E.C)\n")
            expected = ["a", "c"]
        elif v == 2:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return x } "
                   "_ => { return 9 } } }\n"
                   "print f(E.P(%d))\nprint f(E.Q)\n" % a)
            expected = [str(a), "9"]
        elif v == 3:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { E.P(x) => { return x } "
                   "E.Q => { return 1 } } }\n"
                   "print f(E.P(%d))\nprint f(E.Q)\n" % a)
            expected = [str(a), "1"]
        elif v == 4:
            src = ("enum E { P(Int, Text), Q }\n"
                   "fn f(e: E) -> Text { match e { P(x, y) => { return y } "
                   "Q => { return \"q\" } } }\n"
                   "print f(E.P(%d, \"%s\"))\nprint f(E.Q)\n" % (a, s))
            expected = [s, "q"]
        elif v == 5:
            src = ("fn f(r: Result) -> Int { match r { Ok(x) => { return x } "
                   "Err(e) => { return -1 } } }\n"
                   "print f(Ok(%d))\nprint f(Err(\"bad\"))\n" % a)
            expected = [str(a), "-1"]
        elif v == 6:
            src = ("fn f(r: Result) -> Text { match r { Ok(x) => { "
                   "return text(x) } _ => { return \"none\" } } }\n"
                   "print f(Ok(%d))\nprint f(Err(\"e\"))\n" % a)
            expected = [str(a), "none"]
        elif v == 7:
            src = ("enum E { P(Result), Q }\n"
                   "fn f(e: E) -> Int { match e { P(r) => { match r { "
                   "Ok(x) => { return x } _ => { return -1 } } } "
                   "Q => { return 0 } } }\n"
                   "print f(E.P(Ok(%d)))\nprint f(E.P(Err(\"e\")))\n"
                   "print f(E.Q)\n" % a)
            expected = [str(a), "-1", "0"]
        elif v == 8:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Text { match e { P(x) => { "
                   "return \"p\" + text(x) } Q => { return \"q\" } } }\n"
                   "print f(E.P(%d))\n" % a)
            expected = ["p" + str(a)]
        elif v == 9:
            src = ("enum E { P(Int), Q }\n"
                   "record R { e: E\n"
                   "  fn val(self: R) -> Int { match self.e { "
                   "P(x) => { return x } Q => { return %d } } }\n}\n"
                   "let r = R(E.P(%d))\nprint r.val()\n" % (b, a))
            expected = [str(a)]
        elif v == 10:
            src = ("enum E { P(Int), Q }\n"
                   "fn mk(n: Int) { return E.P(n) }\n"
                   "fn f(e: E) { match e { P(x) => { return x } "
                   "Q => { return 0 } } }\n"
                   "let y: Int = f(mk(%d))\nprint y\n" % a)
            expected = [str(a)]
        elif v == 11:
            src = ("enum E { P(Text), Q }\n"
                   "fn f(e: E) -> Text { match e { P(x) => { return x } "
                   "Q => { return \"q\" } } }\n"
                   "print f(E.P(\"%s\"))\n" % s)
            expected = [s]
        elif v == 12:
            src = ("enum E { P(Decimal), Q }\n"
                   "fn f(e: E) -> Decimal { match e { P(x) => { return x } "
                   "Q => { return 0.5 } } }\n"
                   "print f(E.P(1.5))\nprint f(E.Q)\n")
            expected = ["1.5", "0.5"]
        elif v == 13:
            src = ("enum E { P(Bool), Q }\n"
                   "fn f(e: E) -> Bool { match e { P(x) => { return x } "
                   "Q => { return false } } }\n"
                   "print f(E.P(true))\nprint f(E.Q)\n")
            expected = ["True", "False"]
        elif v == 14:
            src = ("enum E { A(Int), B(Text), C }\n"
                   "fn f(e: E) -> Text { match e { A(x) => { "
                   "return text(x) } B(y) => { return y } "
                   "C => { return \"c\" } } }\n"
                   "print f(E.A(%d))\nprint f(E.B(\"%s\"))\nprint f(E.C)\n"
                   % (a, s))
            expected = [str(a), s, "c"]
        elif v == 15:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { "
                   "return x * 2 } Q => { return 0 } } }\n"
                   "print f(E.P(%d))\n" % a)
            expected = [str(a * 2)]
        elif v == 16:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { if true { match e { "
                   "P(x) => { return x } Q => { return 0 } } }\n"
                   "  return -1 }\n"
                   "print f(E.P(%d))\n" % a)
            expected = [str(a)]
        elif v == 17:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return x } "
                   "Q => { return 0 } } }\n"
                   "fn g(e: E) { return f(e) + 1 }\n"
                   "print g(E.P(%d))\nprint g(E.Q)\n" % a)
            expected = [str(a + 1), "1"]
        elif v == 18:
            src = ("enum E { P(Int, Text), Q(Bool) }\n"
                   "fn f(e: E) -> Text { match e { P(a, b) => { "
                   "return b + text(a) } Q(c) => { return text(c) } } }\n"
                   "print f(E.P(%d, \"%s\"))\nprint f(E.Q(true))\n" % (a, s))
            expected = [s + str(a), "True"]
        elif v == 19:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) { match e { P(x) => { return x } "
                   "Q => { return 0 } } }\n"
                   "let y: Int = f(E.P(%d))\nprint y\n" % a)
            expected = [str(a)]
        elif v == 20:
            src = ("enum E { P(Int), Q }\n"
                   "record R { e: E\n"
                   "  fn val(self: R) -> Int { match self.e { "
                   "P(x) => { print x\n return x } Q => { return 0 } } }\n}\n"
                   "let r = R(E.P(%d))\nprint r.val()\n" % a)
            expected = [str(a), str(a)]
        elif v == 21:
            src = ("enum E { P(Int), Q }\n"
                   "fn g(x: Int) -> Int { return x + 1 }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return g(x) } "
                   "Q => { return 0 } } }\n"
                   "print f(E.P(%d))\n" % a)
            expected = [str(a + 1)]
        elif v == 22:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return %d } "
                   "Q => { return %d } } }\n"
                   "print f(E.P(0))\nprint f(E.Q)\n" % (a, b))
            expected = [str(a), str(b)]
        elif v == 23:
            src = ("record P { x: Int }\n"
                   "fn f(n: Int) -> P { return P(n) }\n"
                   "let p = f(%d) with { x = %d }\nprint p.x\n" % (a, b))
            expected = [str(b)]
        elif v == 24:
            src = ("enum E { P(Int), Q }\n"
                   "record R { e: E, n: Int }\n"
                   "let r = R(E.P(%d), %d)\n"
                   "let u = r with { n = %d }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return x } "
                   "Q => { return 0 } } }\n"
                   "print f(u.e)\nprint u.n\n" % (a, b, b + 1))
            expected = [str(a), str(b + 1)]
        elif v == 25:
            src = ("enum E { P(Result), Q }\n"
                   "fn f(r: Result) -> Int { match r { Ok(x) => { "
                   "return x } Err(e) => { return -1 } } }\n"
                   "fn g(e: E) -> Int { match e { P(r) => { return f(r) } "
                   "Q => { return 0 } } }\n"
                   "print g(E.P(Ok(%d)))\nprint g(E.P(Err(\"e\")))\n" % a)
            expected = [str(a), "-1"]
        elif v == 26:
            src = ("enum E { P(Int), Q }\n"
                   "let f = fn(e: E) -> Int { match e { "
                   "P(x) => { return x } Q => { return 0 } } }\n"
                   "print f(E.P(%d))\nprint f(E.Q)\n" % a)
            expected = [str(a), "0"]
        elif v == 27:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { return x } "
                   "Q => { return %d } } }\n"
                   "fn g(e: E) -> Int { return f(e) * 2 }\n"
                   "print g(E.P(%d))\nprint g(E.Q)\n" % (b, a))
            expected = [str(a * 2), str(b * 2)]
        elif v == 28:
            src = ("enum E { P(Int, Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x, y) => { "
                   "return x + y } Q => { return 0 } } }\n"
                   "print f(E.P(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        else:
            src = ("enum E { P(Int), Q }\n"
                   "fn f(e: E) -> Int { match e { P(x) => { "
                   "return x } Q => { return 0 } } }\n"
                   "fn g(e: E) -> Bool { return f(e) > %d }\n"
                   "print g(E.P(%d))\nprint g(E.Q)\n" % (b, a))
            expected = [str(a > b), str(0 > b)]
        return src, expected

    # ---------------------------------------------------- nested with paths
    def nested_with_valid(self):
        r = self.r
        a, b, c, d = (r.randint(0, 9) for _ in range(4))
        v = self.seed % 30
        if v == 0:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d, %d), %d)\n"
                   "let r = q with { p.x = %d }\n"
                   "print r.p.x\nprint r.p.y\nprint q.p.x\n"
                   % (a, b, c, b))
            expected = [str(b), str(b), str(a)]
        elif v == 1:
            src = ("record A { v: Int }\nrecord B { a: A }\n"
                   "record C { b: B }\nlet c = C(B(A(%d)))\n"
                   "let d = c with { b.a.v = %d }\n"
                   "print d.b.a.v\nprint c.b.a.v\n" % (a, b))
            expected = [str(b), str(a)]
        elif v == 2:
            src = ("record P { v: Int }\nrecord Q { a: P, b: P, n: Int }\n"
                   "let q = Q(P(%d), P(%d), %d)\n"
                   "let r = q with { a.v = %d, b.v = %d }\n"
                   "print r.a.v\nprint r.b.v\nprint q.a.v\nprint q.b.v\n"
                   % (a, c, d, b, d))
            expected = [str(b), str(d), str(a), str(c)]
        elif v == 3:
            src = ("record A { v: Int }\nrecord B extends A { w: Int }\n"
                   "record C { b: B }\nlet c = C(B(%d, %d))\n"
                   "let d = c with { b.v = %d, b.w = %d }\n"
                   "print d.b.v\nprint d.b.w\nprint c.b.v\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a)]
        elif v == 4:
            src = ("type I = Int\nrecord P { x: I }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\nlet r = q with { p.x = %d }\n"
                   "print r.p.x\n" % (a, b))
            expected = [str(b)]
        elif v == 5:
            src = ("record Box<T> { v: T }\nrecord Q { b: Box<Int> }\n"
                   "let q = Q(Box(%d))\nlet r = q with { b.v = %d }\n"
                   "print r.b.v\n" % (a, b))
            expected = [str(b)]
        elif v == 6:
            src = ("record P { x: Int }\nrecord Q { p: P\n"
                   "  fn bump(self: Q) -> Q "
                   "{ return self with { p.x = self.p.x + 1 } }\n}\n"
                   "let q = Q(P(%d))\nlet r = q.bump()\n"
                   "print r.p.x\nprint q.p.x\n" % a)
            expected = [str(a + 1), str(a)]
        elif v == 7:
            src = ("record P { x: Int }\n"
                   "fn mk(n: Int) -> P { return P(n) }\n"
                   "let r = mk(%d) with { x = %d }\nprint r.x\n" % (a, b))
            expected = [str(b)]
        elif v == 8:
            src = ("record P { x: Int }\nrecord Q { p: P, n: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = (q with { p.x = %d }) with { n = %d }\n"
                   "print r.p.x\nprint r.n\nprint q.p.x\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a)]
        elif v == 9:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\n"
                   "let r = q with { p.x = q.p.x + %d }\nprint r.p.x\n"
                   % (a, b))
            expected = [str(a + b)]
        elif v == 10:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d, %d))\n"
                   "let r = q with { p.x = %d, p.y = %d }\n"
                   "print r.p.x\nprint r.p.y\nprint q.p.x\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a)]
        elif v == 11:
            src = ("record P { x: Int }\nrecord Q { p: P, n: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = %d, n = %d }\n"
                   "print r.p.x\nprint r.n\n" % (a, b, c, d))
            expected = [str(c), str(d)]
        elif v == 12:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d, %d))\n"
                   "let r = q with { p = q.p with { x = %d } }\n"
                   "print r.p.x\nprint r.p.y\nprint q.p.x\n" % (a, b, c))
            expected = [str(c), str(b), str(a)]
        elif v == 13:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "fn set(q: Q, x: Int) -> Q { return q with { p.x = x } }\n"
                   "let r = set(Q(P(%d)), %d)\nprint r.p.x\n" % (a, b))
            expected = [str(b)]
        elif v == 14:
            src = ("record P { x: Int\n"
                   "  fn get(self: P) -> Int { return self.x }\n}\n"
                   "record Q { p: P }\nlet q = Q(P(%d))\n"
                   "let r = q with { p.x = %d }\nprint r.p.get()\n" % (a, b))
            expected = [str(b)]
        elif v == 15:
            src = ("record P { v: Int }\nrecord Q { a: P, b: P }\n"
                   "let p = P(%d)\nlet q = Q(p, p)\n"
                   "let r = q with { a.v = %d }\n"
                   "print r.a.v\nprint r.b.v\nprint q.a.v\n" % (a, b))
            expected = [str(b), str(a), str(a)]
        elif v == 16:
            src = ("record A { v: Int, w: Int }\nrecord B { a: A }\n"
                   "record C { b: B, n: Int }\n"
                   "let c = C(B(A(%d, %d)), %d)\n"
                   "let d = c with { b.a.v = %d, b.a.w = %d, n = %d }\n"
                   "print d.b.a.v\nprint d.b.a.w\nprint d.n\nprint c.b.a.v\n"
                   % (a, b, c, b, c, a))
            expected = [str(b), str(c), str(a), str(a)]
        elif v == 17:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\n"
                   "let r = q with { p.x = q.p.x * 2 + %d }\n"
                   "print r.p.x\n" % (a, b))
            expected = [str(a * 2 + b)]
        elif v == 18:
            src = ("record P { x: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = %d, z = %d }\n"
                   "print r.p.x\nprint r.z\nprint q.z\n" % (a, b, c, d))
            expected = [str(c), str(d), str(b)]
        elif v == 19:
            src = ("record P { x: Int }\n"
                   "fn mk(n: Int) { return P(n) }\n"
                   "let r = mk(%d) with { x = %d }\nprint r.x\n" % (a, b))
            expected = [str(b)]
        elif v == 20:
            src = ("type I = Int\nrecord P { x: I }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\n"
                   "let r = q with { p.x = q.p.x + %d }\nprint r.p.x\n"
                   % (a, b))
            expected = [str(a + b)]
        elif v == 21:
            src = ("record Box<T> { v: T }\nrecord Q { b: Box<Text> }\n"
                   'let q = Q(Box("s"))\n'
                   'let r = q with { b.v = "t" }\nprint r.b.v\n')
            expected = ["t"]
        elif v == 22:
            src = ("record L3 { v: Int }\nrecord L2 { l3: L3 }\n"
                   "record L1 { l2: L2 }\nrecord L0 { l1: L1 }\n"
                   "let o = L0(L1(L2(L3(%d))))\n"
                   "let p = o with { l1.l2.l3.v = %d }\n"
                   "print p.l1.l2.l3.v\nprint o.l1.l2.l3.v\n" % (a, b))
            expected = [str(b), str(a)]
        elif v == 23:
            src = ("record Base { x: Int }\nrecord Child extends Base { y: Int }\n"
                   "record Holder { c: Child }\n"
                   "let h = Holder(Child(%d, %d))\n"
                   "let g = h with { c.x = %d }\n"
                   "print g.c.x\nprint g.c.y\nprint h.c.x\n" % (a, b, c))
            expected = [str(c), str(b), str(a)]
        elif v == 24:
            src = ("record P { x: Int }\nrecord Q { a: P, b: P }\n"
                   "let q = Q(P(%d), P(%d))\n"
                   "let r = q with { a.x = %d, b.x = %d }\n"
                   "print r.a.x\nprint r.b.x\nprint q.a.x\nprint q.b.x\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a), str(b)]
        elif v == 25:
            src = ("record P { x: Int }\nrecord Q { p: P, n: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = (q with { p.x = %d }) with { p.x = %d }\n"
                   "print r.p.x\n" % (a, b, c, d))
            expected = [str(d)]
        elif v == 26:
            src = ("record P { x: Int }\nrecord Q { p: P\n"
                   "  fn set(self: Q, x: Int) -> Q "
                   "{ return self with { p.x = x } }\n}\n"
                   "let q = Q(P(%d))\nlet r = q.set(%d)\nprint r.p.x\n"
                   % (a, b))
            expected = [str(b)]
        elif v == 27:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "fn upd(q: Q, x: Int) -> Q { return q with { p.x = x } }\n"
                   "let a = Q(P(%d))\nlet b = upd(a, %d)\n"
                   "print a.p.x\nprint b.p.x\n" % (a, b))
            expected = [str(a), str(b)]
        elif v == 28:
            src = ("record P { x: Int }\nrecord Q { p: P, n: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = q.p.x + %d, n = q.n + %d }\n"
                   "print r.p.x\nprint r.n\n" % (a, b, c, d))
            expected = [str(a + c), str(b + d)]
        else:
            src = ("record P { x: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = %d }\n"
                   "let t = r with { z = %d }\n"
                   "print r.z\nprint t.z\nprint q.z\n" % (a, b, c, d))
            expected = [str(b), str(d), str(b)]
        return src, expected

    # ---------------------------------------- intersection/union/generic aliases
    def alias_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        v = self.seed % 30
        if v == 0:
            src = ("type Maybe<T> = T | Text\n"
                   "let x: Maybe<Int> = %d\nprint x\n" % a)
            expected = [str(a)]
        elif v == 1:
            src = ('type Maybe<T> = T | Text\n'
                   'let x: Maybe<Text> = "%s"\nprint x\n' % s)
            expected = [s]
        elif v == 2:
            src = ("record Box<T> { v: T }\ntype Boxed<T> = Box<T>\n"
                   "let x: Boxed<Int> = Box(%d)\nprint x.v\n" % a)
            expected = [str(a)]
        elif v == 3:
            src = ("record Box<T> { v: T }\ntype B1<T> = Box<T>\n"
                   "type B2<T> = B1<T>\ntype B3 = B2<Int>\n"
                   "let x: B3 = Box(%d)\nprint x.v\n" % a)
            expected = [str(a)]
        elif v == 4:
            src = ("type V<T> = List<T>\n"
                   "let xs: V<Int> = [%d, %d]\nprint xs\n" % (a, b))
            expected = ["[%d, %d]" % (a, b)]
        elif v == 5:
            src = ("type V<T> = List<T>\ntype M = V<V<Int>>\n"
                   "let x: M = [[%d], [%d]]\nprint x\n" % (a, b))
            expected = ["[[%d], [%d]]" % (a, b)]
        elif v == 6:
            src = ("type Res<T> = Result<T, Text>\n"
                   "let r: Res<Int> = Ok(%d)\nprint r\n" % a)
            expected = ["Ok(%d)" % a]
        elif v == 7:
            src = ("record Pair<A, B> { a: A, b: B }\n"
                   "type P<X, Y> = Pair<X, Y>\n"
                   'let p: P<Int, Text> = Pair(%d, "%s")\n'
                   "print p.a\nprint p.b\n" % (a, s))
            expected = [str(a), s]
        elif v == 8:
            src = ("record A { x: Int }\nrecord B { y: Text }\n"
                   "type U = A | B\n"
                   'let u: U = B("%s")\nprint u.y\n' % s)
            expected = [s]
        elif v == 9:
            src = ("enum E { A(Int), B(Text) }\ntype U = E | Int\n"
                   "let x: U = E.A(%d)\nprint x\n" % a)
            expected = ["E.A(%d)" % a]
        elif v == 10:
            src = ("type I = Int\ntype L = List<I>\n"
                   "fn total(xs: L) -> Int {\n"
                   "  let s = 0\n  for x in xs { s := s + x }\n"
                   "  return s\n}\n"
                   "print total([%d, %d])\n" % (a, b))
            expected = [str(a + b)]
        elif v == 11:
            src = ("type I = Int\nrecord P { x: I }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\nlet r = q with { p.x = %d }\n"
                   "print r.p.x\n" % (a, b))
            expected = [str(b)]
        elif v == 12:
            src = ("record Box<T> { v: T }\ntype B<T> = Box<Box<T>>\n"
                   "let x: B<Int> = Box(Box(%d))\nprint x.v.v\n" % a)
            expected = [str(a)]
        elif v == 13:
            flag = ("true", "false")[r.randrange(2)]
            src = ("type U = Bool | Int\nlet x: U = %s\nprint x\n" % flag)
            expected = [flag.capitalize()]
        elif v == 14:
            src = "type U = Decimal | Int\nlet x: U = 1.5\nprint x\n"
            expected = ["1.5"]
        elif v == 15:
            src = ("type Res<T> = Result<T, Text>\n"
                   "fn good(n: Int) -> Res<Int> { return Ok(n) }\n"
                   "print good(%d)\n" % a)
            expected = ["Ok(%d)" % a]
        elif v == 16:
            src = ("type Num = Int | Text\n"
                   "fn pick(b: Bool) -> Num {\n"
                   '  if b { return %d }\n  return "%s"\n}\n'
                   "print pick(true)\nprint pick(false)\n" % (a, s))
            expected = [str(a), s]
        elif v == 17:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\ntype AA = A\ntype BB = B\n"
                   "fn f<T: AA & BB>(v: T) -> Int { return v.x + v.y }\n"
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        elif v == 18:
            src = ("type R = Result\n"
                   "fn f(x: Int) -> R { if x < 0 { return Err(\"no\") }\n"
                   "  return Ok(x) }\nprint f(%d)\nprint f(-1)\n" % a)
            expected = ["Ok(%d)" % a, "Err(no)"]
        elif v == 19:
            src = ("type L<T> = List<T>\n"
                   "fn total(xs: L<Int>) -> Int { let s = 0\n"
                   "  for x in xs { s := s + x }\n  return s }\n"
                   "print total([%d, %d, %d])\n" % (a, b, a))
            expected = [str(a + b + a)]
        elif v == 20:
            src = ("enum E { A(Int), B }\ntype T = E\n"
                   "fn f(e: T) -> Int { match e { A(x) => { return x } "
                   "B => { return 0 } } }\n"
                   "print f(E.A(%d))\nprint f(E.B)\n" % a)
            expected = [str(a), "0"]
        elif v == 21:
            src = ("type I = Int\nrecord Inner { v: I }\n"
                   "record Outer { i: Inner }\n"
                   "let o = Outer(Inner(%d))\n"
                   "let p = o with { i.v = %d }\n"
                   "print p.i.v\nprint o.i.v\n" % (a, b))
            expected = [str(b), str(a)]
        elif v == 22:
            src = ("record Box<T> { v: T }\ntype I = Int\n"
                   "type BI = Box<I>\ntype BBI = Box<BI>\n"
                   "let x: BBI = Box(Box(%d))\nprint x.v.v\n" % a)
            expected = [str(a)]
        elif v == 23:
            src = ("enum E { A(Int), B }\ntype U = E | Int\n"
                   "fn pick(b: Bool) -> U { if b { return E.A(%d) }\n"
                   "  return %d }\n"
                   "print pick(true)\nprint pick(false)\n" % (a, b))
            expected = ["E.A(%d)" % a, str(b)]
        elif v == 24:
            src = ("type N = Int | Decimal\n"
                   "fn f(b: Bool) -> N { if b { return %d }\n return 1.5 }\n"
                   "print f(true)\nprint f(false)\n" % a)
            expected = [str(a), "1.5"]
        elif v == 25:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\ntype AA = A\ntype BB = B\n"
                   "type CC = C\n"
                   "fn f<T: AA & BB>(v: T) -> Int { return v.x + v.y }\n"
                   "let c: CC = C(%d, %d)\nprint f(c)\n" % (a, b))
            expected = [str(a + b)]
        elif v == 26:
            src = ("type V<T> = List<T>\ntype VI = V<Int>\ntype VVI = V<VI>\n"
                   "let x: VVI = [[%d], [%d]]\nprint x\n" % (a, b))
            expected = ["[[%d], [%d]]" % (a, b)]
        elif v == 27:
            src = ("record Box<T> { v: T }\ntype BI = Box<Int>\n"
                   "fn mk(n: Int) -> BI { return Box(n) }\n"
                   "let b = mk(%d)\nprint b.v\n" % a)
            expected = [str(a)]
        elif v == 28:
            src = ("record Box<T> { v: T }\nrecord A { x: Int }\n"
                   "type U = Box<Int> | A\n"
                   "let u: U = Box(%d)\nprint u.v\n" % a)
            expected = [str(a)]
        else:
            src = ("type I = Int\nrecord P { x: I }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = %d, z = %d }\n"
                   "print r.p.x\nprint r.z\n" % (a, b, b, a))
            expected = [str(b), str(a)]
        return src, expected

    # ------------------------------------------------------- formatter
    def fmt_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        v = self.seed % 30
        if v < 10:
            src = _Gen9(40000 + self.seed).inference_valid()[0]
        elif v < 16:
            src = _Gen9(41000 + self.seed).pattern_valid()[0]
            for bump in range(1, 12):
                if not re.search(r"[A-Z]\([^)]*,", src):
                    break
                src = _Gen9(41000 + self.seed + bump).pattern_valid()[0]
        elif v < 22:
            src = _Gen9(42000 + self.seed).nested_with_valid()[0]
        elif v < 28:
            src = _Gen9(43000 + self.seed).alias_valid()[0]
        elif v == 28:
            src = ("# lead\nfn f(x: Int) { # body\n"
                   "  return x + 1 # tail\n}\nprint f(%d)\n" % a)
        else:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d, %d), %d)\n"
                   "let r = q with {\n  # c1\n  p.x = %d,\n"
                   "  p.y = %d, # c2\n  z = %d\n}\n"
                   "print r.p.x\nprint r.p.y\nprint r.z\n"
                   % (a, b, a, b, a, b))
        marker = "# round9-%d { } # = + -\n" % r.randrange(10 ** 6)
        return marker + src


# --------------------------------------------------------- mutation tools
def _inference_seed(seed):
    r = random.Random(seed)
    return ("fn f(x: Int) { return x + %d }\n"
            "fn g(x: Int) -> Int effects { io.print } "
            "{ print x\n return x }\n"
            "print f(%d)\nprint g(%d)\n"
            % (r.randint(1, 9), r.randint(1, 9), r.randint(1, 9)))


def _pattern_seed(seed):
    r = random.Random(seed)
    return ("enum E { P(Int), Q }\n"
            "fn f(e: E) -> Int { match e { P(x) => { return x } "
            "Q => { return 0 } } }\n"
            "print f(E.P(%d))\nprint f(E.Q)\n" % r.randint(1, 9))


def _with_seed(seed):
    r = random.Random(seed)
    return ("record P { x: Int, y: Int }\nrecord Q { p: P, z: Int }\n"
            "let q = Q(P(%d, %d), %d)\n"
            "let r = q with { p.x = %d, z = %d }\n"
            "print r.p.x\nprint r.z\n"
            % (r.randint(1, 9), r.randint(1, 9), r.randint(1, 9),
               r.randint(1, 9), r.randint(1, 9)))


def _alias_seed(seed):
    r = random.Random(seed)
    return ("record Box<T> { v: T }\n"
            "type Boxed<T> = Box<T>\n"
            "type Maybe<T> = T | Text\n"
            "let x: Maybe<Int> = %d\n"
            "let b: Boxed<Int> = Box(%d)\n"
            "print x\nprint b.v\n" % (r.randint(1, 9), r.randint(1, 9)))


def _infer_bad_return_type(src):
    return src.replace("fn f(x: Int) {", "fn f(x: Int) -> Text {", 1)


def _infer_bad_effect_braces(src):
    return src.replace("effects { io.print }", "effects io.print", 1)


def _infer_missing_return_expr(src):
    return re.sub(r"return x \+ \d+", "return", src, count=1)


def _infer_missing_param_type(src):
    return src.replace("x: Int", "x:", 1)


def _infer_duplicate_return_conflict(src):
    return src.replace("return x + ", "return \"s\" + ", 1)


def _infer_missing_fn_keyword(src):
    return src.replace("fn g(", "g(", 1)


def _pattern_bad_arm(src):
    return src.replace("P(x) => { return x }", "P => { return x }", 1)


def _pattern_unknown_variant(src):
    return src.replace("Q => { return 0 }", "Z => { return 0 }", 1)


def _pattern_arity(src):
    return src.replace("P(x) => { return x }", "P(x, y) => { return x }", 1)


def _pattern_missing_close(src):
    return src.replace("Q => { return 0 } } }\n", "Q => { return 0 } }\n", 1)


def _pattern_dup_arm(src):
    return src.replace("Q => { return 0 }", "Q(x) => { return 0 }", 1)


def _pattern_bad_subject(src):
    return src.replace("fn f(e: E)", "fn f(e: Int)", 1)


def _with_missing_eq(src):
    return src.replace("p.x =", "p.x", 1)


def _with_unknown_leaf(src):
    return src.replace("p.x =", "p.z =", 1)


def _with_nonrecord_path(src):
    return src.replace("p.x =", "p.x.y =", 1)


def _with_type_mismatch(src):
    return src.replace("p.x =", 'p.x = "bad", _ =', 1)


def _with_duplicate(src):
    return src.replace("p.x =", "p.x = 1, p.x =", 1)


def _with_prefix_conflict(src):
    return src.replace("p.x =", "p = P(1, 2), p.x =", 1)


def _with_missing_value(src):
    return src.replace("p.x =", "p.x =,", 1)


def _with_bad_kw(src):
    return src.replace("q with {", "q wiht {", 1)


def _with_unknown_field(src):
    return src.replace("z =", "q =", 1)


def _with_leading_dot(src):
    return src.replace("p.x =", ".p.x =", 1)


def _with_missing_base(src):
    return src.replace("let r = q with", "let r = with", 1)


def _with_nonrecord_base(src):
    return src.replace("let r = q with", "let r = 1 with", 1)


def _alias_bad_kw(src):
    return src.replace("type Boxed", "tpye Boxed", 1)


def _alias_empty_params(src):
    return src.replace("Boxed<T>", "Boxed<>", 1)


def _alias_unterminated(src):
    return src.replace("= Box<T>", "= Box<T", 1)


def _alias_trailing_pipe(src):
    return src.replace("= T | Text", "= T |", 1)


def _alias_leading_pipe(src):
    return src.replace("= T | Text", "= | T", 1)


def _alias_duplicate_branch(src):
    return src.replace("= T | Text", "= T | T", 1)


def _alias_arity(src):
    return src.replace("Maybe<Int>", "Maybe<Int, Int>", 1)


def _alias_missing_args(src):
    return src.replace("Maybe<Int>", "Maybe", 1)


def _alias_duplicate(src):
    return src.replace(
        "type Maybe<T> = T | Text\n",
        "type Maybe<T> = T | Text\ntype Maybe<T> = T | Text\n", 1)


def _alias_target_arity(src):
    return src.replace("= Box<T>", "= Box", 1)


def _alias_unknown_target(src):
    return src.replace("= Box<T>", "= Nope<T>", 1)


_INFER_MUTATORS = (
    _infer_bad_return_type,
    _infer_bad_effect_braces,
    _infer_missing_return_expr,
    _infer_missing_param_type,
    _infer_duplicate_return_conflict,
    _infer_missing_fn_keyword,
)

_PATTERN_MUTATORS = (
    _pattern_bad_arm,
    _pattern_unknown_variant,
    _pattern_arity,
    _pattern_missing_close,
    _pattern_dup_arm,
    _pattern_bad_subject,
)

_WITH_MUTATORS = (
    _with_missing_eq,
    _with_unknown_leaf,
    _with_nonrecord_path,
    _with_type_mismatch,
    _with_duplicate,
    _with_prefix_conflict,
    _with_missing_value,
    _with_bad_kw,
    _with_unknown_field,
    _with_leading_dot,
    _with_missing_base,
    _with_nonrecord_base,
)

_ALIAS_MUTATORS = (
    _alias_bad_kw,
    _alias_empty_params,
    _alias_unterminated,
    _alias_trailing_pipe,
    _alias_leading_pipe,
    _alias_duplicate_branch,
    _alias_arity,
    _alias_missing_args,
    _alias_duplicate,
    _alias_target_arity,
    _alias_unknown_target,
)


# ------------------------------------------------------ static rejects
_INFERENCE_REJECT = (
    'fn f(x: Int) { if x > 0 { return 1 } return "s" }\nprint f(1)\n',
    "fn f(x: Int) -> Text { return x + 1 }\nprint f(1)\n",
    "let y: Int = (fn(x: Int) { })(1)\nprint y\n",
    "fn f(n: Int) { }\nlet y: Int = f(1)\nprint y\n",
    "fn f(n: Int) { return n }\nlet y: Int = f()\nprint y\n",
    "fn f(n: Int) { return n }\nlet y: Int = f(1, 2)\nprint y\n",
    "fn g(n: Int) -> Int effects {} { print n\n return n }\nprint g(1)\n",
    "fn g(n: Int) -> Int effects { io.print } { return n }\n"
    "fn f(n: Int) -> Int effects {} { return g(n) }\nprint f(1)\n",
    "fn fact(n: Int) { return fact(n - 1) }\nlet y: Int = fact(3)\n",
    "fn f(x: Int) { return x + }\nprint f(1)\n",
)

_PATTERN_REJECT = (
    "enum E { P(Int), Q }\nfn f(e: E) -> Int { match e { "
    "P => { return 1 } } }\nprint f(E.P(1))\n",
    "enum E { P(Int), Q }\nfn f(e: E) -> Int { match e { "
    "P(x, y) => { return 1 } } }\nprint f(E.P(1))\n",
    "enum E { P(Int), Q }\nfn f(e: E) -> Int { match e { "
    "Z => { return 1 } } }\nprint f(E.P(1))\n",
    "enum E { P(Int), Q }\nfn f(e: E) -> Int { match e { "
    '"q" => { return 1 } } }\nprint f(E.P(1))\n',
    "enum E { P(Int), Q }\nfn f(e: Int) -> Int { match e { "
    "P(x) => { return x } } }\nprint f(1)\n",
    "enum E { P(Int), Q }\nfn f(e: E) -> Int { match e { "
    "P(x) => { return y } } }\nprint f(E.P(1))\n",
    "enum E { P(Int), Q }\nfn f(e: E) -> Int { match e { "
    "P(x) => { return x } Q(x) => { return 1 } } }\nprint f(E.Q)\n",
    "fn f(b: Bool) -> Int { match b { _ => { return 1 } } }\n"
    "print f(true)\n",
    "fn f(x: Int) -> Int { match x { 1 => { return 1 } _ => { return 0 } } }\n",
)

_ALIAS_REJECT = (
    "type U = Int |\n",
    "type U = | Int\n",
    "type U = \n",
    "type U = Int | Int\n",
    "type U = Int | Nope\n",
    "type U = Int | Text | Int\n",
    "type Maybe<T> = T | T\n",
    "type Maybe<T> = Missing<T> | Text\n",
    "type I = Int & Text\n",
    "type A = B\ntype B = A\n",
    "type A = A\n",
    "type A = Int\ntype A = Text\n",
    "record P { x: Int }\ntype P = Int\n",
    "enum C { A }\ntype C = Int\n",
    "type Int = Text\n",
    "type A<T> = Int\nlet x: A<Int, Int> = 1\n",
    "type A<T> = Int\nlet x: A = 1\n",
    "type A<T, T> = Int\n",
    "fn f() -> Int { type A = Int\n return 1 }\n",
    "record Box<T> { v: T }\ntype Boxed<T> = Box<T>\n"
    "let x: Boxed<Int> = Box(1)\nprint x.v\nlet y: Boxed = Box(2)\n",
)

_WITH_REJECT = (
    "record P { x: Int }\nrecord Q { p: P }\nlet q = Q(P(1))\n"
    "let r = q with { p.z = 9 }\n",
    "record P { x: Int }\nrecord Q { p: P }\nlet q = Q(P(1))\n"
    "let r = q with { p.x.y = 9 }\n",
    "record P { x: Int }\nrecord Q { p: P }\nlet q = Q(P(1))\n"
    'let r = q with { p.x = "s" }\n',
    "record P { x: Int }\nrecord Q { p: P }\nlet q = Q(P(1))\n"
    "let r = q with { p.x = 2, p.x = 3 }\n",
    "record P { x: Int }\nrecord Q { p: P }\nlet q = Q(P(1))\n"
    "let r = q with { p = P(2), p.x = 3 }\n",
    "record P { x: Int }\nlet q = P(1)\nlet r = q with { z = 2 }\n",
    "record P { x: Int }\nlet q = P(1)\nlet r = q with { x 2 }\n",
    "record P { x: Int }\nlet q = P(1)\nlet r = q with { x = 2\n",
    "record P { x: Int }\nlet q = with { x = 2 }\n",
    "record P { x: Int }\nlet q = 1 with { x = 2 }\n",
    "record P { x: Int }\nlet q = missing with { x = 2 }\n",
    "record P { x: Int }\nlet q = P(1)\nlet r = q wiht { x = 2 }\n",
)


# ---------------------------------------------------------- helpers
def _outcome(fn):
    try:
        return ("ok", tuple(fn().out))
    except G.GamaError as ex:
        return ("error", str(ex))
    except Exception as ex:  # noqa: BLE001 - the actual invariant
        return ("leak", "%s: %s" % (type(ex).__name__, ex))


def _assert_program(test, src, expected, label):
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
        got = _outcome(lambda: G.run(src))
        if got[0] != "ok":
            test.fail("%s: interpreter outcome %r\n%s" % (label, got, src))
            return
        ir = _outcome(lambda: IR.run_program(ast))
        if ir[0] == "leak":
            test.fail("%s: IR leaked %s\n%s" % (label, ir[1], src))
            return
        if ir[0] == "error":
            test.fail("%s: IR rejected a supported case: %s\n%s"
                      % (label, ir[1], src))
            return
    test.assertEqual(list(got[1]), expected,
                     "%s: interpreter output\n%s" % (label, src))
    test.assertEqual(list(ir[1]), expected,
                     "%s: IR output differs\n%s" % (label, src))


def _assert_file(test, lib, main, expected, label):
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "lib.gm").write_text(lib, encoding="utf-8")
        (root / "main.gm").write_text(main, encoding="utf-8")
        with _env():
            it = _outcome(lambda: G.run_file(str(root / "main.gm")))
            if it[0] == "leak":
                test.fail("%s: interpreter leaked %s\n%s\n%s"
                          % (label, it[1], lib, main))
                return
            if it[0] == "error":
                test.fail("%s: interpreter failed: %s\n%s\n%s"
                          % (label, it[1], lib, main))
                return
            ir = _outcome(lambda: IR.run_file(str(root / "main.gm")))
    test.assertEqual(list(it[1]), expected,
                     "%s: interpreter output" % label)
    if ir[0] == "leak":
        test.fail("%s: IR leaked %s\n%s\n%s" % (label, ir[1], lib, main))
        return
    if ir[0] == "error":
        test.fail("%s: IR failed or rejected a supported case: %s\n%s\n%s"
                  % (label, ir[1], lib, main))
        return
    test.assertEqual(list(ir[1]), expected,
                     "%s: IR output differs" % label)


def _assert_formatter(test, src, label):
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


def _assert_gama_error(test, src, label):
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


# ------------------------------------------------------------ case tables
_N_INFERENCE = 40
_N_PATTERN = 30
_N_WITH = 30
_N_ALIAS = 30
_N_FMT = 30
_N_INFER_MUT = 12
_N_PATTERN_MUT = 12
_N_WITH_MUT = 12
_N_ALIAS_MUT = 12

_INFERENCE_CASES = [_Gen9(50000 + i).inference_valid()
                    for i in range(_N_INFERENCE)]
_PATTERN_CASES = [_Gen9(51000 + i).pattern_valid()
                  for i in range(_N_PATTERN)]
_WITH_CASES = [_Gen9(52000 + i).nested_with_valid()
               for i in range(_N_WITH)]
_ALIAS_CASES = [_Gen9(53000 + i).alias_valid() for i in range(_N_ALIAS)]
_FMT_CASES = [_Gen9(54000 + i).fmt_valid() for i in range(_N_FMT)]

_MUTATION_CASES = (
    [_INFER_MUTATORS[i % len(_INFER_MUTATORS)](_inference_seed(55000 + i))
     for i in range(_N_INFER_MUT)]
    + [_PATTERN_MUTATORS[i % len(_PATTERN_MUTATORS)](_pattern_seed(56000 + i))
       for i in range(_N_PATTERN_MUT)]
    + [_WITH_MUTATORS[i % len(_WITH_MUTATORS)](_with_seed(57000 + i))
       for i in range(_N_WITH_MUT)]
    + [_ALIAS_MUTATORS[i % len(_ALIAS_MUTATORS)](_alias_seed(58000 + i))
       for i in range(_N_ALIAS_MUT)]
)

_FILE_CASES = (
    ("fn f(x: Int) { return x + 1 }\n"
     "record P { x: Int }\n"
     "fn mk(n: Int) { return P(n) }\n"
     "export { f, P, mk }\n",
     'import "lib.gm" as m\n'
     'let p: m.P = m.mk(4)\nprint p.x\nprint m.f(2)\n',
     ["4", "3"]),
    ("record Box<T> { v: T }\n"
     "type Boxed<T> = Box<T>\n"
     "type Maybe<T> = T | Text\n"
     "fn mk(n: Int) -> Maybe<Int> { return n }\n"
     "export { Box, Boxed, Maybe, mk }\n",
     'import "lib.gm" as m\n'
     'let x: m.Maybe<Int> = m.mk(4)\n'
     'let b: m.Boxed<Int> = m.Box(5)\nprint x\nprint b.v\n',
     ["4", "5"]),
    ("record P { x: Int }\nrecord Q { p: P }\n"
     "fn mk(n: Int) -> Q { return Q(P(n)) }\n"
     "export { P, Q, mk }\n",
     'import "lib.gm" as m\n'
     'let r = m.mk(1) with { p.x = 9 }\nprint r.p.x\n',
     ["9"]),
    ("enum E { P(Int), Q }\n"
     "fn f(e: E) -> Int { match e { P(x) => { return x } "
     "Q => { return 0 } } }\n"
     "fn mk(n: Int) { return E.P(n) }\n"
     "export { E, f, mk }\n",
     'import "lib.gm" as m\n'
     'print m.f(m.mk(7))\nprint m.f(m.E.Q)\n',
     ["7", "0"]),
)

_TOTAL_CASES = (
    _N_INFERENCE + _N_PATTERN + _N_WITH + _N_ALIAS + _N_FMT
    + len(_MUTATION_CASES) + len(_INFERENCE_REJECT) + len(_PATTERN_REJECT)
    + len(_ALIAS_REJECT) + len(_WITH_REJECT) + len(_FILE_CASES)
)


# ----------------------------------------------------------- test classes
class Fuzz9MetaTests(unittest.TestCase):
    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_generators_are_deterministic(self):
        for i in range(12):
            self.assertEqual(_Gen9(60000 + i).inference_valid(),
                             _Gen9(60000 + i).inference_valid())
            self.assertEqual(_Gen9(61000 + i).pattern_valid(),
                             _Gen9(61000 + i).pattern_valid())
            self.assertEqual(_Gen9(62000 + i).nested_with_valid(),
                             _Gen9(62000 + i).nested_with_valid())
            self.assertEqual(_Gen9(63000 + i).alias_valid(),
                             _Gen9(63000 + i).alias_valid())
            self.assertEqual(_Gen9(64000 + i).fmt_valid(),
                             _Gen9(64000 + i).fmt_valid())

    def test_all_generated_valid_cases_check(self):
        groups = (
            ("inference", _INFERENCE_CASES),
            ("pattern", _PATTERN_CASES),
            ("with", _WITH_CASES),
            ("alias", _ALIAS_CASES),
        )
        for name, cases in groups:
            for i, case in enumerate(cases):
                src = case[0]
                with self.subTest(group=name, i=i):
                    try:
                        with _env():
                            G.check(src)
                    except G.GamaError as ex:
                        self.fail("%s[%d] invalid: %s\n%s"
                                  % (name, i, ex, src))
                    except Exception as ex:  # noqa: BLE001
                        self.fail("%s[%d] leaked %s: %s\n%s"
                                  % (name, i, type(ex).__name__, ex, src))


class GeneratedInferenceTests(unittest.TestCase):
    def test_generated_inference(self):
        for i, (src, expected) in enumerate(_INFERENCE_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "inference[%d]" % i)


class GeneratedPatternTests(unittest.TestCase):
    def test_generated_patterns(self):
        for i, (src, expected) in enumerate(_PATTERN_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "pattern[%d]" % i)


class GeneratedNestedWithTests(unittest.TestCase):
    def test_generated_nested_with_paths(self):
        for i, (src, expected) in enumerate(_WITH_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "with[%d]" % i)


class GeneratedAliasTests(unittest.TestCase):
    def test_generated_aliases(self):
        for i, (src, expected) in enumerate(_ALIAS_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "alias[%d]" % i)


class GeneratedFormatterTests(unittest.TestCase):
    def test_generated_formatter_roundtrips(self):
        for i, src in enumerate(_FMT_CASES):
            with self.subTest(i=i):
                _assert_formatter(self, src, "fmt[%d]" % i)


class GeneratedFileTests(unittest.TestCase):
    def test_generated_file_cases(self):
        for i, (lib, main, expected) in enumerate(_FILE_CASES):
            with self.subTest(i=i):
                _assert_file(self, lib, main, expected, "file[%d]" % i)


class GeneratedRejectTests(unittest.TestCase):
    def test_inference_rejections(self):
        for i, src in enumerate(_INFERENCE_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "inference-reject[%d]" % i)

    def test_pattern_rejections(self):
        for i, src in enumerate(_PATTERN_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "pattern-reject[%d]" % i)

    def test_alias_rejections(self):
        for i, src in enumerate(_ALIAS_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "alias-reject[%d]" % i)

    def test_with_rejections(self):
        for i, src in enumerate(_WITH_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "with-reject[%d]" % i)

    def test_mutations_are_gama_errors(self):
        for i, src in enumerate(_MUTATION_CASES):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "mutation[%d]" % i)


class PatternSurfaceProbeTests(unittest.TestCase):
    """Probe the documented pattern surface instead of skipping it.

    Enum variants (nullary, one and multi payload, qualified), Result arms and
    ``_`` fallbacks execute; record destructuring, list literal patterns and
    arm guards are rejected today (README: records cannot be destructured).
    """

    def test_enum_nullary_and_payload_patterns(self):
        _assert_program(
            self,
            "enum E { A, B(Int), C(Int, Text) }\n"
            "fn f(e: E) -> Text { match e { A => { return \"a\" } "
            "B(x) => { return text(x) } C(x, y) => { return y } } }\n"
            "print f(E.A)\nprint f(E.B(2))\nprint f(E.C(3, \"c\"))\n",
            ["a", "2", "c"], "probe-enum-patterns")

    def test_result_patterns_and_wildcard(self):
        _assert_program(
            self,
            "fn f(r: Result) -> Int { match r { Ok(x) => { return x } "
            "_ => { return -1 } } }\n"
            "print f(Ok(5))\nprint f(Err(\"e\"))\n",
            ["5", "-1"], "probe-result-patterns")

    def test_record_destructuring_is_supported(self):
        src = ("record P { x: Int }\n"
               "fn f(p: P) -> Int { match p { P(x) => { return x } } }\n"
               "print f(P(1))\n")
        self.assertEqual(G.run(src).out, ["1"])

    def test_list_literal_pattern_is_supported(self):
        src = ("fn f(xs: List) -> Int { match xs { "
               "[] => { return 0 } _ => { return 1 } } }\nprint f([])\n")
        self.assertEqual(G.run(src).out, ["0"])

    def test_match_guard_is_supported(self):
        src = ("fn f(x: Int) -> Int { match x { "
               "y if y > 0 => { return 1 } _ => { return 0 } } }\n"
               "print f(1)\n")
        self.assertEqual(G.run(src).out, ["1"])


class FeatureProbeTests(unittest.TestCase):
    def test_return_inference_is_visible_to_callers(self):
        _assert_program(
            self,
            "fn f(x: Int) { return x + 1 }\n"
            "let y: Int = f(2)\nprint y\n",
            ["3"], "probe-return-inference")

    def test_effect_inference_is_visible_to_callers(self):
        _assert_program(
            self,
            "fn g(x: Int) -> Int { print x\n return x + 1 }\n"
            "fn f(x: Int) { return g(x) }\nprint f(2)\n",
            ["2", "3"], "probe-effect-inference")

    def test_effect_inference_inside_methods(self):
        _assert_program(
            self,
            "record R { v: Int\n"
            "  fn run(self: R) -> Int { print self.v\n return self.v }\n}\n"
            "fn f(r: R) { return r.run() }\nprint f(R(4))\n",
            ["4", "4"], "probe-method-effect-inference")

    def test_inference_through_forward_reference(self):
        _assert_program(
            self,
            "fn a(n: Int) { return b(n) }\n"
            "fn b(n: Int) { return n + 2 }\n"
            "let y: Int = a(1)\nprint y\n",
            ["3"], "probe-forward-inference")


class Fuzz9EffectRegressionPins(unittest.TestCase):
    """Minimal pins for genuine effect-checking gaps in this worktree.

    Defect 1 (unsound): an explicit ``effects {}`` contract is enforced when a
    lambda is called directly or a named function is called by name, but not
    when a function value is first bound to a local (``let f = g``) and then
    called.  ``h`` claims no effects while its call chain prints.

    Defect 2 (unsound, same root): binding a lambda with declared effects to a
    local and calling it also bypasses the enclosing contract.

    Defect 3 (over-approximation): effect inference resolves method receivers
    from parameters and annotated locals, but not from a constructor/call
    initialiser without an annotation (``let b = B(1)``).  It then falls back
    to every method with that name, so an unrelated printing method makes a
    pure function look effectful and a valid ``effects {}`` caller is rejected.

    Each test fails on the current core and is pinned with
    ``expectedFailure``; an unexpected success means the gap was fixed.
    """

    def test_named_function_value_effect_contract_is_enforced(self):
        src = ("fn g(x: Int) -> Int effects { io.print } "
               "{ print x\n return x }\n"
               "fn h(x: Int) -> Int effects {} { let f = g\n"
               "  return f(x) }\nprint h(1)\n")
        with self.assertRaises(G.GamaEffectError):
            G.check(src)

    def test_lambda_value_effect_contract_is_enforced(self):
        src = ("let g = fn(x: Int) -> Int effects { io.print } "
               "{ print x\n return x }\n"
               "fn h(x: Int) -> Int effects {} { return g(x) }\n"
               "print h(1)\n")
        with self.assertRaises(G.GamaEffectError):
            G.check(src)

    def test_pure_method_on_constructor_local_is_not_over_approximated(self):
        src = ("record A { v: Int\n"
               "  fn get(self: A) -> Int { print self.v\n return self.v }\n}\n"
               "record B { v: Int\n"
               "  fn get(self: B) -> Int { return self.v }\n}\n"
               "fn f() -> Int { let b = B(1)\n return b.get() }\n"
               "fn h() -> Int effects {} { return f() }\nprint h()\n")
        G.check(src)
        self.assertEqual(list(G.run(src).out), ["1"])


class Fuzz9FormatterRegressionPins(unittest.TestCase):
    """Minimal pin for a real formatter crash found by this round.

    ``gama_fmt`` assumes a match arm payload binding is a single string when
    rendering an inline block (``gama_fmt.py:2792``).  A multi-payload enum
    arm such as ``P(x, y) => { ... }`` stores a list there, so formatting a
    valid program raises a bare ``TypeError`` instead of either formatting or
    a ``GamaError``.  The source checks and both backends run; only the
    formatter is broken.
    """

    def test_formatter_inlines_multi_payload_match_arm(self):
        src = ("enum E { P(Int, Text), Q }\n"
               "fn f(e: E) -> Text { match e { P(x, y) => { return y } "
               "Q => { return \"q\" } } }\n"
               'print f(E.P(1, "a"))\n')
        with _env():
            G.check(src)
            out = gama_fmt.format_source(src)
            self.assertEqual(gama_fmt.format_source(out), out)


if __name__ == "__main__":
    unittest.main()
