# GAMA-L 2.0 core (executable)

This is a real parser + static checker + tree-walking interpreter, not the old
transpiler. It is a **core subset** of `SPEC_2.0.md`; the rest is still roadmap.

## Run

```bash
python3 gamal2.py run examples/invoice.gm
python3 gamal2.py check examples/invoice.gm
python3 gamal2.py test
```

## Implemented

- `import "file.gm"` with cycle detection (relative to the importing file)
- `let` with optional type annotation, `:=` assignment
- `fn` with typed parameters, return type, `effects { ... }`
- `if` / `else` blocks, `return`
- literals: text, int, decimal, bool, null, list, map
- arithmetic/comparison/boolean operators
- pipes: `filter`, `select`, `sum`, `limit`, `count`, `map`
- `ask` (offline mock, or OpenAI-compatible `GAMA_L_ASK_URL`), `sql` (demo table, or read-only SQLite via `GAMA_L_DB`), `remember/recall`
- `wait_user` (non-interactive fails closed)
- `prove` (hash-chained receipt) and `verify` (tamper detection)
- static type errors and static effect violations
- CLI `run`, `check`, `test`

## Tests

```bash
python3 -m unittest discover -s tests -v   # 13/13
```

Conformance coverage is the T1-T5 subset from `SPEC_2.0.md`:
arithmetic/pipes, type error, effect violation, HITL denial, proof/tamper,
plus imports and real read-only SQLite.

## Not implemented yet

- modules/imports and package manager
- full type inference and generics
- real SQL/AI/mail connectors (the core uses demo/mock adapters)
- formatter, LSP, VS Code syntax, distribution build

The old v0.1 transpiler remains in `gama-lang/` for reference. Do not call this
full 2.0 until the remaining conformance tests and connectors are done.
