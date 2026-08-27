# -*- coding: utf-8 -*-
"""GAMA-L transpajler v0.1

GAMA-L = AI-first dataflow jezik. Principi:
  * Sve je PIPE: `izvor | filt X | suma Y`
  * `:=` je dodela (razlikuje se od == u izrazima!)
  * ke_ywords su AI/native: ask, remember, recall, notify, sql, wait, embed
  * tabelarni literali `{k: v}`, liste `[ ... ]`
  * komentari `# ...`
  * funkcije `napravi ime(par:{tip}) { ... }` -> obican def
Transpajlu je u PYTHON (nas runtim), ali sintaksa NIJE python.

Primjer:
  # citaj kupce
  t := sql("SELECT konto,naziv,mjesto FROM kupdob")        # -> gama ... rows
  z := t | filt r.naziv?  -> naziv ?
  hajde prvo jednostavno:
  c := sql("SELECT TOP 3 konto,naziv FROM kupdob")
  izlaz := c | gama_limit 5
"""
import re

KEYWORDS = {"napravi", "povrati", "wait", "ask", "remember", "recall", "notify",
            "sql", "embed", "interface", "filt", "select", "suma", "broj", "limit",
            "sort", "map", "ako", "inače", "ponavljaj"}

def _to_py_var(name):
    # GAMA-L identifikatori mogu biti bosanski -> dozvoli unicode u var nazivima
    return name

def transpile(src):
    lines = src.splitlines()
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        orig = lines[i]
        i += 1
        if not ln or ln.startswith("#"):
            out.append(orig if orig.strip().startswith("#") else "")
            continue
        # blok funkcije: napravi ime(args) { ... } -> def
        m = re.match(r"^napravi\s+(\w+)\s*\(([^)]*)\)\s*:?\s*(\w*)\s*\{$", ln)
        if m:
            name, args, ret = m.group(1), m.group(2), m.group(3)
            out.append(f"def {name}({args}):")
            continue
        if ln == "}":
            out.append("")
            continue
        # dodela sa pipe: x := a | b | c   (ili obican izraz a)
        m = re.match(r"^(\w+)\s*:=\s*(.*)$", ln)
        if m:
            var, expr = m.group(1), m.group(2)
            out.append(f"{_to_py_var(var)} = {_parse_expr(expr)}")
            continue
        # dodela = (python-like) -> prepusti runtime-istoj, ali bez pipe
        m = re.match(r"^(\w+)\s*=\s*(.*)$", ln)
        if m:
            out.append(f"{_to_py_var(m.group(1))} = {_parse_expr(m.group(2))}")
            continue
        # poziv funkcije / naredba
        out.append(_parse_expr(ln))
    return "\n".join(out)


def _parse_expr(expr):
    e = expr.strip()
    # GAMA-L dict literali: {naziv: "x"} -> {'naziv': "x"} (automatski citiraj gole kljuceve)
    e = re.sub(r'\{([A-Za-z_]\w*)\s*:', r"{'\1':", e)
    e = re.sub(r'(\s)([A-Za-z_]\w*)\s*:', r"\1'\2':", e)
    # dodela = primjer ??
    # ask "..." (default model) ili ask model "..." (specificni model)
    m = re.match(r'^ask\s+(?:"(.+)"|(\w+)\s+"(.+)")$', e)
    if m:
        if m.group(1) is not None:
            return f"__gama_ai({m.group(1)!r})"
        return f"__gama_ai({m.group(3)!r}, model={m.group(2)!r})"
    # remember "k" = "v"
    m = re.match(r'^remember\s*(?:"(\w+)"|(\w+))\s*=\s*"(.+)"\s*$', e)
    if m:
        k = m.group(1) or m.group(2)
        return f"remember({k!r}, {m.group(3)!r})"
    # recall "k"
    m = re.match(r'^recall\s*(?:"(\w+)"|(\w+))$', e)
    if m:
        k = m.group(1) or m.group(2)
        return f"recall({k!r})"
    # wait user "..." -> hitl
    m = re.match(r'^wait\s+user\s+"(.+)"$', e)
    if m:
        return f"hitl({m.group(1)!r})"
    # notify email to: "x" subject: "s" "body"
    m = re.match(r'^notify\s+(\w+)\s+to:\s*"([^"]+)"(?:\s+subject:\s*"([^"]*)")?\s*"([^"]+)"$', e)
    if m:
        return f"notify({m.group(1)!r}, {m.group(2)!r}, {m.group(3) or m.group(4)!r}, {m.group(4)!r})"
    # sql("...") unutar izraza --- pass-through (vec python-compatible)
    # pipes: a | filt pred | select f | suma f | limit n | broj | map fn
    return _process_pipes(e)


def _process_pipes(e):
    # razdvaj pipe (van stringova)
    parts = []
    cur, in_str, q = "", False, None
    for ch in e:
        if in_str:
            cur += ch
            if ch == q: in_str = False
            continue
        if ch in "\"'":
            in_str = True; q = ch; cur += ch; continue
        if ch == "|":
            parts.append(cur.strip()); cur = ""
            continue
        cur += ch
    if cur.strip(): parts.append(cur.strip())
    if len(parts) == 1:
        return parts[0]
    # parts[0] izvor, ostatak su operacije -> chain
    out = parts[0]
    for op in parts[1:]:
        om = re.match(r"^filt\s+([\w.]+)$", op)
        if om:
            f = om.group(1)
            out = f"gama_pfilter({out}, lambda r: bool(r.get({f!r})) if isinstance(r, dict) else bool({f}))"
            continue
        om = re.match(r"^select\s+(.+)$", op)
        if om:
            fields = [x.strip() for x in om.group(1).split(",")]
            out = f"gama_pselect({out}, {', '.join(repr(x) for x in fields)})"
            continue
        om = re.match(r"^suma\s+(\w+)$", op)
        if om:
            out = f"gama_psum({out}, {om.group(1)!r})"
            continue
        om = re.match(r"^limit\s+(\d+)$", op)
        if om:
            out = f"gama_plimit({out}, {om.group(1)})"
            continue
        om = re.match(r"^broj$", op)
        if om:
            out = f"gama_pcount({out})"
            continue
        om = re.match(r"^map\s+([\w.]+)$", op)
        if om:
            out = f"gama_phmap({out}, lambda r: r.get({om.group(1)!r}))"
            continue
        # nepoznata pipe op -> pretpostavi helper call
        out = f"({op})({out})"
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        src = open(sys.argv[1], encoding="utf-8").read()
        print(transpile(src))