# GAMA-L + GAMA-OS — "What is where" Report (v0.1)

Author: **GAMA TRADE** — built through a shared working environment (opencode + AI agent).
Date: 2026-08-27

This document is the **map of the whole thing** — what is where, what works, and how to run it.
Goal: that anyone (e.g. Adi) quickly grasps the essence without reading a hundred files.

---

## 1. WHAT THIS IS — one sentence

> Our **programming language with AI built into the language** (not as a library —
> as the keystone), plus an **operating system** that turns old/existing computers
> into "AI-native" terminals of our network.

Two products, black and white:

| Product | What it is | Where |
|---|---|---|
| **GAMA-L** | AI-first dataflow programming language (v0.1) | `gama-lang/` |
| **GAMA-OS** | Own OS product line on top of a Linux kernel | `gama-os/` |

---

## 2. GAMA-L — POLYMORPHIC PROGRAMMING LANGUAGE (`gama-lang/`)

### Paradigm (why it differs from everything)
1. **Pipes (dataflow)** — everything is a flow: `source | filter X | select A,B | count`.
   You write **what**, not *how*.
2. **Data is a first-class citizen** — tables/maps are native, no boilerplate.
3. **AI is built in** — `ask`, `remember/recall`, `embed`, `notify` are **KEYWORDS**, not `import`.
4. **HITL (human-in-the-loop) by default** — `wait user "..."` asks for a human
   where it matters.
5. **Meta-level (#tune)** — functions carry tags that our AI optimizes (on GPU),
   so the language evolves with us.

### Folder structure
```
gama-lang/
├── gamal.py            TRANSPILER: GAMA-L → Python (heart of the language, ~160 lines)
├── gamal_runtime.py    RUNTIME: wires code to infrastructure (SQL, AI, mail, memory)
├── test_e2e.gm         end-to-end test (SQL + pipe, no sending)
├── test_full.gm        full test (memory + AI)
├── test_noai.gm        test without AI
├── demo.gm             live demo: customers from SQL → pipe → AI → mail
└── gamal_mem.json      language memory ({key: value})
```

### File by file — what the CODE does
- **`gamal.py`** — parses GAMA-L and rewrites it to Python. Supports:
  `:=` (assignment), pipes (`|`), functions `make name(...)`, comments `#`,
  keywords `sql / ask / remember / recall / notify / wait user`.
- **`gamal_runtime.py`** — the functions the transpiler calls:
  - `ask(prompt, model)` → ask AI (Oracle semapi → fallback Vast/Ollama GPU)
  - `sql(query, db)` → SQL on VP2026 (real sales database, 192.168.88.83)
  - `remember/recall` → memory in `gamal_mem.json`
  - `notify(...)` → SMTP mail (gamatrade.ba:26)
  - `hitl(...)` → `wait user` (ask the user)
  - pipes: `filter / select / sum / map / limit / sort / count`

### Syntax (quick overview)
```gamal
# comment
t := sql("SELECT konto,naziv FROM kupdob")         # SQL → table
fl := t | filter naziv | select konto, naziv       # pipe: filter, select
n  := fl | count                                   # pipe: count rows

thought := ask local "What do you think about this?"  # AI (GPU/Oracle)
remember "memory" = "value"                             # memory (internal)
got := recall "memory"                                  # get memory

confirm := wait user "Please confirm?"                  # HITL
notify email to: "x@y.ba" subject: "Subject" "Body"     # notification
```

### Proven working (v0.1) — tested live
- ✅ SQL from the **real VP2026 database**: real customers pulled (A&F D.O.O. Jajce, ...)
- ✅ pipe `select` + `count`
- ✅ `remember/recall` memory (memory file written)
- ✅ `ask` → AI answer from Vast GPU / Oracle
- ✅ transpile: `:=`, pipes, functions

### Next steps (v0.2 — plan)
- [ ] `#tune` meta-level — AI optimizes functions on GPU
- [ ] `interface` — auto UI spec from declarations ("app by request")
- [ ] built-in `embed` (semantic search, bge-m3)
- [ ] module import/export
- [ ] TTS/STT (`speak`, `listen`) — audio interface
- [ ] distribute the runtime across all machines

> **Idea:** a single GAMA-L file = a complete "app by request" — database + AI +
> interface + notifications, **without a classic dev stack**.

### Running it
```bash
cd gama-lang
python gamal.py demo.gm                              # transpile to Python (preview)
python -c "from gamal_runtime import run; run(open('test_e2e.gm',encoding='utf-8').read()); print('ok')"
```

---

## 3. GAMA-OS — OPERATING SYSTEM (`gama-os/`)

### What it is
An own operating-system family on top of a Linux kernel. Every machine in our
network (old junk included) becomes a **GAMA-OS terminal — with AI built in**,
instead of being e-waste.

### Folder structure
```
gama-os/
├── builder/
│   └── gama-os.sh     BUILDER script: one kernel, all roles (kiosk/terminal/pro/node)
├── launcher/
│   └── index.html     GAMA-OS UI (splash + apps: offer/db/AI/mail/POS/...)
└── docs/
    └── PRODUCT_LINE.md  product line + pricing + Q-LAB test list
```

### Versions (customer choice)
| Version | Purpose | Min hardware | Price |
|---|---|---|---|
| **KIOSK** (Lite) | revive old junk | Celeron/Pentium, 512MB-1GB | package |
| **TERMINAL** | thin client / workstation | 1GB+, i686/x64 | package |
| **PRO** | = like our Linux3 | x64, 2-4GB | package |
| **NODE** | service node / backend | x64, 8GB+ | subscription |

### What every version shares
- **Kernel**: Alpine Linux (ultra-light, secure)
- **UI**: web launcher (GAMA-L `interface`) — works on mobile too
- **AI**: GAMA-L runtime (`ask`, `remember`, `sql`, `notify`)
- **Network**: Tailscale + gama-bus (our agent protocol)
- **Alerts**: `notify` (SMTP + bus)
- **Security**: locked kiosk, remote-admin, self-update

### Differentiators
1. **AI is in the language** (GAMA-L), not an add-on — every machine can ask/speak.
2. **Everything branded GAMA** — splash, launcher, UI.
3. **Cloud bridge to our fleet** — an old machine is capacity, not e-waste.
4. **Tested on real junk** (Q-LAB list).

---

## 4. HOW THIS CAME TO BE (work context)

Part of the broader **"GAMA AI"** system we build in parallel:
- AI agent + bot (WhatsApp) + SQL access to the VP2026 sales database
- Meta-system of 400+ agent skills
- Experiment: **own "gama-mini" model** fine-tuned on Qwen3-8B (QLoRA), served on GPU
- GAMA-L and GAMA-OS are **own technology** inside that system

> This repo extracts **only** the language + OS (finished v0.1 artifacts),
> so they are presentable and shareable (e.g. for Adi).

---

## 5. STATUS AT A GLANCE
- **GAMA-L v0.1**: works, tested end-to-end (SQL + AI + memory + mail).
- **GAMA-OS v0.1**: builder + UI + line documentation. Q-LAB testing is next.
- All **locally our proprietary work** — no third-party code in the core.

**We welcome public feedback**: the language, its syntax, the OS idea, the
differentiation — all open to critique.
