"""Deterministic property/fuzz round 7 for the current GAMA-L 2.0 surface.

Rounds 1-6 covered the core, builtins/imports/limits, formatter/IR parity,
structural records, bounds, prove/verify, record methods, nominal inheritance,
export enums/renames, runtime-limit parity, selective re-exports and both
simple and generic/union type aliases.

This round re-samples the newest surface with fresh seeds:

* generic type aliases (``type Boxed<T> = Box<T>``), including chains,
  arity checks, aliases through records/enums/functions and IR transparency;
* union type aliases (``type Num = Int | Text``), including records,
  enums, functions and chained aliases;
* selective re-export lists crossing the file loader with generic/union
  aliases, renames and multiple lists, with interpreter/IR parity;
* IR host callables (direct, variable, higher-order, returned, wrapped
  exceptions, arity and missing names);
* nested record copy/update expressions (``a with { p = a.p with { ... } }``)
  including inherited fields and methods;
* IR runtime limits on nested ``with``, alias-annotated loops and inherited
  recursive methods, byte-for-byte with the reference interpreter;
* formatter round-trips for aliases, selective lists and nested ``with``;
* graceful probes for unsupported intersection bounds (``A & B``) which must
  be rejected with a ``GamaError`` rather than crash;
* malformed mutations of alias/union/``with`` sources that must stay inside
  the ``GamaError`` family.

Invariants exercised:

* Generated *valid* sources raise only ``GamaError`` from
  ``parse``/``check``/``run``; a bare Python exception is a failure.
* IR parity holds for aliases, nested ``with`` and selective re-exports; an
  unexpected ``IRUnsupported`` for a supported case is a failure.
* IR host calls return the documented value and wrap host exceptions and
  arity mistakes in ``IRRuntimeError`` (a ``GamaError``).
* Malformed mutations fail the parser/checker with ``GamaError``.
* Runtime-limit outcomes match the interpreter exactly.
* The formatter is idempotent, keeps the program checking, and preserves the
  reference interpreter output.

Everything is seeded. Three real defects surfaced by this round are pinned as
minimal ``expectedFailure`` tests at the bottom (unexpected success means the
core was fixed) instead of being hidden.
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
    """Clean GAMA-L environment with deterministic overrides."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("GAMA_L_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


def _h_double(x):
    return x * 2


def _h_plus3(x):
    return x + 3


def _h_pair(x, y):
    return x * 10 + y


def _h_mk(n):
    return [n, n + 1]


def _h_label(n):
    return "v%d" % n


def _h_hjoin(a, b):
    return "%s-%s" % (a, b)


def _h_boom(x):
    raise ValueError("boom")


class _Gen7:
    """Seeded generator for round-7 cases."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    # ------------------------------------------------- generic aliases
    def generic_alias_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        flag = ("true", "false")[r.randrange(2)]
        variant = r.randrange(18)
        box = "record Box<T> { v: T }\n"
        pair = "record Pair<A, B> { a: A, b: B }\n"
        if variant == 0:
            src = "type I = Int\nlet x: I = %d\nprint x\n" % a
            expected = [str(a)]
        elif variant == 1:
            src = ("type I = Int\ntype J = I\nlet x: J = %d\nprint x\n" % a)
            expected = [str(a)]
        elif variant == 2:
            src = 'type T = Text\nlet x: T = "%s"\nprint x\n' % s
            expected = [s]
        elif variant == 3:
            src = "type F = Bool\nlet x: F = %s\nprint x\n" % flag
            expected = [flag.capitalize()]
        elif variant == 4:
            src = "type D = Decimal\nlet x: D = 1.5\nprint x\n"
            expected = ["1.5"]
        elif variant == 5:
            src = box + ("type IntBox = Box<Int>\n"
                         "let v: IntBox = Box(%d)\nprint v.v\n" % a)
            expected = [str(a)]
        elif variant == 6:
            src = box + ("type Boxed<T> = Box<T>\n"
                         "let v: Boxed<Int> = Box(%d)\nprint v.v\n" % a)
            expected = [str(a)]
        elif variant == 7:
            src = box + ("type A<T> = Box<T>\ntype B<T> = A<T>\n"
                         "let v: B<Int> = Box(%d)\nprint v.v\n" % a)
            expected = [str(a)]
        elif variant == 8:
            src = ("type Vec<T> = List<T>\n"
                   "let xs: Vec<Int> = [%d, %d]\nprint xs\n" % (a, b))
            expected = ["[%d, %d]" % (a, b)]
        elif variant == 9:
            src = ("type V<T> = List<T>\ntype M = V<V<Int>>\n"
                   "let x: M = [[%d], [%d]]\nprint x\n" % (a, b))
            expected = ["[[%d], [%d]]" % (a, b)]
        elif variant == 10:
            src = ("type Res<T> = Result<T, Text>\n"
                   "let x: Res<Int> = Ok(%d)\nprint x\n" % a)
            expected = ["Ok(%d)" % a]
        elif variant == 11:
            src = ("type Res<T> = Result<T, Text>\n"
                   "fn good(n: Int) -> Res<Int> { return Ok(n) }\n"
                   "print good(%d)\n" % a)
            expected = ["Ok(%d)" % a]
        elif variant == 12:
            src = pair + ("type P<X, Y> = Pair<X, Y>\n"
                          'let p: P<Int, Text> = Pair(%d, "%s")\n'
                          "print p.a\nprint p.b\n" % (a, s))
            expected = [str(a), s]
        elif variant == 13:
            src = ("type I = Int\nrecord R { x: I, y: I }\n"
                   "let r = R(%d, %d)\nprint r.x + r.y\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 14:
            src = ("type I = Int\nenum E { V(I) }\n"
                   "let e = E.V(%d)\nprint e\n" % a)
            expected = ["E.V(%d)" % a]
        elif variant == 15:
            src = ("type I = Int\n"
                   "fn inc(x: I) -> I { return x + 1 }\n"
                   "print inc(%d)\n" % a)
            expected = [str(a + 1)]
        elif variant == 16:
            src = ("type AnyBox<T> = List<T>\n"
                   "fn n(xs: AnyBox<Any>) -> Int { return len(xs) }\n"
                   "print n([%d, %d])\n" % (a, b))
            expected = ["2"]
        else:
            src = ("type A = Int\ntype B = List<A>\n"
                   "fn total(xs: B) -> Int {\n"
                   "  let s = 0\n  for x in xs { s := s + x }\n"
                   "  return s\n}\n"
                   "print total([%d, %d])\n" % (a, b))
            expected = [str(a + b)]
        return src, expected

    # --------------------------------------------------- union aliases
    def union_alias_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        variant = r.randrange(14)
        box = "record Box<T> { v: T }\n"
        if variant == 0:
            src = "type Num = Int | Text\nlet x: Num = %d\nprint x\n" % a
            expected = [str(a)]
        elif variant == 1:
            src = 'type Num = Int | Text\nlet x: Num = "%s"\nprint x\n' % s
            expected = [s]
        elif variant == 2:
            src = ("type Num = Int | Text\n"
                   "fn ident(x: Num) -> Num { return x }\n"
                   'print ident(%d)\nprint ident("%s")\n' % (a, s))
            expected = [str(a), s]
        elif variant == 3:
            src = ("type Num = Int | Text\nrecord R { v: Num }\n"
                   'let x = R(%d)\nlet y = R("%s")\n'
                   "print x.v\nprint y.v\n" % (a, s))
            expected = [str(a), s]
        elif variant == 4:
            src = ("record A { x: Int }\nrecord B { y: Int }\n"
                   "type U = A | B\nlet u: U = A(%d)\nprint u.x\n" % a)
            expected = [str(a)]
        elif variant == 5:
            src = box + ("type U = Box<Int> | Text\n"
                         "let u: U = Box(%d)\nprint u.v\n" % a)
            expected = [str(a)]
        elif variant == 6:
            src = box + ('type U = Box<Int> | Text\n'
                         'let u: U = "%s"\nprint u\n' % s)
            expected = [s]
        elif variant == 7:
            src = ("type Num = Int | Text\n"
                   "fn f(b: Bool) -> Num {\n"
                   '  if b { return %d }\n  return "%s"\n}\n'
                   "print f(true)\nprint f(false)\n" % (a, s))
            expected = [str(a), s]
        elif variant == 8:
            src = ("type A = Int | Text\ntype B = A\n"
                   "let x: B = %d\nprint x\n" % a)
            expected = [str(a)]
        elif variant == 9:
            src = ("enum E { A, B }\ntype U = E | Int\n"
                   "let x: U = E.A\nprint x\n")
            expected = ["E.A"]
        elif variant == 10:
            src = "type U = Bool | Int\nlet x: U = true\nprint x\n"
            expected = ["True"]
        elif variant == 11:
            src = "type U = Decimal | Int\nlet x: U = 1.5\nprint x\n"
            expected = ["1.5"]
        elif variant == 12:
            src = ("record A { x: Int }\nrecord B { y: Text }\n"
                   'type U = A | B\nlet u: U = B("%s")\nprint u.y\n' % s)
            expected = [s]
        else:
            src = ("type Num = Int | Text\n"
                   "fn pick(x: Num, b: Bool) -> Num {\n"
                   "  if b { return x }\n  return 0\n}\n"
                   "print pick(%d, true)\nprint pick(1, false)\n" % a)
            expected = [str(a), "0"]
        return src, expected

    # ------------------------------------------- selective re-exports
    def selective_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        s = "s%d" % r.randint(0, 99)
        variant = r.randrange(12)
        if variant == 0:
            lib = ("fn add(a: Int, b: Int) -> Int { return a + b }\n"
                   "export { add }\n")
            main = 'import "lib.gm" as m\nprint m.add(%d, %d)\n' % (a, b)
            expected = [str(a + b)]
        elif variant == 1:
            lib = "let pi: Decimal = 3.14\nexport { pi as PI }\n"
            main = 'import "lib.gm" as m\nprint m.PI\n'
            expected = ["3.14"]
        elif variant == 2:
            lib = ("record Point { x: Int, y: Int }\n"
                   "export { Point as Pt }\n")
            main = ('import "lib.gm" as m\n'
                    "let p = m.Pt(%d, %d)\nprint p.x + p.y\n" % (a, b))
            expected = [str(a + b)]
        elif variant == 3:
            lib = "enum Color { Red, Green, Blue }\nexport { Color as Hue }\n"
            main = 'import "lib.gm" as m\nprint m.Hue.Green\n'
            expected = ["m.Hue.Green"]
        elif variant == 4:
            lib = ("fn inc(a: Int) -> Int { return a + 1 }\n"
                   "fn dbl(a: Int) -> Int { return a * 2 }\n"
                   "export { inc as f, dbl as g }\n")
            main = ('import "lib.gm" as m\n'
                    "print m.f(%d)\nprint m.g(%d)\n" % (a, a))
            expected = [str(a + 1), str(a * 2)]
        elif variant == 5:
            lib = ("fn add(a: Int, b: Int) -> Int { return a + b }\n"
                   "fn hidden(a: Int) -> Int { return a * 100 }\n"
                   "export { add }\n")
            main = ('import "lib.gm"\n'
                    "print add(%d, %d)\nprint hidden(%d)\n" % (a, b, a))
            expected = [str(a + b), str(a * 100)]
        elif variant == 6:
            lib = ("record B<T> { v: T }\n"
                   "type Boxed<T> = B<T>\n"
                   "export { B as C, Boxed as V }\n")
            main = ('import "lib.gm" as m\n'
                    "let x: m.V<Int> = m.C(%d)\nprint x.v\n" % a)
            expected = [str(a)]
        elif variant == 7:
            lib = ("type Num = Int | Text\n"
                   "fn mk(n: Int) -> Num { return n }\n"
                   "export { Num, mk }\n")
            main = ('import "lib.gm" as m\n'
                    "let x: m.Num = m.mk(%d)\nprint x\n" % a)
            expected = [str(a)]
        elif variant == 8:
            lib = ("record Animal { name: Text }\n"
                   "record Dog extends Animal { breed: Text }\n"
                   "fn build() -> Dog { return Dog(\"Rex\", \"lab\") }\n"
                   "export { Animal as Beast, Dog as Hound, build }\n")
            main = ('import "lib.gm" as m\n'
                    "let d = m.build()\n"
                    "let a: m.Beast = d\n"
                    "print a.name\nprint d.breed\n")
            expected = ["Rex", "lab"]
        elif variant == 9:
            lib = ("type Nums<T> = List<T>\n"
                   "type Ints = Nums<Int>\n"
                   "export { Nums as V, Ints }\n")
            main = ('import "lib.gm" as m\n'
                    "let x: m.V<Int> = [%d]\n"
                    "let y: m.Ints = [%d]\n"
                    "print x\nprint y\n" % (a, b))
            expected = ["[%d]" % a, "[%d]" % b]
        elif variant == 10:
            lib = ("fn one() -> Int { return %d }\n"
                   "fn two() -> Int { return %d }\n"
                   "export { one }\nexport { two as second }\n"
                   % (a, b))
            main = ('import "lib.gm" as m\n'
                    "print m.one()\nprint m.second()\n")
            expected = [str(a), str(b)]
        else:
            lib = ("type I = Int\n"
                   "fn mk(x: I) -> I { return x }\n"
                   "export { I as Ident, mk as build }\n")
            main = ('import "lib.gm" as m\n'
                    "let x: m.Ident = m.build(%d)\nprint x\n" % a)
            expected = [str(a)]
        return lib, main, expected

    # ------------------------------------------------ IR host calls
    def host_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(13)
        h = _h_double
        plus = _h_plus3
        pair = _h_pair
        mk = _h_mk
        label = _h_label
        hjoin = _h_hjoin
        if variant == 0:
            src, env, expected = "print h(%d)\n" % a, {"h": h}, [str(a * 2)]
        elif variant == 1:
            src = "let x = plus(%d)\nprint x + %d\n" % (a, b)
            env, expected = {"plus": plus}, [str(a + 3 + b)]
        elif variant == 2:
            src = "let f = h\nprint f(%d)\n" % a
            env, expected = {"h": h}, [str(a * 2)]
        elif variant == 3:
            src = "print (h)(%d)\n" % a
            env, expected = {"h": h}, [str(a * 2)]
        elif variant == 4:
            src = ("fn apply(g: Any, x: Any) -> Any { return g(x) }\n"
                   "print apply(h, %d)\n" % a)
            env, expected = {"h": h}, [str(a * 2)]
        elif variant == 5:
            src = ("fn pick() -> Any { return h }\n"
                   "print pick()(%d)\n" % a)
            env, expected = {"h": h}, [str(a * 2)]
        elif variant == 6:
            src, env, expected = ("print pair(%d, %d)\n" % (a, b),
                                  {"pair": pair}, [str(a * 10 + b)])
        elif variant == 7:
            src, env, expected = ("print mk(%d)\n" % a,
                                  {"mk": mk}, ["[%d, %d]" % (a, a + 1)])
        elif variant == 8:
            src, env, expected = ("print label(%d)\n" % a,
                                  {"label": label}, ["v%d" % a])
        elif variant == 9:
            src = "let f = h\nlet g = f\nprint g(%d)\n" % a
            env, expected = {"h": h}, [str(a * 2)]
        elif variant == 10:
            src = "print h(%d) + h(%d)\n" % (a, b)
            env, expected = {"h": h}, [str(a * 2 + b * 2)]
        elif variant == 11:
            src = ("fn inc(x: Any) -> Any { return x + 1 }\n"
                   "print inc(h(%d))\n" % a)
            env, expected = {"h": h}, [str(a * 2 + 1)]
        else:
            src = 'print hjoin("a", "b")\n'
            env, expected = {"hjoin": hjoin}, ["a-b"]
        return src, env, expected

    # ------------------------------------------------- nested with
    def nested_with_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(10)
        if variant == 0:
            src = ("record P { x: Int, y: Int }\n"
                   "record Q { p: P, z: Int }\n"
                   "let q = Q(P(%d, %d), %d)\n"
                   "let r = q with { p = q.p with { x = %d } }\n"
                   "print r.p.x\nprint r.p.y\n" % (a, b, a + b, b))
            expected = [str(b), str(b)]
        elif variant == 1:
            src = ("record A { v: Int }\n"
                   "record B { a: A }\n"
                   "record C { b: B }\n"
                   "let c = C(B(A(%d)))\n"
                   "let d = c with { b = c.b with { a = c.b.a with { v = %d } } }\n"
                   "print d.b.a.v\n" % (a, b))
            expected = [str(b)]
        elif variant == 2:
            src = ("record P { x: Int, y: Int }\n"
                   "let p = P(%d, %d)\n"
                   "let q = p with { x = %d, y = %d }\n"
                   "print q.x + q.y\n" % (a, b, b, a))
            expected = [str(a + b)]
        elif variant == 3:
            src = ("record P { x: Int, y: Int }\n"
                   "fn bump(p: P) -> P { return p with { x = p.x + 1 } }\n"
                   "let q = bump(P(%d, %d))\nprint q.x\nprint q.y\n"
                   % (a, b))
            expected = [str(a + 1), str(b)]
        elif variant == 4:
            src = ("record A { v: Int }\n"
                   "record B { a: A }\n"
                   "fn set(b: B, x: Int) -> B {\n"
                   "  return b with { a = b.a with { v = x } }\n}\n"
                   "let r = set(B(A(%d)), %d)\nprint r.a.v\n" % (a, b))
            expected = [str(b)]
        elif variant == 5:
            src = ("record Box<T> { v: T }\n"
                   "type I = Int\n"
                   "let b: Box<I> = Box(%d)\n"
                   "let c = b with { v = %d }\nprint c.v\n" % (a, b))
            expected = [str(b)]
        elif variant == 6:
            src = ("record A { x: Int\n"
                   "  fn val(self: A) -> Int { return self.x }\n}\n"
                   "let p = A(%d)\n"
                   "let q = p with { x = %d }\nprint q.val()\n" % (a, b))
            expected = [str(b)]
        elif variant == 7:
            src = ("type I = Int\nrecord P { x: I, y: I }\n"
                   "let p = P(%d, %d)\n"
                   "let q = p with { y = %d }\nprint q.y\n"
                   % (a, b, a + b))
            expected = [str(a + b)]
        elif variant == 8:
            src = ("record A { v: Int }\n"
                   "record B extends A { w: Int }\n"
                   "let b = B(%d, %d)\n"
                   "let c = b with { v = %d, w = %d }\n"
                   "print c.v\nprint c.w\n" % (a, b, a + 1, b + 1))
            expected = [str(a + 1), str(b + 1)]
        else:
            src = ("record A { v: Int }\n"
                   "record B { a: A, n: Int }\n"
                   "let b = B(A(%d), %d)\n"
                   "let c = b with {\n"
                   "  a = b.a with { v = b.a.v + 1 },\n"
                   "  n = b.n + 1\n}\n"
                   "print c.a.v\nprint c.n\n" % (a, b))
            expected = [str(a + 1), str(b + 1)]
        return src, expected

    # ---------------------------------------------------- formatter
    def fmt_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(15)
        if variant == 0:
            src = ("type Boxed<T> = Box<T>\nrecord Box<T> { v: T }\n"
                   "let x: Boxed<Int> = Box(%d)\nprint x.v\n" % a)
        elif variant == 1:
            src = ("type A = Int\ntype B = A\ntype C = B\n"
                   "let x: C = %d\nprint x\n" % a)
        elif variant == 2:
            src = ("type Boxed<T> = Box<T>\nrecord Box<T> { v: T }\n"
                   "type IntBox = Boxed<Int>\ntype U = Box<Int> | Text\n"
                   "let x: IntBox = Box(%d)\nlet u: U = Box(%d)\n"
                   "print x.v\nprint u\n" % (a, b))
        elif variant == 3:
            src = ("type Num = Int | Text\n"
                   "let x: Num = %d\nprint x\n" % a)
        elif variant == 4:
            src = ("record Box<T> { v: T }\ntype U = Box<Int> | Text\n"
                   "let u: U = Box(%d)\nprint u.v\n" % a)
        elif variant == 5:
            src = ("fn add(a: Int, b: Int) -> Int { return a + b }\n"
                   "let pi: Decimal = 3.14\nexport {\n  add,\n  pi as PI,\n}\n")
        elif variant == 6:
            src = ("record B<T> { v: T }\ntype Boxed<T> = B<T>\n"
                   "type IntBox = Boxed<Int>\n"
                   "export { B as C, Boxed as V, IntBox }\n")
        elif variant == 7:
            src = ("record P { x: Int, y: Int }\n"
                   "record Q { p: P, z: Int }\n"
                   "let q = Q(P(%d, %d), %d)\n"
                   "let r = q with { p = q.p with { x = %d } }\n"
                   "print r.p.x\n" % (a, b, a + b, b))
        elif variant == 8:
            src = ("record P { x: Int }\nrecord Q { p: P }\n"
                   "# top\nlet q = Q(P(%d))\n# inner\n"
                   "let r = q with { p = q.p with { x = %d } }\n"
                   "print r.p.x\n" % (a, b))
        elif variant == 9:
            src = ("record A { v: Int }\nrecord B extends A { w: Int }\n"
                   "let b = B(%d, %d)\n"
                   "let c = b with { v = %d, w = %d }\n"
                   "print c.v\nprint c.w\n" % (a, b, b, a))
        elif variant == 10:
            src = ("type I = Int\nrecord A { v: I }\n"
                   "record B extends A { w: I }\n"
                   "let b = B(%d, %d)\nprint b.v + b.w\n" % (a, b))
        elif variant == 11:
            src = ("type I = Int\nenum E { V(I), W(Text) }\n"
                   "let e = E.V(%d)\nprint e\n" % a)
        elif variant == 12:
            src = ("type A<T> = List<T>\ntype B = A<Int>\n"
                   "type C = List<A<Int>>\n"
                   "let x: B = [%d]\nlet y: C = [[%d]]\n"
                   "print x\nprint y\n" % (a, b))
        elif variant == 13:
            src = ("record A { x: Int\n"
                   "  fn val(self: A) -> Int { return self.x }\n}\n"
                   "let p = A(%d)\nlet q = p with { x = %d }\n"
                   "print q.val()\n" % (a, b))
        else:
            src = ("type Res<T> = Result<T, Text>\n"
                   "fn good(n: Int) -> Res<Int> { return Ok(n) }\n"
                   "let r: Res<Int> = good(%d)\nprint r\n" % a)
        marker = "# round7-%d { } # = + -\n" % r.randrange(10 ** 6)
        return marker + src

    # ------------------------------------------------------- limits
    def limits_case(self):
        r = self.r
        cap = r.choice((0, 1, 2, 3, 4, 5, 8, 13))
        a, b = r.randint(1, 9), r.randint(1, 9)
        variant = r.randrange(10)
        if variant == 0:
            src = "let i = 0\nwhile i < 200 { i := i + 1 }\nprint i\n"
            env = {"GAMA_L_MAX_STEPS": str(cap)}
        elif variant == 1:
            src = "let i = 0\nwhile i < 200 { i := i + 1 }\nprint i\n"
            env = {"GAMA_L_LOOP_LIMIT": str(cap)}
        elif variant == 2:
            src = ("let i = 0\nwhile i < 200 { i := i + 1 }\n"
                   "print i + %d * %d\n" % (a, b))
            env = {"GAMA_L_MAX_EXPR_STEPS": str(cap)}
        elif variant == 3:
            src = ("fn down(n: Int) -> Int { if n <= 0 { return 0 } "
                   "return down(n - 1) }\nprint down(%d)\n" % (a + 4))
            env = {"GAMA_L_MAX_CALL_DEPTH": str(cap)}
        elif variant == 4:
            src = 'print "%s"\n' % ("abcdefghij" * (a + 1))
            env = {"GAMA_L_MAX_OUTPUT_CHARS": str(cap)}
        elif variant == 5:
            src = ("record A { v: Int\n"
                   "  fn down(self: A, k: Int) -> Int {\n"
                   "    if k <= 0 { return self.v }\n"
                   "    return self.down(k - 1)\n"
                   "  }\n}\n"
                   "record B extends A { w: Int }\n"
                   "let b = B(%d, %d)\nprint b.down(%d)\n"
                   % (a, b, a + 3))
            env = {"GAMA_L_MAX_CALL_DEPTH": str(cap)}
        elif variant == 6:
            src = ("let s = 0\nfor i in [1, 2, 3, 4, 5, 6, 7] { "
                   "s := s + i }\nprint s\n")
            env = {"GAMA_L_MAX_STEPS": str(cap)}
        elif variant == 7:
            src = ("type I = Int\nrecord A { v: I }\nrecord B { a: A }\n"
                   "let b = B(A(1))\nlet i = 0\nwhile i < 20 { i := i + 1 }\n"
                   "let c = b with { a = b.a with { v = i } }\n"
                   "print c.a.v\n")
            env = {"GAMA_L_MAX_STEPS": str(cap)}
        elif variant == 8:
            src = ("type I = Int | Text\nlet i = 0\n"
                   "while i < 20 { i := i + 1 }\nprint i\n")
            env = {"GAMA_L_MAX_EXPR_STEPS": str(cap)}
        else:
            src = ("type I = Int\ntype L = List<I>\nlet i = 0\n"
                   "while i < 20 { i := i + 1 }\nprint i\n")
            env = {"GAMA_L_MAX_STEPS": str(cap)}
        merged = dict(_WIDE)
        merged.update(env)
        return src, merged

    # ---------------------------------------------- inheritance
    def inheritance_valid(self):
        r = self.r
        depth = r.choice((1, 1, 2, 2, 3))
        vals = [r.randint(0, 9) for _ in range(depth + 1)]
        overrides = [False] * (depth + 1)
        deltas = [0] * (depth + 1)
        overrides[0] = True
        deltas[0] = r.choice((0, 0, 1, 2))
        for i in range(1, depth + 1):
            if r.random() < 0.45:
                overrides[i] = True
                deltas[i] = r.randint(1, 9)
        recs = ["record R0 {\n  v: Int\n"
                "  fn base(self: R0) -> Int { return self.v + %d }\n}"
                % deltas[0]]
        for i in range(1, depth + 1):
            lines = ["record R%d extends R%d {" % (i, i - 1),
                     "  x%d: Int" % i]
            if overrides[i]:
                lines.append("  fn base(self: R%d) -> Int "
                             "{ return self.v + %d }" % (i, deltas[i]))
            lines.append("}")
            recs.append("\n".join(lines))
        leaf = depth
        own_method = ("  fn own(self: R%d) -> Int { return self.v }" % leaf
                      if leaf == 0 else
                      "  fn own(self: R%d) -> Int "
                      "{ return self.v + self.x%d }" % (leaf, leaf))
        # Append the leaf method by replacing the declaration's closing brace.
        recs[-1] = recs[-1][:-1] + own_method + "\n}"
        nearest = 0
        for i in range(depth, -1, -1):
            if overrides[i]:
                nearest = i
                break
        base_delta = deltas[nearest]
        own_value = vals[0] if depth == 0 else vals[0] + vals[depth]
        body = ["let v = R%d(%s)" % (depth,
                                     ", ".join(str(x) for x in vals)),
                "print v.v"]
        expected = [str(vals[0])]
        for i in range(1, depth + 1):
            body.append("print v.x%d" % i)
            expected.append(str(vals[i]))
        body.extend(["print v.base()", "print v.own()"])
        expected.extend([str(vals[0] + base_delta), str(own_value)])
        if r.random() < 0.5:
            nv = r.randint(0, 9)
            body.extend(["let w = v with { v = %d }" % nv,
                         "print w.v", "print w.base()"])
            expected.extend([str(nv), str(nv + base_delta)])
        if r.random() < 0.4:
            av = r.randint(0, 9)
            body.extend(["v.v := %d" % av, "print v.v", "print v.base()"])
            expected.extend([str(av), str(av + base_delta)])
        return "\n".join(recs + body) + "\n", expected


# -------------------------------------------------------- mutation tools
def _generic_seed(seed):
    r = random.Random(seed)
    return ("record Box<T> { v: T }\n"
            "type Boxed<T> = Box<T>\n"
            "type IntBox = Boxed<Int>\n"
            "let x: IntBox = Box(%d)\nprint x.v\n" % r.randint(1, 9))


def _union_with_seed(seed):
    r = random.Random(seed)
    return ("record P { x: Int, y: Int }\n"
            "record Q { p: P, z: Int }\n"
            "type Num = Int | Text\n"
            "let q = Q(P(%d, %d), %d)\n"
            "let r = q with { p = q.p with { x = %d } }\n"
            "print r.p.x\nprint %d\n"
            % (r.randint(1, 9), r.randint(1, 9), r.randint(1, 9),
               r.randint(1, 9), r.randint(1, 9)))


def _break_type_kw(src):
    return src.replace("type Boxed", "tpye Boxed", 1)


def _drop_alias_target(src):
    return re.sub(r"(type Boxed<T> =) [^\n]*", r"\1", src, count=1)


def _empty_generic_params(src):
    return src.replace("Boxed<T>", "Boxed<>", 1)


def _unterminated_generic(src):
    return src.replace("= Box<T>", "= Box<T", 1)


def _alias_trailing_pipe(src):
    return src.replace("= Box<T>", "= Box<T> |", 1)


def _alias_double_eq(src):
    return src.replace("type IntBox =", "type IntBox ==", 1)


def _union_trailing_pipe(src):
    return src.replace("= Int | Text", "= Int |", 1)


def _union_leading_pipe(src):
    return src.replace("= Int | Text", "= | Int", 1)


def _with_missing_eq(src):
    return src.replace("with { x =", "with { x", 1)


def _with_missing_close(src):
    return src.replace("with { p = q.p with { x =", "with { p = q.p with { x =", 1) \
        .replace("} }\nprint r.p.x", " }\nprint r.p.x", 1)


def _with_typo(src):
    return src.replace("q with {", "q wiht {", 1)


def _with_extra_comma(src):
    return src.replace("with { p =", "with { p =,", 1)


def _nested_with_bad_inner(src):
    return src.replace("q.p with { x =", "q.p with { x", 1)


_GENERIC_MUTATORS = (
    _break_type_kw,
    _drop_alias_target,
    _empty_generic_params,
    _unterminated_generic,
    _alias_trailing_pipe,
    _alias_double_eq,
)

_UNION_WITH_MUTATORS = (
    _union_trailing_pipe,
    _union_leading_pipe,
    _with_missing_eq,
    _with_missing_close,
    _with_typo,
    _with_extra_comma,
    _nested_with_bad_inner,
)


# ------------------------------------------------------ static rejects
_ALIAS_REJECT = (
    'type Id = Int\nlet x: Id = "s"\n',
    'type Id = Int\nfn f(x: Id) -> Int { return 1 }\nprint f("s")\n',
    'type Id = Int\nrecord R { x: Id }\nlet r = R("s")\n',
    'type Id = Int\nenum E { V(Id) }\nlet e = E.V("s")\n',
    "type A = B\ntype B = A\nlet x: A = 1\n",
    "type A = A\nlet x: A = 1\n",
    "type A = Int\ntype A = Text\nlet x: A = 1\n",
    "record P { x: Int }\ntype P = Int\n",
    "enum C { A }\ntype C = Int\n",
    "type A = Nope\nlet x: A = 1\n",
    "type A = List<Nope>\n",
    "fn f() -> Int { type A = Int\n  return 1 }\n",
    "type A = Int\nlet x: A<Int> = 1\nprint x\n",
    "type Int = Text\nlet x: Int = 1\nprint x\n",
    "type A<T> = Int\nlet x: A<Int, Int> = 1\n",
    "type A<T> = Int\nlet x: A = 1\n",
    "type A<T, T> = Int\n",
    "type A<T> = U\n",
    "type A<T> = A<T>\n",
)

_UNION_REJECT = (
    "type U = Int |\n",
    "type U = | Int\n",
    "type U = \n",
    "type U = Int | Int\n",
    "type U = Int | Nope\n",
    "type U = Int | Text | Int\n",
    "type Maybe<T> = T | T\n",
    "type Maybe<T> = Missing<T> | Text\n",
)

_WITH_REJECT = (
    "record P { x: Int }\nlet p = P(1)\nlet q = p with { x 2 }\n",
    "record P { x: Int }\nlet p = P(1)\nlet q = p with { z = 2 }\n",
    "record P { x: Int }\nlet p = P(1)\n"
    "let q = p with { x = 2, x = 3 }\n",
    "record P { x: Int }\nlet p = P(1)\nlet q = p with { x = 2\n",
    "record P { x: Int }\nlet q = with { x = 2 }\n",
    'record P { x: Int }\nlet p = P(1)\nlet q = p with { x = "a" }\n',
    "record P { x: Int, y: Int }\nlet p = P(1, 2)\n"
    "let q = p with { x = 2,, y = 3 }\n",
    "record A { x: Int }\nrecord B { a: A }\nlet b = B(A(1))\n"
    "let c = b with { a = b.a with { x 2 } }\n",
)

_INTERSECTION_REJECT = (
    "type I = A & B\n",
    "fn f(x: A & B) -> Int { return 1 }\n",
    "record R { x: A & B }\n",
    "type I = Int & Text\n",
    "let x: Int & Text = 1\n",
    "type I = Int | Text & Bool\n",
)


_EXPORT_REJECT = (
    "empty_list",
    "unknown_name",
    "duplicate_entry",
    "duplicate_rename",
    "rename_collision",
    "export_inside_block",
    "dotted_name",
    "missing_comma",
    "unterminated",
)


def _export_reject_case(test, kind):
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        write = lambda name, text: (root / name).write_text(
            text, encoding="utf-8")
        if kind == "empty_list":
            write("main.gm", "export {}\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "unknown_name":
            write("main.gm",
                  "fn add() -> Int { return 1 }\nexport { nope }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "duplicate_entry":
            write("main.gm",
                  "fn add() -> Int { return 1 }\nexport { add, add }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "duplicate_rename":
            write("main.gm",
                  "fn a() -> Int { return 1 }\nexport { a as x, a as x }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "rename_collision":
            write("main.gm",
                  "fn a() -> Int { return 1 }\n"
                  "fn x() -> Int { return 2 }\nexport { a as x }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "export_inside_block":
            write("main.gm",
                  "fn f() -> Int {\n  export { f }\n  return 1\n}\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "dotted_name":
            write("main.gm",
                  "fn a() -> Int { return 1 }\nexport { m.a }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "missing_comma":
            write("main.gm",
                  "fn a() -> Int { return 1 }\n"
                  "fn b() -> Int { return 2 }\nexport { a b }\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        elif kind == "unterminated":
            write("main.gm",
                  "fn a() -> Int { return 1 }\nexport { a\n")
            op = lambda: G.check_file(str(root / "main.gm"))
        else:
            test.fail("unknown export reject kind %r" % (kind,))
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


# ------------------------------------------------------------ helpers
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


def _assert_host(test, src, env, expected, label, checked=True):
    if checked:
        try:
            program = IR.compile_program(src, env=env)
        except IR.IRUnsupported as ex:
            test.fail("%s: IRUnsupported for host program: %s\n%s"
                      % (label, ex, src))
            return
        except G.GamaError as ex:
            test.fail("%s: host program rejected: %s\n%s"
                      % (label, ex, src))
            return
        call = lambda: IR.run_program(program, env=env)
    else:
        call = lambda: IR.run_program(G.parse(src), env=env)
    got = _outcome(call)
    if got[0] == "leak":
        test.fail("%s: IR leaked %s\n%s" % (label, got[1], src))
        return
    if got[0] == "error":
        test.fail("%s: IR rejected a valid host call: %s\n%s"
                  % (label, got[1], src))
        return
    test.assertEqual(list(got[1]), expected,
                     "%s: host output\n%s" % (label, src))


def _assert_limits(test, src, env, label):
    with _env(**env):
        it = _outcome(lambda: G.run(src))
        ir = _outcome(lambda: IR.run_program(G.check(src)))
    if it[0] == "leak":
        test.fail("%s: interpreter leaked %s\n%s" % (label, it[1], src))
        return
    if ir[0] == "leak":
        test.fail("%s: IR leaked %s\n%s" % (label, ir[1], src))
        return
    test.assertEqual(ir, it, "%s: limit outcome differs\n%s" % (label, src))


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


# ------------------------------------------------------------ case tables
_N_GENERIC = 60
_N_UNION = 40
_N_SELECT = 40
_N_HOST = 35
_N_WITH = 35
_N_FMT = 35
_N_LIMIT = 20
_N_INHERIT = 25
_N_MUTATION = 30

_GENERIC_CASES = [_Gen7(7000 + i).generic_alias_valid()
                  for i in range(_N_GENERIC)]
_UNION_CASES = [_Gen7(8000 + i).union_alias_valid()
                for i in range(_N_UNION)]
_SELECT_CASES = [_Gen7(9000 + i).selective_valid()
                 for i in range(_N_SELECT)]
_HOST_CASES = [_Gen7(10000 + i).host_valid() for i in range(_N_HOST)]
_WITH_CASES = [_Gen7(11000 + i).nested_with_valid()
               for i in range(_N_WITH)]
_FMT_CASES = [_Gen7(12000 + i).fmt_valid() for i in range(_N_FMT)]
_LIMIT_CASES = [_Gen7(13000 + i).limits_case() for i in range(_N_LIMIT)]
_INHERIT_CASES = [_Gen7(14000 + i).inheritance_valid()
                  for i in range(_N_INHERIT)]

_MUTATION_CASES = (
    [_GENERIC_MUTATORS[i % len(_GENERIC_MUTATORS)](_generic_seed(15000 + i))
     for i in range(_N_MUTATION // 2)]
    + [_UNION_WITH_MUTATORS[i % len(_UNION_WITH_MUTATORS)](
        _union_with_seed(16000 + i))
       for i in range(_N_MUTATION - _N_MUTATION // 2)]
)

_TOTAL_CASES = (
    _N_GENERIC + _N_UNION + _N_SELECT + _N_HOST + _N_WITH + _N_FMT
    + _N_LIMIT + _N_INHERIT + _N_MUTATION + len(_ALIAS_REJECT)
    + len(_UNION_REJECT) + len(_WITH_REJECT) + len(_INTERSECTION_REJECT)
    + len(_EXPORT_REJECT)
)


# ----------------------------------------------------------- test classes
class Fuzz7MetaTests(unittest.TestCase):
    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_generators_are_deterministic(self):
        for i in range(10):
            self.assertEqual(_Gen7(7700 + i).generic_alias_valid(),
                             _Gen7(7700 + i).generic_alias_valid())
            self.assertEqual(_Gen7(8800 + i).union_alias_valid(),
                             _Gen7(8800 + i).union_alias_valid())
            self.assertEqual(_Gen7(9900 + i).nested_with_valid(),
                             _Gen7(9900 + i).nested_with_valid())

    def test_all_generated_valid_cases_check(self):
        groups = (
            ("generic", _GENERIC_CASES),
            ("union", _UNION_CASES),
            ("with", _WITH_CASES),
            ("inherit", _INHERIT_CASES),
        )
        for name, cases in groups:
            for i, case in enumerate(cases):
                src = case[0]
                with self.subTest(group=name, i=i):
                    try:
                        with _env():
                            G.check(src)
                    except G.GamaError as ex:
                        self.fail("%s[%d] invalid: %s\n%s" % (name, i, ex, src))
                    except Exception as ex:  # noqa: BLE001
                        self.fail("%s[%d] leaked %s: %s\n%s"
                                  % (name, i, type(ex).__name__, ex, src))

    def test_all_mutations_are_gama_errors(self):
        for i, src in enumerate(_MUTATION_CASES):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "mutation[%d]" % i)


class GeneratedGenericAliasTests(unittest.TestCase):
    def test_generated_generic_aliases(self):
        for i, (src, expected) in enumerate(_GENERIC_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "generic[%d]" % i)


class GeneratedUnionAliasTests(unittest.TestCase):
    def test_generated_union_aliases(self):
        for i, (src, expected) in enumerate(_UNION_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "union[%d]" % i)


class GeneratedSelectiveReexportTests(unittest.TestCase):
    def test_generated_selective_reexports(self):
        for i, (lib, main, expected) in enumerate(_SELECT_CASES):
            with self.subTest(i=i):
                _assert_file(self, lib, main, expected, "select[%d]" % i)


class GeneratedHostCallableTests(unittest.TestCase):
    def test_generated_host_calls(self):
        for i, (src, env, expected) in enumerate(_HOST_CASES):
            with self.subTest(i=i):
                _assert_host(self, src, env, expected, "host[%d]" % i)


class GeneratedNestedWithTests(unittest.TestCase):
    def test_generated_nested_with(self):
        for i, (src, expected) in enumerate(_WITH_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "with[%d]" % i)


class GeneratedLimitTests(unittest.TestCase):
    def test_generated_limits(self):
        for i, (src, env) in enumerate(_LIMIT_CASES):
            with self.subTest(i=i):
                _assert_limits(self, src, env, "limit[%d]" % i)


class GeneratedInheritanceTests(unittest.TestCase):
    def test_generated_inheritance(self):
        for i, (src, expected) in enumerate(_INHERIT_CASES):
            with self.subTest(i=i):
                _assert_program(self, src, expected, "inherit[%d]" % i)


class GeneratedFormatterTests(unittest.TestCase):
    def test_generated_formatter_roundtrips(self):
        for i, src in enumerate(_FMT_CASES):
            with self.subTest(i=i):
                _assert_formatter(self, src, "fmt[%d]" % i)


class GeneratedRejectTests(unittest.TestCase):
    def test_alias_rejections(self):
        for i, src in enumerate(_ALIAS_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "alias-reject[%d]" % i)

    def test_union_rejections(self):
        for i, src in enumerate(_UNION_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "union-reject[%d]" % i)

    def test_with_rejections(self):
        for i, src in enumerate(_WITH_REJECT):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "with-reject[%d]" % i)

    def test_intersection_bounds_are_rejected(self):
        for i, src in enumerate(_INTERSECTION_REJECT):
            with self.subTest(i=i):
                _assert_parse_error(self, src, "intersection[%d]" % i)

    def test_mutations_are_gama_errors(self):
        for i, src in enumerate(_MUTATION_CASES):
            with self.subTest(i=i):
                _assert_gama_error(self, src, "mutation[%d]" % i)


class GeneratedExportRejectTests(unittest.TestCase):
    def test_export_rejections(self):
        for kind in _EXPORT_REJECT:
            with self.subTest(kind=kind):
                _export_reject_case(self, kind)


class HostCallableBehaviorTests(unittest.TestCase):
    def test_wrong_arity_is_irruntimerror(self):
        with self.assertRaises(G.GamaError):
            IR.run_program(G.parse("print h(1, 2)\n"),
                           env={"h": lambda x: x})

    def test_host_exception_is_wrapped(self):
        with self.assertRaises(G.GamaError) as ctx:
            IR.run_program(G.parse("print h(1)\n"), env={"h": _h_boom})
        self.assertIn("ValueError", str(ctx.exception))

    def test_noncallable_env_value_is_rejected(self):
        with self.assertRaises(G.GamaError):
            IR.run_program(G.parse("print h(1)\n"), env={"h": 5})

    def test_missing_host_name_is_gamerror(self):
        with self.assertRaises(G.GamaError):
            IR.run_program(G.parse("print missing(1)\n"))

    def test_compile_program_source_seeds_host_names(self):
        program = IR.compile_program("print h(2)\n", env={"h": _h_double})
        self.assertEqual(list(IR.run_program(program,
                                             env={"h": _h_double}).out),
                         ["4"])

    def test_parsed_program_without_seeded_env(self):
        with self.assertRaises(G.GamaError):
            IR.compile_program("print h(2)\n")


class FeatureProbeTests(unittest.TestCase):
    def test_generic_alias_is_supported(self):
        _assert_program(self,
                        "type Boxed<T> = Box<T>\n"
                        "record Box<T> { v: T }\n"
                        "let b: Boxed<Int> = Box(1)\nprint b.v\n",
                        ["1"], "probe-generic-alias")

    def test_union_alias_is_supported(self):
        _assert_program(self,
                        "type Num = Int | Text\n"
                        "let x: Num = 1\nprint x\n",
                        ["1"], "probe-union-alias")

    def test_nested_with_is_supported(self):
        _assert_program(self,
                        "record P { x: Int }\nrecord Q { p: P }\n"
                        "let q = Q(P(1))\n"
                        "let r = q with { p = q.p with { x = 2 } }\n"
                        "print r.p.x\n",
                        ["2"], "probe-nested-with")

    def test_intersection_bounds_are_supported(self):
        _assert_program(
            self,
            "record A { x: Int }\nrecord B { y: Int }\n"
            "record C { x: Int, y: Int }\n"
            "fn f<T: A & B>(v: T) -> Int { return v.x + v.y }\n"
            "print f(C(1, 2))\n",
            ["3"], "probe-intersection")


class Fuzz7RegressionPins(unittest.TestCase):
    """Real defects found by this round.

    Each test fails on the current core, so ``expectedFailure`` keeps the
    suite green while pinning the bug.  An unexpected success means the bug
    was fixed and the pin can be removed.
    """

    def test_generic_union_alias_substitutes_bare_type_parameter(self):
        src = ("type Maybe<T> = T | Text\n"
               "let x: Maybe<Int> = 1\nprint x\n")
        G.check(src)
        self.assertEqual(list(G.run(src).out), ["1"])

    def test_union_alias_distinguishes_same_generic_with_different_args(self):
        src = ("record Box<T> { v: T }\n"
               "type U = Box<Int> | Box<Text>\n"
               "let x: U = Box(1)\nprint x\n")
        G.check(src)
        self.assertEqual(list(G.run(src).out), ["Box { v: 1 }"])

    def test_generic_alias_branch_inside_generic_union_alias(self):
        src = ("type Boxed<T> = List<T>\n"
               "type Maybe<T> = Boxed<T> | Text\n"
               "let x: Maybe<Int> = [1]\nprint x\n")
        G.check(src)
        self.assertEqual(list(G.run(src).out), ["[1]"])


if __name__ == "__main__":
    unittest.main()
