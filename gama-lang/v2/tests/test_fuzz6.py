"""Deterministic property/fuzz round 6 for the current GAMA-L 2.0 surface.

Rounds 1-5 (``test_fuzz.py`` ... ``test_fuzz5.py``) covered the original core,
builtins/imports/limits, formatter/IR parity, structural records, bounds,
prove/verify, record methods, nominal inheritance, export enums/renames and
runtime-limit parity.  This round targets the features that are now fully
integrated and were only *probed* (or not covered) by round 5:

* selective re-export lists (``export { add, pi as PI }``), including multiple
  lists, plain-import compatibility, records/enums/inheritance/type aliases
  through the file loader, and IR parity where the backend supports it;
* top-level type aliases (simple, chained, generic targets, ``List``,
  ``Result``, record fields, enum payloads, parameters and returns), with
  strict interpreter/IR output parity;
* IR host callables: direct calls, variables, expression callees,
  higher-order use, returned callables, arity/error wrapping and
  ``compile_program(source)`` checking seeded from ``env``;
* IR runtime limits on more program shapes (while/for loops, recursion,
  inherited record methods, output caps) with byte-for-byte outcome parity;
* nominal record inheritance (flattening, subtyping, overrides, ``with``,
  assignment, namespaced records) as a second independent sample;
* formatter round-trips for aliases, selective lists and their combinations;
* malformed mutations that must fail inside the ``GamaError`` family.

Graceful probes are used only for features the surface does **not** implement
(generic aliases, union aliases): they must be rejected with ``GamaError``
instead of being skipped.  Everything that is implemented is exercised
directly; an unexpected ``IRUnsupported`` for a supported case is a failure.

Invariants exercised:

* Generated *valid* sources may raise only the public ``GamaError`` family from
  ``parse``/``check``/``run``; a bare Python exception is a failure.
* IR parity holds for aliases, inheritance and selective re-exports; an
  unexpected ``IRUnsupported`` for a supported case is a failure.
* IR host calls return the documented value and wrap host exceptions in
  ``IRRuntimeError`` (a ``GamaError``).
* Malformed mutations fail the parser with ``GamaError``.
* Runtime-limit failures match the interpreter byte for byte on the same
  program and the same caps.
* The formatter is idempotent, keeps the program checking, and preserves the
  reference interpreter output.

Everything is seeded.  Two real defects surfaced by this round are pinned as
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


def _host_double(x):
    return x * 2


def _host_plus3(x):
    return x + 3


def _host_pair(x, y):
    return x * 10 + y


def _host_mk(n):
    return [n, n + 1]


def _host_label(n):
    return "v%d" % n


class _Gen6:
    """Seeded generator for the round-6 feature cases."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    # ------------------------------------------- selective re-export lists
    def selective_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(14)
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
                    'let p = m.Pt(%d, %d)\n'
                    'print p.x + p.y\n' % (a, b))
            expected = [str(a + b)]
        elif variant == 3:
            color = ("Red", "Green", "Blue")[r.randrange(3)]
            lib = ("enum Color { Red, Green, Blue }\n"
                   "export { Color as Hue }\n")
            main = 'import "lib.gm" as m\nprint m.Hue.%s\n' % color
            expected = ["m.Hue.%s" % color]
        elif variant == 4:
            lib = ("fn f(a: Int) -> Int { return a + 1 }\n"
                   "fn g(a: Int) -> Int { return a * 2 }\n"
                   "export { f as inc, g as dbl }\n")
            main = ('import "lib.gm" as m\n'
                    'print m.inc(%d)\nprint m.dbl(%d)\n' % (a, a))
            expected = [str(a + 1), str(a * 2)]
        elif variant == 5:
            lib = ("fn add(a: Int, b: Int) -> Int { return a + b }\n"
                   "fn hidden(a: Int) -> Int { return a * 100 }\n"
                   "export { add }\n")
            main = ('import "lib.gm"\n'
                    'print add(%d, %d)\nprint hidden(%d)\n' % (a, b, a))
            expected = [str(a + b), str(a * 100)]
        elif variant == 6:
            lib = ('record P { x: Int }\ntype Q = P\n'
                   'fn mk(n: Int) -> Q { return P(n) }\n'
                   'export { Q, mk }\n')
            main = ('import "lib.gm" as m\n'
                    'let p: m.Q = m.mk(%d)\nprint p.x\n' % a)
            expected = [str(a)]
        elif variant == 7:
            lib = ('type Ints = List<Int>\n'
                   'fn total(xs: Ints) -> Int {\n'
                   '  let s = 0\n  for x in xs { s := s + x }\n'
                   '  return s\n}\n'
                   'export { Ints, total }\n')
            main = ('import "lib.gm" as m\n'
                    'let xs: m.Ints = [%d, %d, %d]\n'
                    'print m.total(xs)\n' % (a, b, a + b))
            expected = [str(2 * (a + b))]
        elif variant == 8:
            lib = ('type R = Result<Int, Text>\n'
                   'fn good(n: Int) -> R { return Ok(n) }\n'
                   'export { R, good }\n')
            main = ('import "lib.gm" as m\n'
                    'let r: m.R = m.good(%d)\nprint r\n' % a)
            expected = ["Ok(%d)" % a]
        elif variant == 9:
            lib = ('record Animal { name: Text }\n'
                   'record Dog extends Animal { breed: Text }\n'
                   'fn build() -> Dog { return Dog("Rex", "lab") }\n'
                   'export { Animal as Beast, Dog as Hound, build }\n')
            main = ('import "lib.gm" as m\n'
                    'let d = m.build()\n'
                    'let a: m.Beast = d\n'
                    'print a.name\nprint d.breed\n')
            expected = ["Rex", "lab"]
        elif variant == 10:
            lib = ('enum Opt { Some(Int), None }\n'
                   'fn wrap(n: Int) -> Opt { return Opt.Some(n) }\n'
                   'export { Opt as Maybe, wrap }\n')
            main = ('import "lib.gm" as m\n'
                    'let o = m.wrap(%d)\n'
                    'match o {\n'
                    '  m.Maybe.Some(v) => { print v },\n'
                    '  m.Maybe.None => { print "none" }\n'
                    '}\n' % a)
            expected = [str(a)]
        elif variant == 11:
            lib = ('fn add(a: Int, b: Int) -> Int { return a + b }\n'
                   'let pi: Decimal = 3.14\n'
                   'export {\n  add,\n  pi as PI,\n}\n')
            main = ('import "lib.gm" as m\n'
                    'print m.add(%d, %d)\nprint m.PI\n' % (a, b))
            expected = [str(a + b), "3.14"]
        elif variant == 12:
            lib = ('type Id = Int\n'
                   'fn mk(x: Id) -> Id { return x }\n'
                   'export { Id as Ident, mk as build }\n')
            main = ('import "lib.gm" as m\n'
                    'let x: m.Ident = m.build(%d)\nprint x\n' % a)
            expected = [str(a)]
        else:
            lib = ('fn add(a: Int, b: Int) -> Int { return a + b }\n'
                   'fn twice(x: Int) -> Int { return add(x, x) }\n'
                   'export { add as plus }\nexport { twice }\n')
            main = ('import "lib.gm" as m\n'
                    'print m.plus(%d, %d)\nprint m.twice(%d)\n'
                    % (a, b, a))
            expected = [str(a + b), str(a + a)]
        return lib, main, expected

    # ------------------------------------------------------ type aliases
    def alias_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(15)
        if variant == 0:
            src = "type UserId = Int\nlet x: UserId = %d\nprint x\n" % a
        elif variant == 1:
            src = ("type A = Int\ntype B = A\ntype C = B\n"
                   "let x: C = %d\nprint x\n" % a)
        elif variant == 2:
            src = 'type Name = Text\nlet x: Name = "hi%d"\nprint x\n' % a
        elif variant == 3:
            flag = ("true", "false")[r.randrange(2)]
            src = "type Flag = Bool\nlet x: Flag = %s\nprint x\n" % flag
        elif variant == 4:
            src = "type D = Decimal\nlet x: D = 1.5\nprint x\n"
        elif variant == 5:
            src = ("record Box<T> { value: T }\ntype IntBox = Box<Int>\n"
                   "let b: IntBox = Box(%d)\nprint b.value\n" % a)
        elif variant == 6:
            src = ("type Ints = List<Int>\n"
                   "let xs: Ints = [%d, %d]\nprint xs\n" % (a, b))
        elif variant == 7:
            src = ("type A = Int\ntype B = List<A>\n"
                   "let xs: B = [%d, %d]\nprint xs\n" % (a, b))
        elif variant == 8:
            src = ("type R = Result<Int, Text>\n"
                   "let r: R = Ok(%d)\nprint r\n" % a)
        elif variant == 9:
            src = ("type I = Int\nfn f(x: I) -> I { return x + 1 }\n"
                   "print f(%d)\n" % a)
        elif variant == 10:
            src = ("type I = Int\nrecord R { x: I, y: I }\n"
                   "let r = R(%d, %d)\nprint r.x + r.y\n" % (a, b))
        elif variant == 11:
            src = ("type I = Int\nenum E { V(I) }\n"
                   "let e = E.V(%d)\nprint e\n" % a)
        elif variant == 12:
            pick = ("A", "B")[r.randrange(2)]
            src = ("enum C { A, B }\ntype MyC = C\n"
                   "let c: MyC = C.%s\n"
                   "match c {\n"
                   "  C.A => { print \"a\" },\n"
                   "  C.B => { print \"b\" }\n"
                   "}\n" % pick)
        elif variant == 13:
            src = "type A = Any\nlet x: A = %d\nprint x\n" % a
        else:
            src = ("record Point { x: Int, y: Int }\n"
                   "type Coord = Point\ntype Coords = List<Coord>\n"
                   "let p: Coord = Point(%d, %d)\n"
                   "let ps: Coords = [p]\n"
                   "print len(ps)\nprint p.x + p.y\n" % (a, b))
        with _env():
            G.check(src)
            expected = G.run(src).out
        return src, list(expected)

    # -------------------------------------------------- IR host callables
    def host_valid(self):
        r = self.r
        a, b = r.randint(0, 9), r.randint(0, 9)
        variant = r.randrange(13)
        h = _host_double
        plus = _host_plus3
        pair = _host_pair
        mk = _host_mk
        label = _host_label
        if variant == 0:
            src, env, expected = "print h(%d)\n" % a, {"h": h}, [str(a * 2)]
        elif variant == 1:
            src = ("let x = plus(%d)\nprint x + %d\n" % (a, b))
            env, expected = {"plus": plus}, [str(a + 3 + b)]
        elif variant == 2:
            src, env, expected = ("let f = h\nprint f(%d)\n" % a,
                                  {"h": h}, [str(a * 2)])
        elif variant == 3:
            src, env, expected = ("print (h)(%d)\n" % a,
                                  {"h": h}, [str(a * 2)])
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
            src = ("let f = h\nlet g = f\nprint g(%d)\n" % a)
            env, expected = {"h": h}, [str(a * 2)]
        elif variant == 10:
            src = "print h(%d) + h(%d)\n" % (a, b)
            env, expected = {"h": h}, [str(a * 2 + b * 2)]
        elif variant == 11:
            src = ("fn inc(x: Any) -> Any { return x + 1 }\n"
                   "print inc(h(%d))\n" % a)
            env, expected = {"h": h}, [str(a * 2 + 1)]
        else:
            src = ("print pair(%d, %d) + plus(%d)\n" % (a, b, a))
            env = {"pair": pair, "plus": plus}
            expected = [str(a * 10 + b + a + 3)]
        return src, env, expected

    # ------------------------------------------------------- IR limits
    def limits_case(self):
        r = self.r
        cap = r.choice((0, 1, 2, 3, 4, 5, 8, 13))
        a, b = r.randint(1, 9), r.randint(1, 9)
        variant = r.randrange(8)
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
            src = ('record A {\n  v: Int\n'
                   '  fn down(self: A, k: Int) -> Int {\n'
                   '    if k <= 0 { return self.v }\n'
                   '    return self.down(k - 1)\n'
                   '  }\n}\n'
                   'record B extends A { w: Int }\n'
                   'let b = B(%d, %d)\n'
                   'print b.down(%d)\n' % (a, b, a + 3))
            env = {"GAMA_L_MAX_CALL_DEPTH": str(cap)}
        elif variant == 6:
            src = ("let s = 0\nfor i in [1, 2, 3, 4, 5, 6, 7] { "
                   "s := s + i }\nprint s\n")
            env = {"GAMA_L_MAX_STEPS": str(cap)}
        else:
            src = ("record A { v: Int }\n"
                   "record B extends A { w: Int }\n"
                   "let b = B(%d, %d)\n"
                   "print b.v + b.w\nprint b\n" % (a, b))
            env = {}
        merged = dict(_WIDE)
        merged.update(env)
        return src, merged

    # ---------------------------------------------- record inheritance
    def inheritance_valid(self):
        r = self.r
        depth = r.choice((1, 1, 2, 2, 3))
        namespace = "geo." if r.random() < 0.15 else ""
        recs = []
        for i in range(depth + 1):
            name = "%sR%d" % (namespace, i)
            own = ["v"] if i == 0 else ["x%d" % i]
            inherited = "" if i == 0 else " extends %sR%d" % (namespace, i - 1)
            lines = ["record %s%s {" % (name, inherited),
                     "  %s: Int" % own[0]]
            if i == 0:
                lines.append(
                    "  fn base(self: %s) -> Int { return self.v }" % name)
            elif r.random() < 0.35:
                lines.append(
                    "  fn base(self: %s) -> Int { return self.v + %d }"
                    % (name, r.randint(1, 9)))
            lines.append(
                "  fn own%d(self: %s) -> Int { return self.v + self.%s }"
                % (i, name, own[0]))
            lines.append("}")
            recs.append("\n".join(lines))
        last = "%sR%d" % (namespace, depth)
        values = [str(r.randint(0, 9)) for _ in range(depth + 1)]
        body = ["let v = %s(%s)" % (last, ", ".join(values)),
                "print v", "print v.v"]
        for i in range(1, depth + 1):
            if r.random() < 0.7:
                body.append("print v.x%d" % i)
        body.append("print v.base()")
        if r.random() < 0.3:
            body.append("print v.own%d()" % depth)
        if r.random() < 0.5:
            body.append("let w = v with { v = %d }" % r.randint(0, 9))
            body.append("print w")
        if r.random() < 0.4:
            body.append("v.v := %d" % r.randint(0, 9))
            body.append("print v.v")
        return "\n".join(recs + body) + "\n"

    # ------------------------------------------------------ formatter
    def fmt_valid(self):
        r = self.r
        variant = r.randrange(15)
        if variant == 0:
            src = self.alias_valid()[0]
        elif variant == 1:
            src = ("type A = Int\ntype B = A\ntype C = B\n"
                   "let x: C = %d\nprint x\n" % r.randint(0, 9))
        elif variant == 2:
            src = ("record Box<T> { value: T }\ntype IntBox = Box<Int>\n"
                   "let b: IntBox = Box(%d)\nprint b.value\n" % r.randint(0, 9))
        elif variant == 3:
            src = ("type Ints = List<Int>\ntype Nested = List<Ints>\n"
                   "let xs: Ints = [1, 2]\nprint xs\n")
        elif variant == 4:
            src = self.selective_valid()[0]
        elif variant == 5:
            src = ("fn add(a: Int, b: Int) -> Int { return a + b }\n"
                   "let pi: Decimal = 3.14\n"
                   "export {\n  add,\n  pi as PI,\n}\n")
        elif variant == 6:
            src = ("fn ident(x: Id) -> Id { return x }\n"
                   "export type Id = Int as Ident\n"
                   "print ident(3)\n")
        elif variant == 7:
            src = ('type Id = Int\nfn mk(x: Id) -> Id { return x }\n'
                   'export { Id as Ident, mk as build }\n')
        elif variant == 8:
            src = ("type I = Int\n"
                   "record Base { v: I }\n"
                   "record Child extends Base { w: I }\n"
                   "let c = Child(1, 2)\nprint c.v + c.w\n")
        elif variant == 9:
            src = ('enum Color { Red, Green, Blue }\n'
                   'fn name(n: Int) -> Text {\n'
                   '  if n == 0 { return "red" }\n'
                   '  if n == 1 { return "green" }\n'
                   '  return "blue"\n}\n'
                   'export { Color as Hue, name as label }\n')
        elif variant == 10:
            src = self.inheritance_valid().replace("geo.", "")
        elif variant == 11:
            src = ("type R = Result<Int, Text>\n"
                   "fn good(n: Int) -> R { return Ok(n) }\n"
                   "let r: R = good(%d)\nprint r\n" % r.randint(0, 9))
        elif variant == 12:
            src = ('record P { x: Int }\ntype Q = P\n'
                   'fn mk(n: Int) -> Q { return P(n) }\n'
                   'export { Q, mk }\n')
        elif variant == 13:
            src = ("type A = Int\ntype B = List<A>\n"
                   "record R { xs: B }\n"
                   "let r = R([1, 2, 3])\nprint len(r.xs)\n")
        else:
            src = self.limits_case()[0]
        marker = "# round6-%d { } # = + -\n" % r.randrange(10 ** 6)
        return marker + src


# ------------------------------------------------------------- mutations
def _alias_seed(seed):
    r = random.Random(seed)
    return ("type A = Int\ntype B = List<A>\n"
            "let xs: B = [%d]\nprint xs\n" % r.randint(1, 9))


def _export_seed(seed):
    r = random.Random(seed)
    return ("fn add(a: Int, b: Int) -> Int { return a + b + %d }\n"
            "let pi: Decimal = 3.14\nexport { add, pi as PI }\n"
            % r.randint(0, 9))


def _break_type_kw(src):
    return src.replace("type", "tpye", 1)


def _drop_alias_eq(src):
    return re.sub(r"type (\w+) =", r"type \1", src, count=1)


def _drop_alias_target(src):
    return re.sub(r"(type [A-Za-z_]\w* =) [^\n]*", r"\1", src, count=1)


def _dup_alias_eq(src):
    return src.replace("=", "==", 1)


def _alias_extra_token(src):
    return src.replace("Int", "Int Int", 1)


def _alias_dotted_name(src):
    return src.replace("type A", "type a.b", 1)


def _alias_generic(src):
    return src.replace("type A = Int", "type A<T> = Int", 1)


def _alias_union(src):
    return src.replace("type A = Int", "type A = Int | Text", 1)


def _alias_stray(src):
    return src.replace("print xs", "print ) xs", 1)


def _drop_export_open(src):
    return src.replace("export {", "export ", 1)


def _drop_export_close(src):
    return src.replace(" }", "", 1)


def _export_missing_comma(src):
    return src.replace("add, pi", "add pi", 1)


def _export_dotted(src):
    return src.replace("export { add", "export { m.add", 1)


def _export_broken_as(src):
    return src.replace(" as PI", " az PI", 1)


def _export_empty(src):
    return "export {}\n" + src


def _export_in_fn(src):
    return "fn f() -> Int {\n  export { add }\n  return 1\n}\n" + src


def _export_stray(src):
    return src.replace("export {", "export { )", 1)


def _export_unterminated(src):
    return src.replace("export { add, pi as PI }",
                       "export { add, pi as PI", 1)


_ALIAS_MUTATORS = (
    _break_type_kw,
    _drop_alias_eq,
    _drop_alias_target,
    _dup_alias_eq,
    _alias_extra_token,
    _alias_dotted_name,
    _alias_generic,
    _alias_union,
    _alias_stray,
)

_EXPORT_MUTATORS = (
    _drop_export_open,
    _drop_export_close,
    _export_missing_comma,
    _export_dotted,
    _export_broken_as,
    _export_empty,
    _export_in_fn,
    _export_stray,
    _export_unterminated,
)


# ------------------------------------------------------------- helpers
def _outcome(fn):
    try:
        return ("ok", tuple(fn().out))
    except G.GamaError as ex:
        return ("error", str(ex))
    except Exception as ex:  # noqa: BLE001 - the actual invariant
        return ("leak", "%s: %s" % (type(ex).__name__, ex))


def _assert_alias_parity(test, src, expected, label):
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


def _assert_inherit_parity(test, src, label):
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
        it = _outcome(lambda: G.run(src))
        if it[0] == "leak":
            test.fail("%s: interpreter leaked %s\n%s"
                      % (label, it[1], src))
            return
        if it[0] == "error":
            test.fail("%s: interpreter rejected a valid case: %s\n%s"
                      % (label, it[1], src))
            return
        ir = _outcome(lambda: IR.run_program(ast))
    if ir[0] == "leak":
        test.fail("%s: IR leaked %s\n%s" % (label, ir[1], src))
        return
    if ir[0] == "error":
        test.fail("%s: IR rejected a supported case: %s\n%s"
                  % (label, ir[1], src))
        return
    test.assertEqual(list(ir[1]), list(it[1]),
                     "%s: IR output differs\n%s" % (label, src))


def _assert_host_case(test, src, env, expected, label, checked):
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


def _assert_limits_parity(test, src, env, label):
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


def _assert_parse_rejected(test, src, label):
    try:
        G.parse(src)
    except G.GamaError:
        return
    except Exception as ex:  # noqa: BLE001
        test.fail("%s: malformed parse leaked %s: %s\n%s"
                  % (label, type(ex).__name__, ex, src))
        return
    test.fail("%s: malformed source parsed without GamaError\n%s" % (label, src))


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


def _assert_file_parity(test, lib, main, expected, label):
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


# ----------------------------------------------------- static reject tables
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
    "type Pair<T> = T\n",
    "type N = Int | Text\n",
    "fn f() -> Int { type A = Int\n  return 1 }\n",
    "type A = Int\nlet x: A<Int> = 1\nprint x\n",
    "type Int = Text\nlet x: Int = 1\nprint x\n",
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
    "private_enum_hidden",
    "module_alias",
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
        elif kind == "private_enum_hidden":
            write("lib.gm", "export enum Color { Red }\nenum Hidden { A }\n")
            write("main.gm",
                  'import "lib.gm" as g\nprint g.Hidden.A\n')
            op = lambda: G.run_file(str(root / "main.gm"))
        elif kind == "module_alias":
            write("inner.gm", "fn a() -> Int { return 1 }\n")
            write("main.gm",
                  'import "inner.gm" as inner\nexport { inner }\n')
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


# ------------------------------------------------------------ case tables
_N_SELECT = 45
_N_ALIAS = 45
_N_HOST = 40
_N_LIMITS = 30
_N_INHERIT = 30
_N_FMT = 40
_N_ALIAS_MUT = 15
_N_EXPORT_MUT = 15

_SELECT_CASES = [_Gen6(6000 + i).selective_valid()
                 for i in range(_N_SELECT)]
_ALIAS_CASES = [_Gen6(7000 + i).alias_valid() for i in range(_N_ALIAS)]
_HOST_CASES = [_Gen6(8000 + i).host_valid() for i in range(_N_HOST)]
_LIMIT_CASES = [_Gen6(9000 + i).limits_case() for i in range(_N_LIMITS)]
_INHERIT_CASES = [_Gen6(10000 + i).inheritance_valid()
                  for i in range(_N_INHERIT)]
_FMT_CASES = [_Gen6(11000 + i).fmt_valid() for i in range(_N_FMT)]

_ALIAS_MUTATION_CASES = [
    _ALIAS_MUTATORS[i % len(_ALIAS_MUTATORS)](_alias_seed(12000 + i))
    for i in range(_N_ALIAS_MUT)]
_EXPORT_MUTATION_CASES = [
    _EXPORT_MUTATORS[i % len(_EXPORT_MUTATORS)](_export_seed(13000 + i))
    for i in range(_N_EXPORT_MUT)]

_MUTATION_CASES = _ALIAS_MUTATION_CASES + _EXPORT_MUTATION_CASES

_N_MUTATION = len(_MUTATION_CASES)

_TOTAL_CASES = (
    _N_SELECT + _N_ALIAS + _N_HOST + _N_LIMITS + _N_INHERIT + _N_FMT
    + _N_MUTATION + len(_ALIAS_REJECT) + len(_EXPORT_REJECT)
)


# ----------------------------------------------------------- test classes
class Fuzz6MetaTests(unittest.TestCase):
    def test_case_count_is_at_least_200(self):
        self.assertGreaterEqual(_TOTAL_CASES, 200, _TOTAL_CASES)

    def test_generated_case_count_is_at_least_200(self):
        generated = (_N_SELECT + _N_ALIAS + _N_HOST + _N_LIMITS
                     + _N_INHERIT + _N_FMT + _N_MUTATION)
        self.assertGreaterEqual(generated, 200, generated)

    def test_generators_are_deterministic(self):
        for seed in range(40):
            self.assertEqual(_Gen6(seed).selective_valid(),
                             _Gen6(seed).selective_valid())
            self.assertEqual(_Gen6(seed).alias_valid(),
                             _Gen6(seed).alias_valid())
            self.assertEqual(_Gen6(seed).host_valid(),
                             _Gen6(seed).host_valid())
            self.assertEqual(_Gen6(seed).limits_case(),
                             _Gen6(seed).limits_case())
            self.assertEqual(_Gen6(seed).inheritance_valid(),
                             _Gen6(seed).inheritance_valid())
            self.assertEqual(_Gen6(seed).fmt_valid(),
                             _Gen6(seed).fmt_valid())

    def test_all_generated_valid_cases_check(self):
        pool = ([(s, {}) for s in _INHERIT_CASES]
                + [c for c in _LIMIT_CASES]
                + [(lib, {}) for lib, _main, _exp in _SELECT_CASES]
                + [(s, {}) for s, _exp in _ALIAS_CASES]
                + [(s, {}) for s in _FMT_CASES])
        for idx, (src, _env_) in enumerate(pool):
            with self.subTest(case=idx):
                try:
                    G.check(src)
                except G.GamaError as ex:
                    self.fail("case %d invalid: %s\n%s" % (idx, ex, src))
                except Exception as ex:  # noqa: BLE001
                    self.fail("case %d leaked %s: %s\n%s"
                              % (idx, type(ex).__name__, ex, src))

    def test_all_mutations_are_parse_errors(self):
        for idx, src in enumerate(_MUTATION_CASES):
            with self.subTest(case=idx):
                with self.assertRaises(G.GamaError):
                    G.parse(src)


class GeneratedSelectiveReexportTests(unittest.TestCase):
    """Dynamically populated: selective re-export lists through the loader."""


class GeneratedAliasTests(unittest.TestCase):
    """Dynamically populated: type aliases with IR parity."""


class GeneratedHostCallableTests(unittest.TestCase):
    """Dynamically populated: IR host-injected Python callables."""


class GeneratedLimitsTests(unittest.TestCase):
    """Dynamically populated: IR runtime-limit parity."""


class GeneratedInheritanceTests(unittest.TestCase):
    """Dynamically populated: IR record-inheritance parity."""


class GeneratedFormatterTests(unittest.TestCase):
    """Dynamically populated: formatter round-trips (aliases/exports)."""


class GeneratedMutationTests(unittest.TestCase):
    """Dynamically populated: malformed alias/export mutations."""


class GeneratedAliasRejectTests(unittest.TestCase):
    """Dynamically populated: invalid alias programs."""


class GeneratedExportRejectTests(unittest.TestCase):
    """Dynamically populated: illegal selective re-exports."""


class HostCallableBehaviorTests(unittest.TestCase):
    """Concrete host-callable error semantics."""

    def test_wrong_arity_is_irruntimerror(self):
        with self.assertRaises(IR.IRRuntimeError) as ctx:
            IR.run_program(G.parse("print h(1, 2)\n"),
                           env={"h": lambda x: x})
        self.assertIn("expects 1..1 args, got 2", str(ctx.exception))

    def test_host_exception_is_wrapped(self):
        def boom(x):
            raise ValueError("nope")
        with self.assertRaises(IR.IRRuntimeError) as ctx:
            IR.run_program(G.parse("print boom(1)\n"), env={"boom": boom})
        self.assertIn("host callable boom raised ValueError: nope",
                      str(ctx.exception))

    def test_noncallable_env_value_is_rejected(self):
        with self.assertRaises(IR.IRRuntimeError) as ctx:
            IR.run_program(G.parse("print h(1)\n"), env={"h": 5})
        self.assertIn("h is not callable", str(ctx.exception))

    def test_callability_is_checked_before_arguments(self):
        def bad(x):
            raise AssertionError("argument must not be evaluated")
        with self.assertRaises(IR.IRRuntimeError) as ctx:
            IR.run_program(G.parse("print h(bad(1))\n"),
                           env={"h": 5, "bad": bad})
        self.assertIn("h is not callable", str(ctx.exception))

    def test_missing_host_name_is_gamerror(self):
        with self.assertRaises(G.GamaError):
            IR.run_program(G.parse("print missing(1)\n"))

    def test_compile_program_source_seeds_host_names(self):
        program = IR.compile_program(
            "print add(2, 3)\n", env={"add": lambda a, b: a + b})
        self.assertEqual(
            IR.run_program(program, env={"add": lambda a, b: a + b}).out,
            ["5"])


class FeatureProbeTests(unittest.TestCase):
    """Concrete probes for round-6 features and unsupported surfaces."""

    def test_generic_alias_is_rejected_not_skipped(self):
        with self.assertRaisesRegex(G.GamaError, "generic type aliases"):
            G.parse("type Pair<T> = T\n")

    def test_union_alias_is_rejected_not_skipped(self):
        with self.assertRaisesRegex(G.GamaError, "union types"):
            G.parse("type N = Int | Text\n")

    def test_type_alias_is_transparent_to_the_ir(self):
        src = "type Id = Int\nlet x: Id = 7\nprint x\n"
        ast = G.check(src)
        self.assertEqual(G.run(src).out, ["7"])
        self.assertEqual(IR.run_program(ast).out, ["7"])

    def test_host_callable_through_parsed_ast(self):
        src = "print host(2)\n"
        out = IR.run_program(G.parse(src),
                             env={"host": lambda x: x * 2}).out
        self.assertEqual(out, ["4"])

    def test_selective_reexport_executes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "lib.gm").write_text(
                "fn add(a: Int, b: Int) -> Int { return a + b }\n"
                "fn hidden() -> Int { return 9 }\n"
                "export { add as plus }\n", encoding="utf-8")
            (root / "main.gm").write_text(
                'import "lib.gm" as m\nprint m.plus(2, 3)\n',
                encoding="utf-8")
            self.assertEqual(
                G.run_file(str(root / "main.gm")).out, ["5"])


class Fuzz6RegressionPins(unittest.TestCase):
    """Minimal pins of real round-6 defects.

    Defect 1: a newline-separated selective re-export list cannot close with a
    newline after the last name unless there is a trailing comma.  The
    documented spelling is "entries separated by commas or newlines; a
    trailing comma is allowed"; ``export {\\n  a\\n}`` is a natural spelling but
    the parser sees the newline branch, loops once more and then demands an
    identifier instead of accepting ``}``.  Pinned as expectedFailure: when
    the parser accepts it this test fails (unexpected success).

    Defect 2: ``IR.run_program(source, env=...)`` does not forward ``env`` to
    ``compile_program``, so the source-string convenience cannot resolve
    host-injected callables at check time (``unknown function add``), while
    ``IR.compile_program(source, env=...)`` and ``IR.run_file(..., env=...)``
    both work.  Pinned as expectedFailure.
    """

    @unittest.expectedFailure
    def test_export_list_allows_trailing_newline_before_close(self):
        src = ("fn a() -> Int { return 1 }\n"
               "export {\n  a\n}\n")
        G.check(src)

    @unittest.expectedFailure
    def test_run_program_source_forwards_env_to_checker(self):
        out = IR.run_program("print add(2, 3)\n",
                             env={"add": lambda a, b: a + b}).out
        self.assertEqual(out, ["5"])


def _make_case(fn, idx, label):
    def test(self):
        fn(self, idx)
    test.__doc__ = "%s #%d" % (label, idx)
    return test


for _i, (_lib, _main, _exp) in enumerate(_SELECT_CASES):
    setattr(GeneratedSelectiveReexportTests, "test_select_%03d" % _i,
            _make_case(
                lambda t, i, lib=_lib, main=_main, exp=_exp: _assert_file_parity(
                    t, lib, main, exp, "selective case %d" % i),
                _i, "selective"))


for _i, (_src, _exp) in enumerate(_ALIAS_CASES):
    setattr(GeneratedAliasTests, "test_alias_%03d" % _i,
            _make_case(
                lambda t, i, src=_src, exp=_exp: _assert_alias_parity(
                    t, src, exp, "alias case %d" % i),
                _i, "alias"))


for _i, (_src, _env_, _exp) in enumerate(_HOST_CASES):
    setattr(GeneratedHostCallableTests, "test_host_%03d" % _i,
            _make_case(
                lambda t, i, src=_src, env=_env_, exp=_exp:
                _assert_host_case(
                    t, src, env, exp, "host case %d" % i, bool(i % 2)),
                _i, "host"))


for _i, (_src, _lim_env) in enumerate(_LIMIT_CASES):
    setattr(GeneratedLimitsTests, "test_limits_%03d" % _i,
            _make_case(
                lambda t, i, src=_src, env=_lim_env: _assert_limits_parity(
                    t, src, env, "limits case %d" % i),
                _i, "limits"))


for _i, _src in enumerate(_INHERIT_CASES):
    setattr(GeneratedInheritanceTests, "test_inherit_%03d" % _i,
            _make_case(
                lambda t, i, src=_src: _assert_inherit_parity(
                    t, src, "inheritance case %d" % i),
                _i, "inheritance"))


for _i, _src in enumerate(_FMT_CASES):
    setattr(GeneratedFormatterTests, "test_fmt_%03d" % _i,
            _make_case(
                lambda t, i, src=_src: _assert_formatter_roundtrip(
                    t, src, "formatter case %d" % i),
                _i, "formatter"))


for _i, _src in enumerate(_MUTATION_CASES):
    setattr(GeneratedMutationTests, "test_mutation_%03d" % _i,
            _make_case(
                lambda t, i, src=_src: _assert_parse_rejected(
                    t, src, "mutation case %d" % i),
                _i, "mutation"))


def _alias_reject_test(src, idx):
    def test(self):
        _assert_rejected(self, src, "alias reject %d" % idx)
    return test


for _i, _src in enumerate(_ALIAS_REJECT):
    setattr(GeneratedAliasRejectTests, "test_reject_%03d" % _i,
            _alias_reject_test(_src, _i))


def _export_reject_test(kind, idx):
    def test(self):
        _export_reject_case(self, kind)
    test.__doc__ = "export reject: %s" % kind
    return test


for _i, _kind in enumerate(_EXPORT_REJECT):
    setattr(GeneratedExportRejectTests, "test_reject_%02d" % _i,
            _export_reject_test(_kind, _i))


if __name__ == "__main__":
    unittest.main()
