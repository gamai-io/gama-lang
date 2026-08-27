# -*- coding: utf-8 -*-
"""GAMA-L runtime — wires GAMA-L code to our infrastructure.
SQL (vp2026/mp*), AI (Oracle semapi / Zen GO), memory (local JSON),
notify (SMTP gamatrade), interface (UI spec), HITL (wait user).
"""
import os, json, sys, time, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
MEM_FILE = os.path.join(BASE, "gamal_mem.json")

# ---------- memory ----------
def _mem():
    if os.path.exists(MEM_FILE):
        try: return json.load(open(MEM_FILE, encoding="utf-8"))
        except Exception: return {}
    return {}

def remember(key, val):
    m = _mem(); m[key] = val
    json.dump(m, open(MEM_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return True

def recall(key):
    return _mem().get(key)

# ---------- AI ----------
def ask(prompt, model="local", system=""):
    """Ask AI. model=local -> Oracle semapi qwen; vast/... -> local GPU tunnel; else fallback."""
    # 1) Oracle semapi
    try:
        import urllib.request
        body = json.dumps({"message": prompt, "model": model, "system": system}).encode()
        req = urllib.request.Request("http://100.96.186.2:8080/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())["reply"]
    except Exception:
        pass
    # 2) Vast/Ollama local tunnel (GPU, private)
    try:
        import urllib.request
        mname = {"local": "gemma4:26b", "vast/qwen3-coder:30b": "qwen3-coder:30b"}.get(model, str(model).split('/')[-1])
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=5) as r:
                tags = json.loads(r.read().decode()).get("models", [])
            if tags and not any(t["name"].split(":")[0] == mname.split(":")[0] for t in tags):
                mname = tags[0]["name"]
        except Exception:
            pass
        body = json.dumps({"model": mname, "messages": [{"role": "user", "content": prompt}],
                           "stream": False}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[AI error: {e}]"

# ---------- SQL (vp2026/mp*) ----------
def sql(query, db="vp2026"):
    import pymssql
    conn = pymssql.connect(server="192.168.88.83", port=1433, user="sa",
                           password="SKUpro123", database=db, charset="utf8", tds_version="7.0")
    cur = conn.cursor(as_dict=True)
    cur.execute(query)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [dict(r) for r in rows]

# ---------- notify (SMTP) ----------
def notify(kind, to, subject, body, attach=None):
    if kind == "email":
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject; msg["From"] = "sumeja@gamatrade.ba"; msg["To"] = to
        s = smtplib.SMTP("gamatrade.ba", 26); s.starttls()
        s.login("sumeja@gamatrade.ba", "G4m45um3ja1")
        s.sendmail("sumeja@gamatrade.ba", [to], msg.as_string())
        s.quit()
        return f"email->{to}"
    return f"notify({kind})->{to}"

# ---------- HITL ----------
def hitl(prompt):
    ans = input(f"\n[GAMA-L user] {prompt} ")
    return ans.strip()

# ---------- pipes ----------
def p_filter(rows, pred):
    return [r for r in rows if pred(r)]

def p_select(rows, *fields):
    return [{f: r.get(f) for f in fields} for r in rows]

def p_sum(rows, field):
    return sum(r.get(field, 0) or 0 for r in rows)

def p_hmap(rows, fn):
    return [fn(r) for r in rows]

def p_limit(rows, n):
    return rows[:n]

def p_sort(rows, field, desc=False):
    return sorted(rows, key=lambda r: r.get(field), reverse=desc)

def p_count(rows):
    return len(rows)

def run(source, _globals=None):
    from gamal import transpile
    py = transpile(source)
    code = compile(py, "<gamal>", "exec")
    g = {"__gama_ai": ask, "sql": sql, "remember": remember, "recall": recall,
         "notify": notify, "hitl": hitl,
         "gama_pfilter": p_filter, "gama_pselect": p_select, "gama_psum": p_sum,
         "gama_phmap": p_hmap, "gama_plimit": p_limit, "gama_psort": p_sort,
         "gama_pcount": p_count}
    if _globals: g.update(_globals)
    exec(code, g)
    return g
