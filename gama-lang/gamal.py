# -*- coding: utf-8 -*-
"""GAMA-L transpiler v0.1

GAMA-L = AI-first dataflow language. Principles:
  * Everything is a PIPE: `source | filter X | sum Y`
  * `:=` is assignment (differs from == in expressions!)
  * Keywords are AI/native: ask, remember, recall, notify, sql, wait, embed
  * table literals `{k: v}`, lists `[ ... ]`
  * comments `# ...`
  * functions `make name(param:{type}) { ... }` -> plain def
The transpiler outputs PYTHON (our runtime), but the syntax is NOT python.

Example:
  # read customers
  t := sql("SELECT konto,naziv,mjesto FROM kupdob")        # -> rows table
  fl := t | filter name                                      # filter
  # keep it simple first:
  c := sql("SELECT TOP 3 konto,naziv FROM kupdob")
  out := c | limit 5
"""
import re

KEYWORDS = {"make", "return", "wait", "ask", "remember", "recall", "notify",
            "sql", "embed", "interface", "filter", "select", "sum", "count",
            "limit", "sort", "map", "if", "else", "repeat"}


def transpile(src):
    lines = src.splitlines()
    out = []
    for ln in lines:
        ln = ln.strip()
        orig = ln
        if not ln or ln.startswith("#"):
            out.append(orig if orig.startswith("#") else "")
            continue
        # function block: make name(args) { ... } -> def
        m = re.match(r"^make\s+(\w+)\s*\(([^)]*)\)\s*:?\s*(\w*)\s*\{$", ln)
        if m:
            name, args = m.group(1), m.group(2)
            out.append(f"def {name}({args}):")
            continue
        if ln == "}":
            out.append("")
            continue
        # assignment with pipe: x := a | b | c  (or plain expression a)
        m = re.match(r"^(\w+)\s*:=\s*(.*)$", ln)
        if m:
            var, expr = m.group(1), m.group(2)
            out.append(f"{var} = {_parse_expr(expr)}")
            continue
        # assignment = (python-like) -> leave to runtime, but without pipe
        m = re.match(r"^(\w+)\s*=\s*(.*)$", ln)
        if m:
            out.append(f"{m.group(1)} = {_parse_expr(m.group(2))}")
            continue
        # function call / statement
        out.append(_parse_expr(ln))
    return "\n".join(out)


def _parse_expr(expr):
    e = expr.strip()
    # GAMA-L dict literals: {name: "x"} -> {'name': "x"} (auto-quote bare keys)
    e = re.sub(r'\{([A-Za-z_]\w*)\s*:', r"{'\1':", e)
    e = re.sub(r'(\s)([A-Za-z_]\w*)\s*:', r"\1'\2':", e)
    # ask "..." (default model) or ask model "..."
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
        return f"notify({m.group(1)!r}, {m.group(2)!r}, {m.group(4)!r}, {m.group(4)!r})"
    return _process_pipes(e)


def _process_pipes(e):
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
    out = parts[0]
    for op in parts[1:]:
        om = re.match(r"^filter\s+([\w.]+)$", op)
        if om:
            f = om.group(1)
            out = f"gama_pfilter({out}, lambda r: bool(r.get({f!r})) if isinstance(r, dict) else bool({f}))"
            continue
        om = re.match(r"^select\s+(.+)$", op)
        if om:
            fields = [x.strip() for x in om.group(1).split(",")]
            out = f"gama_pselect({out}, {', '.join(repr(x) for x in fields)})"
            continue
        om = re.match(r"^sum\s+(\w+)$", op)
        if om:
            out = f"gama_psum({out}, {om.group(1)!r})"
            continue
        om = re.match(r"^limit\s+(\d+)$", op)
        if om:
            out = f"gama_plimit({out}, {om.group(1)})"
            continue
        om = re.match(r"^count$", op)
        if om:
            out = f"gama_pcount({out})"
            continue
        om = re.match(r"^map\s+([\w.]+)$", op)
        if om:
            out = f"gama_phmap({out}, lambda r: r.get({om.group(1)!r}))"
            continue
        out = f"({op})({out})"
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        src = open(sys.argv[1], encoding="utf-8").read()
        print(transpile(src))
