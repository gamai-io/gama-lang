"""Deterministic property/fuzz round 8 for the current GAMA-L 2.0 surface.

Rounds 1-7 covered the core, builtins/imports/limits, formatter/IR parity,
structural records, bounds, prove/verify, record methods, nominal inheritance,
export enums/renames, runtime-limit parity, selective re-exports, type aliases,
generic/union aliases and nested ``with`` expressions.

This round re-samples the newest surface with fresh seeds:

* intersection generic bounds (``fn f<T: A & B>``), including aliased and
  inherited components, structural nested records, three-way intersections,
  explicit type arguments, defaults, effects and formatter/IR erasure;
* nested record ``with`` paths (``o with { inner.x = 9 }``) with sibling
  merges, deep paths, inherited/generic/aliased fields, method bodies,
  call receivers, chained parenthesised updates, evaluation order and IR
  parity;
* generic and union aliases after the substitution fixes: bare type
  parameters in unions, same-generic-different-args branches, generic alias
  branches inside generic union aliases, chains, builtin targets and aliases
  crossing records, enums, functions, fields and ``with`` paths;
* return/effect inference probes: omitted return types and omitted effect
  sets are exercised directly (never skipped), with pinned expectations for
  genuine inference gaps at the bottom;
* formatter round-trips for every new construct;
* malformed mutations of intersection/nested-``with``/alias/inference sources
  that must stay inside the ``GamaError`` family;
* selective re-export lists crossing the file loader with intersection bounds,
  nested paths and generic/union aliases, with interpreter/IR parity.

Invariants exercised:

* Generated *valid* sources raise only ``GamaError`` from
  ``parse``/``check``/``run``; a bare Python exception is a failure.
* IR parity holds for intersection bounds, nested ``with`` paths and aliases;
  an unexpected ``IRUnsupported`` for a supported case is a failure.
* Malformed mutations fail the parser/checker with ``GamaError``.
* The formatter is idempotent, keeps the program checking, and preserves the
  reference interpreter output.

Everything is seeded.  Inference gaps (return-type inference and effect
inference) are pinned as minimal ``expectedFailure`` tests instead of being
skipped; an unexpected success means the core gained the feature.
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


class _Gen8:
    """Seeded generator for round-8 cases."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    # -------------------------------------------------- intersection bounds
    def intersection_valid(self):
        r = self.r
        a, b, c = r.randint(0, 9), r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        variant = r.randrange(18)
        if variant == 0:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 1:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
                   "print f<C>(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 2:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   'record C { x: Int, y: Int, z: Text }\n'
                   "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
                   'print f(C(%d, %d, "%s"))\n' % (a, b, s))
            expected = [str(a + b)]
        elif variant == 3:
            src = ("record A { a: Int }\nrecord B { b: Int }\n"
                   "record C { c: Int }\nrecord D { a: Int, b: Int, c: Int }\n"
                   "fn f<T: A & B & C>(v: T) -> Int "
                   "{ return v.a + v.b + v.c }\n"
                   "print f(D(%d, %d, %d))\n" % (a, b, c))
            expected = [str(a + b + c)]
        elif variant == 4:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\ntype AA = A\ntype BB = B\n"
                   "fn f<T: AA & BB>(v: T) -> Int { return v.x + v.y }\n"
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 5:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T, n: Int = %d) -> Int "
                   "{ return v.x + v.y + n }\n"
                   "print f(C(%d, %d))\n" % (c, a, b))
            expected = [str(a + b + c)]
        elif variant == 6:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int effects { io.print } "
                   "{ print v.x\n return v.x + v.y }\n"
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a), str(a + b)]
        elif variant == 7:
            src = ("record Inner { x: Int }\nrecord A { inner: Inner }\n"
                   "record B { other: Int }\n"
                   "record C { inner: Inner, other: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int "
                   "{ return v.inner.x + v.other }\n"
                   "print f(C(Inner(%d), %d))\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 8:
            src = ("record Base { x: Int }\nrecord B { y: Int }\n"
                   "record C extends Base { y: Int }\n"
                   "fn f<T: Base & B>(v: T) -> Int { return v.x + v.y }\n"
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 9:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B, U: B>(a: T, b: U) -> Int "
                   "{ return a.x + a.y + b.y }\n"
                   "print f(C(%d, %d), C(%d, %d))\n" % (a, b, c, b + 1))
            expected = [str(a + b + b + 1)]
        elif variant == 10:
            src = ("record Box<T> { v: T }\n"
                   "fn f<T: Box>(v: T) -> Int { return v.v }\n"
                   "print f(Box(%d))\n" % a)
            expected = [str(a)]
        elif variant == 11:
            src = ("record Inner2 { z: Int }\n"
                   "record Inner { x: Int, deep: Inner2 }\n"
                   "record A { inner: Inner }\nrecord B { other: Int }\n"
                   "record C { inner: Inner, other: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int "
                   "{ return v.inner.deep.z + v.other }\n"
                   "print f(C(Inner(%d, Inner2(%d)), %d))\n" % (a, b, c))
            expected = [str(b + c)]
        elif variant == 12:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\ntype AA = A\ntype BB = B\n"
                   "type CC = C\n"
                   "fn f<T: AA & BB>(v: T) -> Int { return v.x + v.y }\n"
                   "let c: CC = C(%d, %d)\nprint f(c)\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 13:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
                   "let c = C(%d, %d) with { x = %d }\n"
                   "print f(c)\n" % (a, b, a + 1))
            expected = [str(a + 1 + b)]
        elif variant == 14:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T, n: Int = %d) -> Int "
                   "{ return v.x + v.y + n }\n"
                   "print f<C>(C(%d, %d))\n" % (c, a, b))
            expected = [str(a + b + c)]
        elif variant == 15:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
                   "fn mk(x: Int, y: Int) -> C { return C(x, y) }\n"
                   "print f(mk(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 16:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Text "
                   '{ return text(v.x + v.y) }\n'
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        else:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
                   "let c = C(%d, %d)\nlet d = c with { y = %d }\n"
                   "print f(d)\nprint f(c)\n" % (a, b, b + 1))
            expected = [str(a + b + 1), str(a + b)]
        return src, expected

    # ---------------------------------------------------- nested with paths
    def nested_with_valid(self):
        r = self.r
        a, b, c, d = (r.randint(0, 9) for _ in range(4))
        variant = r.randrange(19)
        if variant == 0:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d, %d), %d)\n"
                   "let r = q with { p.x = %d }\n"
                   "print r.p.x\nprint r.p.y\nprint q.p.x\n"
                   % (a, b, c, b))
            expected = [str(b), str(b), str(a)]
        elif variant == 1:
            src = ("record A { v: Int }\nrecord B { a: A }\n"
                   "record C { b: B }\nlet c = C(B(A(%d)))\n"
                   "let d = c with { b.a.v = %d }\n"
                   "print d.b.a.v\nprint c.b.a.v\n" % (a, b))
            expected = [str(b), str(a)]
        elif variant == 2:
            src = ("record P { v: Int }\nrecord Q { a: P, b: P, n: Int }\n"
                   "let q = Q(P(%d), P(%d), %d)\n"
                   "let r = q with { a.v = %d, b.v = %d }\n"
                   "print r.a.v\nprint r.b.v\nprint q.a.v\nprint q.b.v\n"
                   % (a, c, d, b, d))
            expected = [str(b), str(d), str(a), str(c)]
        elif variant == 3:
            src = ("record A { v: Int }\nrecord B extends A { w: Int }\n"
                   "record C { b: B }\nlet c = C(B(%d, %d))\n"
                   "let d = c with { b.v = %d, b.w = %d }\n"
                   "print d.b.v\nprint d.b.w\nprint c.b.v\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a)]
        elif variant == 4:
            src = ("type I = Int\nrecord P { x: I }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\nlet r = q with { p.x = %d }\n"
                   "print r.p.x\n" % (a, b))
            expected = [str(b)]
        elif variant == 5:
            src = ("record Box<T> { v: T }\nrecord Q { b: Box<Int> }\n"
                   "let q = Q(Box(%d))\nlet r = q with { b.v = %d }\n"
                   "print r.b.v\n" % (a, b))
            expected = [str(b)]
        elif variant == 6:
            src = ("record P { x: Int }\nrecord Q { p: P\n"
                   "  fn bump(self: Q) -> Q "
                   "{ return self with { p.x = self.p.x + 1 } }\n}\n"
                   "let q = Q(P(%d))\nlet r = q.bump()\n"
                   "print r.p.x\nprint q.p.x\n" % a)
            expected = [str(a + 1), str(a)]
        elif variant == 7:
            src = ("record P { x: Int }\n"
                   "fn mk(n: Int) -> P { return P(n) }\n"
                   "let r = mk(%d) with { x = %d }\nprint r.x\n" % (a, b))
            expected = [str(b)]
        elif variant == 8:
            src = ("record P { x: Int }\nrecord Q { p: P, n: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = (q with { p.x = %d }) with { n = %d }\n"
                   "print r.p.x\nprint r.n\nprint q.p.x\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a)]
        elif variant == 9:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\n"
                   "let r = q with { p.x = q.p.x + %d }\nprint r.p.x\n"
                   % (a, b))
            expected = [str(a + b)]
        elif variant == 10:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d, %d))\n"
                   "let r = q with { p.x = %d, p.y = %d }\n"
                   "print r.p.x\nprint r.p.y\nprint q.p.x\n"
                   % (a, b, c, d))
            expected = [str(c), str(d), str(a)]
        elif variant == 11:
            src = ("record P { x: Int }\nrecord Q { p: P, n: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = %d, n = %d }\n"
                   "print r.p.x\nprint r.n\n" % (a, b, c, d))
            expected = [str(c), str(d)]
        elif variant == 12:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d, %d))\n"
                   "let r = q with { p = q.p with { x = %d } }\n"
                   "print r.p.x\nprint r.p.y\nprint q.p.x\n" % (a, b, c))
            expected = [str(c), str(b), str(a)]
        elif variant == 13:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "fn set(q: Q, x: Int) -> Q { return q with { p.x = x } }\n"
                   "let r = set(Q(P(%d)), %d)\nprint r.p.x\n" % (a, b))
            expected = [str(b)]
        elif variant == 14:
            src = ("record P { x: Int\n"
                   "  fn get(self: P) -> Int { return self.x }\n}\n"
                   "record Q { p: P }\nlet q = Q(P(%d))\n"
                   "let r = q with { p.x = %d }\nprint r.p.get()\n" % (a, b))
            expected = [str(b)]
        elif variant == 15:
            src = ("record P { v: Int }\nrecord Q { a: P, b: P }\n"
                   "let p = P(%d)\nlet q = Q(p, p)\n"
                   "let r = q with { a.v = %d }\n"
                   "print r.a.v\nprint r.b.v\nprint q.a.v\n" % (a, b))
            expected = [str(b), str(a), str(a)]
        elif variant == 16:
            src = ("record A { v: Int, w: Int }\nrecord B { a: A }\n"
                   "record C { b: B, n: Int }\n"
                   "let c = C(B(A(%d, %d)), %d)\n"
                   "let d = c with { b.a.v = %d, b.a.w = %d, n = %d }\n"
                   "print d.b.a.v\nprint d.b.a.w\nprint d.n\nprint c.b.a.v\n"
                   % (a, b, c, b, c, a))
            expected = [str(b), str(c), str(a), str(a)]
        elif variant == 17:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\n"
                   "let r = q with { p.x = q.p.x * 2 + %d }\n"
                   "print r.p.x\n" % (a, b))
            expected = [str(a * 2 + b)]
        else:
            src = ("record P { x: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with { p.x = %d, z = %d }\n"
                   "print r.p.x\nprint r.z\nprint q.z\n" % (a, b, c, d))
            expected = [str(c), str(d), str(b)]
        return src, expected

    # ------------------------------------------- generic/union aliases
    def alias_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        variant = r.randrange(18)
        if variant == 0:
            src = ("type Maybe<T> = T | Text\n"
                   "let x: Maybe<Int> = %d\nprint x\n" % a)
            expected = [str(a)]
        elif variant == 1:
            src = ('type Maybe<T> = T | Text\n'
                   'let x: Maybe<Text> = "%s"\nprint x\n' % s)
            expected = [s]
        elif variant == 2:
            src = ("record Box<T> { v: T }\ntype Boxed<T> = Box<T>\n"
                   "let x: Boxed<Int> = Box(%d)\nprint x.v\n" % a)
            expected = [str(a)]
        elif variant == 3:
            src = ("record Box<T> { v: T }\ntype B1<T> = Box<T>\n"
                   "type B2<T> = B1<T>\ntype B3 = B2<Int>\n"
                   "let x: B3 = Box(%d)\nprint x.v\n" % a)
            expected = [str(a)]
        elif variant == 4:
            src = ("type V<T> = List<T>\n"
                   "let xs: V<Int> = [%d, %d]\nprint xs\n" % (a, b))
            expected = ["[%d, %d]" % (a, b)]
        elif variant == 5:
            src = ("type V<T> = List<T>\ntype M = V<V<Int>>\n"
                   "let x: M = [[%d], [%d]]\nprint x\n" % (a, b))
            expected = ["[[%d], [%d]]" % (a, b)]
        elif variant == 6:
            src = ("type Res<T> = Result<T, Text>\n"
                   "let r: Res<Int> = Ok(%d)\nprint r\n" % a)
            expected = ["Ok(%d)" % a]
        elif variant == 7:
            src = ("record Pair<A, B> { a: A, b: B }\n"
                   "type P<X, Y> = Pair<X, Y>\n"
                   'let p: P<Int, Text> = Pair(%d, "%s")\n'
                   "print p.a\nprint p.b\n" % (a, s))
            expected = [str(a), s]
        elif variant == 8:
            src = ("record A { x: Int }\nrecord B { y: Text }\n"
                   "type U = A | B\n"
                   'let u: U = B("%s")\nprint u.y\n' % s)
            expected = [s]
        elif variant == 9:
            src = ("enum E { A(Int), B(Text) }\ntype U = E | Int\n"
                   "let x: U = E.A(%d)\nprint x\n" % a)
            expected = ["E.A(%d)" % a]
        elif variant == 10:
            src = ("type I = Int\ntype L = List<I>\n"
                   "fn total(xs: L) -> Int {\n"
                   "  let s = 0\n  for x in xs { s := s + x }\n"
                   "  return s\n}\n"
                   "print total([%d, %d])\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 11:
            src = ("type I = Int\nrecord P { x: I }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\nlet r = q with { p.x = %d }\n"
                   "print r.p.x\n" % (a, b))
            expected = [str(b)]
        elif variant == 12:
            src = ("record Box<T> { v: T }\ntype B<T> = Box<Box<T>>\n"
                   "let x: B<Int> = Box(Box(%d))\nprint x.v.v\n" % a)
            expected = [str(a)]
        elif variant == 13:
            flag = ("true", "false")[r.randrange(2)]
            src = ("type U = Bool | Int\nlet x: U = %s\nprint x\n" % flag)
            expected = [flag.capitalize()]
        elif variant == 14:
            src = "type U = Decimal | Int\nlet x: U = 1.5\nprint x\n"
            expected = ["1.5"]
        elif variant == 15:
            src = ("type Res<T> = Result<T, Text>\n"
                   "fn good(n: Int) -> Res<Int> { return Ok(n) }\n"
                   "print good(%d)\n" % a)
            expected = ["Ok(%d)" % a]
        elif variant == 16:
            src = ("type Num = Int | Text\n"
                   "fn pick(b: Bool) -> Num {\n"
                   '  if b { return %d }\n  return "%s"\n}\n'
                   "print pick(true)\nprint pick(false)\n" % (a, s))
            expected = [str(a), s]
        else:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\ntype AA = A\ntype BB = B\n"
                   "fn f<T: AA & BB>(v: T) -> Int { return v.x + v.y }\n"
                   "print f(C(%d, %d))\n" % (a, b))
            expected = [str(a + b)]
        return src, expected

    # ------------------------------------ return/effect inference probes
    def inference_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(11)
        if variant == 0:
            src = ("fn f(x: Int) { return x + 1 }\nprint f(%d)\n" % a)
            expected = [str(a + 1)]
        elif variant == 1:
            src = "fn f(x: Int) { let y = x + 1 }\nprint f(%d)\n" % a
            expected = ["None"]
        elif variant == 2:
            src = ("fn f(x: Int) { return x + 1 }\n"
                   "let y: Any = f(%d)\nprint y\n" % a)
            expected = [str(a + 1)]
        elif variant == 3:
            src = ("fn f(x: Int) { return x + 1 }\nprint f(%d) * 2\n" % a)
            expected = [str((a + 1) * 2)]
        elif variant == 4:
            src = ("fn f(x: Int) -> Int effects { io.print } "
                   "{ print x\n return x }\nprint f(%d)\n" % a)
            expected = [str(a), str(a)]
        elif variant == 5:
            src = ("let f = fn(x: Int) -> Int effects { io.print } "
                   "{ print x\n return x }\nprint f(%d)\n" % a)
            expected = [str(a), str(a)]
        elif variant == 6:
            src = ("record R { v: Int\n"
                   "  fn show(self: R) -> Int effects { io.print } "
                   "{ print self.v\n return self.v }\n}\n"
                   "let r = R(%d)\nprint r.show()\n" % a)
            expected = [str(a), str(a)]
        elif variant == 7:
            src = ("fn inner(x: Int) -> Int effects { io.print } "
                   "{ print x\n return x }\n"
                   "fn outer(x: Int) -> Int effects { io.print } "
                   "{ return inner(x) }\nprint outer(%d)\n" % a)
            expected = [str(a), str(a)]
        elif variant == 8:
            src = ("fn g(x: Int) -> Int { return x + 1 }\n"
                   "fn f(x: Int) { return g(x) }\nprint f(%d)\n" % a)
            expected = [str(a + 1)]
        elif variant == 9:
            src = ("fn f(x: Int) { if x > 0 { return %d } return %d }\n"
                   "print f(%d)\n" % (a, b, a + 1))
            expected = [str(a)]
        else:
            src = ("fn f(x: Int) { return [x, x] }\nprint f(%d)\n" % a)
            expected = ["[%d, %d]" % (a, a)]
        return src, expected

    # ------------------------------------------------------- formatter
    def fmt_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(16)
        if variant == 0:
            src = self.intersection_valid()[0]
        elif variant == 1:
            src = self.nested_with_valid()[0]
        elif variant == 2:
            src = self.alias_valid()[0]
        elif variant == 3:
            src = self.inference_valid()[0]
        elif variant == 4:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\n"
                   "fn f<T: A & B>(v: T) -> Int "
                   "{ return v.x + v.y }\nprint f(C(%d, %d))\n" % (a, b))
        elif variant == 5:
            src = ("record P { x: Int }\nrecord Q { p: P, z: Int }\n"
                   "let q = Q(P(%d), %d)\n"
                   "let r = q with {\n  # inner\n  p.x = %d,\n  z = %d\n}\n"
                   "print r.p.x\nprint r.z\n" % (a, b, b, a))
        elif variant == 6:
            src = ("record Box<T> { v: T }\ntype Boxed<T> = Box<T>\n"
                   "type Maybe<T> = T | Text\n"
                   "let x: Maybe<Int> = %d\n"
                   "let b: Boxed<Int> = Box(%d)\n"
                   "print x\nprint b.v\n" % (a, b))
        elif variant == 7:
            src = ("record Box<T> { v: T }\n"
                   "type U = Box<Int> | Box<Text>\n"
                   "let x: U = Box(%d)\nprint x.v\n" % a)
        elif variant == 8:
            src = ("type V<T> = List<T>\ntype M = V<V<Int>>\n"
                   "let x: M = [[%d], [%d]]\nprint x\n" % (a, b))
        elif variant == 9:
            src = ("record A { v: Int }\nrecord B extends A { w: Int }\n"
                   "record C { b: B }\nlet c = C(B(%d, %d))\n"
                   "let d = c with { b.v = %d, b.w = %d }\n"
                   "print d.b.v\nprint d.b.w\n" % (a, b, b, a))
        elif variant == 10:
            src = ("record P { x: Int }\n"
                   "fn mk(n: Int) -> P { return P(n) }\n"
                   "let r = mk(%d) with { x = %d }\nprint r.x\n" % (a, b))
        elif variant == 11:
            src = ("record P { x: Int, y: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d, %d))\n"
                   "let r = q with { p.x = %d, p.y = %d }\n"
                   "print r.p.x\nprint r.p.y\n" % (a, b, b, a))
        elif variant == 12:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "record C { x: Int, y: Int }\ntype AA = A\ntype BB = B\n"
                   "fn f<T: AA & BB>(v: T) -> Int "
                   "{ return v.x + v.y }\nprint f(C(%d, %d))\n" % (a, b))
        elif variant == 13:
            src = ("fn f(x: Int) -> Int effects { io.print } "
                   "{ print x\n return x + 1 }\nprint f(%d)\n" % a)
        elif variant == 14:
            src = ("record Box<T> { v: T }\ntype B<T> = Box<Box<T>>\n"
                   "let x: B<Int> = Box(Box(%d))\nprint x.v.v\n" % a)
        else:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "let q = Q(P(%d))\n"
                   "let r = (q with { p.x = %d }) with { p.x = %d }\n"
                   "print r.p.x\n" % (a, b, a + b))
        marker = "# round8-%d { } # = + -\n" % r.randrange(10 ** 6)
        return marker + src


# --------------------------------------------------------- mutation tools
def _intersection_seed(seed):
    r = random.Random(seed)
    return ("record A { x: Int }\nrecord B { y: Int }\n"
            "record C { x: Int, y: Int }\n"
            "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
            "print f(C(%d, %d))\n" % (r.randint(1, 9), r.randint(1, 9)))


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


def _inference_seed(seed):
    r = random.Random(seed)
    return ("fn f(x: Int) { return x + %d }\n"
            "fn g(x: Int) -> Int effects { io.print } "
            "{ print x\n return x }\n"
            "print f(%d)\nprint g(%d)\n"
            % (r.randint(1, 9), r.randint(1, 9), r.randint(1, 9)))


def _inter_trailing(src):
    return src.replace("A & B", "A &", 1)


def _inter_leading(src):
    return src.replace("A & B", "& A B", 1)


def _inter_missing_amp(src):
    return src.replace("A & B", "A B", 1)


def _inter_empty_component(src):
    return src.replace("A & B", "A & >", 1)


def _inter_duplicate(src):
    return src.replace("A & B", "A & A", 1)


def _inter_unknown(src):
    return src.replace("A & B", "A & Nope", 1)


def _inter_bad_typaram(src):
    return src.replace("<T: A & B>", "<T: A & B", 1)


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


def _with_missing_close(src):
    return src.replace("print r.p.x", " }\nprint r.p.x", 1)


def _with_bad_kw(src):
    return src.replace("q with {", "q wiht {", 1)


def _with_unknown_field(src):
    return src.replace("z =", "q =", 1)


def _with_leading_dot(src):
    return src.replace("p.x =", ".p.x =", 1)


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


def _infer_bad_return_type(src):
    return src.replace("fn f(x: Int) {", "fn f(x: Int) -> Text {", 1)


def _infer_missing_effects(src):
    return src.replace("effects { io.print } ", "", 1)


def _infer_bad_effect_braces(src):
    return src.replace("effects { io.print }", "effects io.print", 1)


def _infer_missing_return_expr(src):
    return re.sub(r"return x \+ \d+", "return", src, count=1)


def _infer_missing_param_type(src):
    return src.replace("x: Int", "x:", 1)


_INTER_MUTATORS = (
    _inter_trailing,
    _inter_leading,
    _inter_missing_amp,
    _inter_empty_component,
    _inter_duplicate,
    _inter_unknown,
    _inter_bad_typaram,
)

_WITH_MUTATORS = (
    _with_missing_eq,
    _with_unknown_leaf,
    _with_nonrecord_path,
    _with_type_mismatch,
    _with_duplicate,
    _with_prefix_conflict,
    _with_missing_value,
    _with_missing_close,
    _with_bad_kw,
    _with_unknown_field,
    _with_leading_dot,
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
)

_INFER_MUTATORS = (
    _infer_bad_return_type,
    _infer_missing_effects,
    _infer_bad_effect_braces,
    _infer_missing_return_expr,
    _infer_missing_param_type,
)


# ------------------------------------------------------ static rejects
_INTERSECTION_REJECT = (
    "type I = Int & Text\n",
    "fn f(x: Int & Text) -> Int { return 1 }\n",
    "record R { x: Int & Text }\n",
    "let x: Int & Text = 1\n",
    "type I = Int | Text & Bool\n",
    "record A { x: Int }\nfn f<T: A & A>(v: T) -> Int { return v.x }\n",
    "record A { x: Int }\nfn f<T: A & Nope>(v: T) -> Int { return v.x }\n",
    "record A { x: Int }\nrecord R<T: A & A> { x: T }\n",
    "record A { x: Int }\nenum E<T: A & A> { V(T) }\n",
    "record A { x: Int }\nrecord B { y: Int }\nrecord C { x: Int }\n"
    "fn f<T: A & B>(v: T) -> Int { return v.x }\nprint f(C(1))\n",
    "record A { x: Int }\nrecord B { y: Int }\nrecord D { x: Int }\n"
    "fn f<T: A & B>(v: T) -> Int { return v.x }\nprint f<D>(D(1))\n",
    "fn f<T: A & >(v: Int) -> Int { return v }\n",
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
    "record P { x: Int }\nlet q = P(1)\nlet r = q with { x = 2, }\n",
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
    "type A = Int | Text\ntype B = A | Bool\n",
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
    "type A<T> = U\n",
    "type A<T> = A<T>\n",
    "fn f() -> Int { type A = Int\n return 1 }\n",
    "record Box<T> { v: T }\ntype Boxed<T> = Box<T>\n"
    "let x: Boxed<Int> = Box(1)\nprint x.v\nlet y: Boxed = Box(2)\n",
)

_INFERENCE_REJECT = (
    "fn f(x: Int) -> Text { return x + 1 }\nprint f(1)\n",
    "fn f(x: Int) -> Int { print x\n return x }\nprint f(1)\n",
    "fn inner(x: Int) -> Int effects { io.print } "
    "{ print x\n return x }\n"
    "fn outer(x: Int) -> Int { return inner(x) }\nprint outer(1)\n",
    "return 1\n",
    "let f = fn(x: Int) -> Int { print x\n return x }\nprint f(1)\n",
    "fn f(x: Int) -> Int { return x + }\nprint f(1)\n",
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


def _assert_parse_error(test, src, label):
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


# ------------------------------------------------------------ case tables
_N_INTERSECTION = 30
_N_WITH = 40
_N_ALIAS = 40
_N_INFERENCE = 25
_N_FMT = 35
_N_INTER_MUT = 14
_N_WITH_MUT = 18
_N_ALIAS_MUT = 16
_N_INFER_MUT = 10

_INTERSECTION_CASES = [_Gen8(20000 + i).intersection_valid()
                       for i in range(_N_INTERSECTION)]
_WITH_CASES = [_Gen8(21000 + i).nested_with_valid()
               for i in range(_N_WITH)]
_ALIAS_CASES = [_Gen8(22000 + i).alias_valid() for i in range(_N_ALIAS)]
_INFERENCE_CASES = [_Gen8(23000 + i).inference_valid()
                    for i in range(_N_INFERENCE)]
_FMT_CASES = [_Gen8(24000 + i).fmt_valid() for i in range(_N_FMT)]

_MUTATION_CASES = (
    [_INTER_MUTATORS[i % len(_INTER_MUTATORS)](_intersection_seed(25000 + i))
     for i in range(_N_INTER_MUT)]
    + [_WITH_MUTATORS[i % len(_WITH_MUTATORS)](_with_seed(26000 + i))
       for i in range(_N_WITH_MUT)]
    + [_ALIAS_MUTATORS[i % len(_ALIAS_MUTATORS)](_alias_seed(27000 + i))
       for i in range(_N_ALIAS_MUT)]
    + [_INFER_MUTATORS[i % len(_INFER_MUTATORS)](_inference_seed(28000 + i))
       for i in range(_N_INFER_MUT)]
)

_FILE_CASES = (
    ("record A { x: Int }\nrecord B { y: Int }\n"
     "record C { x: Int, y: Int }\n"
     "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
     "export { A, B, C, f }\n",
     'import "lib.gm" as m\nprint m.f(m.C(1, 2))\n',
     ["3"]),
    ("record Box<T> { v: T }\ntype Boxed<T> = Box<T>\n"
     "type Maybe<T> = T | Text\n"
     "fn mk(n: Int) -> Maybe<Int> { return n }\n"
     "export { Box, Boxed, Maybe, mk }\n",
     'import "lib.gm" as m\nlet x: m.Maybe<Int> = m.mk(4)\n'
     'let b: m.Boxed<Int> = m.Box(5)\nprint x\nprint b.v\n',
     ["4", "5"]),
    ("record P { x: Int }\nrecord Q { p: P }\n"
     "fn mk(n: Int) -> Q { return Q(P(n)) }\n"
     "export { P, Q, mk }\n",
     'import "lib.gm" as m\n'
     'let r = m.mk(1) with { p.x = 9 }\nprint r.p.x\n',
     ["9"]),
)

_TOTAL_CASES = (
    _N_INTERSECTION + _N_WITH + _N_ALIAS + _N_INFERENCE + _N_FMT
    + len(_MUTATION_CASES) + len(_INTERSECTION_REJECT) + len(_WITH_REJECT)
    + len(_ALIAS_REJECT) + len(_INFERENCE_REJECT) + len(_FILE_CASES)
)


# ----------------------------------------------------------- test classes
class Fuzz8MetaTests(unittest.TestCase):
    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_generators_are_deterministic(self):
        for i in range(12):
            self.assertEqual(_Gen8(29000 + i).intersection_valid(),
                             _Gen8(29000 + i).intersection_valid())
            self.assertEqual(_Gen8(30000 + i).nested_with_valid(),
                             _Gen8(30000 + i).nested_with_valid())
            self.assertEqual(_Gen8(31000 + i).alias_valid(),
                             _Gen8(31000 + i).alias_valid())
            self.assertEqual(_Gen8(32000 + i).inference_valid(),
                             _Gen8(32000 + i).inference_valid())
            self.assertEqual(_Gen8(33000 + i).fmt_valid(),
                             _Gen8(33000 + i).fmt_valid())

    def test_all_generated_valid_cases_check(self):
        groups = (
            ("intersection", _INTERSECTION_CASES),
            ("with", _WITH_CASES),
            ("alias", _ALIAS_CASES),
            ("inference", _INFERENCE_CASES),
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


class GeneratedIntersectionTests(unittest.TestCase):
    def test_generated_intersection_bounds(self):
        for i, (src, expected) in enumerate(_INTERSECTION_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "intersection[%d]" % i)


class GeneratedNestedWithTests(unittest.TestCase):
    def test_generated_nested_with_paths(self):
        for i, (src, expected) in enumerate(_WITH_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "with[%d]" % i)


class GeneratedAliasTests(unittest.TestCase):
    def test_generated_generic_and_union_aliases(self):
        for i, (src, expected) in enumerate(_ALIAS_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "alias[%d]" % i)


class GeneratedInferenceProbeTests(unittest.TestCase):
    def test_generated_inference_probes(self):
        for i, (src, expected) in enumerate(_INFERENCE_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "inference[%d]" % i)


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
    def test_intersection_rejections(self):
        for i, src in enumerate(_INTERSECTION_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "intersection-reject[%d]" % i)

    def test_with_rejections(self):
        for i, src in enumerate(_WITH_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "with-reject[%d]" % i)

    def test_alias_rejections(self):
        for i, src in enumerate(_ALIAS_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "alias-reject[%d]" % i)

    def test_inference_rejections(self):
        for i, src in enumerate(_INFERENCE_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "inference-reject[%d]" % i)

    def test_mutations_are_gama_errors(self):
        for i, src in enumerate(_MUTATION_CASES):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "mutation[%d]" % i)


class FeatureProbeTests(unittest.TestCase):
    def test_intersection_bounds_execute(self):
        _assert_program(
            self,
            "record A { x: Int }\nrecord B { y: Int }\n"
            "record C { x: Int, y: Int }\n"
            "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
            "print f(C(1, 2))\n",
            ["3"], "probe-intersection")

    def test_nested_with_path_executes(self):
        _assert_program(
            self,
            "record P { x: Int }\nrecord Q { p: P }\n"
            "let q = Q(P(1))\n"
            "let r = q with { p.x = 2 }\nprint r.p.x\n",
            ["2"], "probe-with-path")

    def test_generic_alias_with_bare_union_parameter(self):
        _assert_program(
            self,
            "type Maybe<T> = T | Text\nlet x: Maybe<Int> = 1\nprint x\n",
            ["1"], "probe-generic-union-alias")

    def test_union_alias_distinguishes_generic_args(self):
        _assert_program(
            self,
            "record Box<T> { v: T }\n"
            "type U = Box<Int> | Box<Text>\n"
            "let x: U = Box(1)\nprint x.v\n",
            ["1"], "probe-union-generic-args")

    def test_omitted_return_is_any_today(self):
        _assert_program(
            self,
            "fn f(x: Int) { return x + 1 }\nlet y: Any = f(2)\nprint y\n",
            ["3"], "probe-return-any")

    def test_omitted_effects_are_empty_today(self):
        _assert_program(
            self,
            "fn f(x: Int) -> Int { return x + 1 }\nprint f(2)\n",
            ["3"], "probe-effects-empty")


class Fuzz8RegressionPins(unittest.TestCase):
    """Minimal pins for real inference gaps in this worktree.

    Defect 1: an omitted return type is erased to ``Any`` instead of being
    inferred from the returns, so a result cannot be assigned to (or passed
    as) its natural type.  ``let y: Int = f(2)`` is rejected with
    ``Any != Int`` even though every return is ``Int``.

    Defect 2: the same erasure hides inconsistent returns; a function whose
    branches return ``Int`` and ``Text`` is accepted.

    Defect 3: an omitted effect set is treated as empty rather than inferred,
    so a function that only ``print``s is rejected unless it declares
    ``effects { io.print }``.

    Each test fails on the current core and is pinned with
    ``expectedFailure``; an unexpected success means inference was added and
    the pin can be removed.
    """

    @unittest.expectedFailure
    def test_omitted_return_type_infers_assignment_type(self):
        src = ("fn f(x: Int) { return x + 1 }\n"
               "let y: Int = f(2)\nprint y\n")
        G.check(src)
        self.assertEqual(list(G.run(src).out), ["3"])

    @unittest.expectedFailure
    def test_omitted_return_type_rejects_inconsistent_returns(self):
        src = ('fn f(b: Bool) { if b { return 1 } return "s" }\n'
               "print f(true)\n")
        with self.assertRaises(G.GamaError):
            G.check(src)

    @unittest.expectedFailure
    def test_omitted_effects_infers_used_effects(self):
        src = ("fn f(x: Int) -> Int { print x\n return x }\n"
               "print f(1)\n")
        G.check(src)
        self.assertEqual(list(G.run(src).out), ["1", "1"])


if __name__ == "__main__":
    unittest.main()
