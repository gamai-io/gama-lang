# GAMA-L

Minimal VS Code package for the GAMA-L 2 language.

- Language id: `gama-l`
- File extension: `.gm`
- Scope name: `source.gama-l`

It provides TextMate syntax highlighting, bracket/comment configuration,
snippets, and a **minimal built-in LSP client** for diagnostics, completion,
hover, and signature help (no `vscode-languageclient`, no npm dependencies).

Namespace support: the grammar highlights `import "lib.gm" as math` and
`math.add(...)`, and snippets include `impas` (import ... as), `callns`
(namespaced call), `iife` (immediately invoked lambda), and `fnlettype`
(Function-typed let). In the language server, `import "lib.gm" as ` suggests
the filename stem as the alias (only when it does not collide), `math.`
completes the imported file's top-level functions, and hover/signature help
show their signatures.

Limitations: alias completion cannot infer arbitrary aliases (it only derives
one from the module filename); module member tooling does not follow
re-exported imports or expose top-level `let` values; diagnostics use the
in-memory checker, so `import` documents report "import requires
check_file/run_file" until checked from disk.

## Requirements

- **VS Code 1.80+**
- One of:
  - **Python 3.10+** on `PATH` (`python3`, falling back to `python`/`py`), or
  - the **`gama-lsp`** command on `PATH` (installed from this repository, e.g.
    `pip install .` exposes `gama-lsp`).
- **`gamal2.py`** when no `gama-lsp` command is available.

## Workspace Trust

The language server runs code from the workspace (`gamal2.py`) and therefore
**the extension refuses to start in an untrusted workspace**.

- `capabilities.untrustedWorkspaces.supported` is `false`,
- `gama-l.pythonPath` and `gama-l.serverScript` are listed as
  `restrictedConfigurations`, so a repository's `.vscode/settings.json` cannot
  redirect them before you trust the workspace,
- `extensionKind` is `["workspace"]` (the extension runs where the files are).

If the workspace is not trusted, the extension shows **one** message with
**Manage Trust** and **Open Settings**; nothing is spawned. Trust the folder
(Command Palette → *Workspaces: Manage Workspace Trust*) to enable diagnostics.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `gama-l.pythonPath` | `python3` | Interpreter used to start the server. If it is not found (`ENOENT`), `python` and then `py` are tried. |
| `gama-l.serverScript` | `""` (empty) | Path to `gamal2.py`. Empty means auto-detect. `${workspaceFolder}` is expanded to the first workspace folder. |

Changing either setting restarts the language server for that window.

## How the server is started (in order)

1. **`gama-l.serverScript` is set** → `pythonPath <serverScript> lsp`
   (with the interpreter fallback below). A missing file produces a warning,
   not a crash.
2. **`serverScript` is empty and `gama-lsp` exists on `PATH`** →
   `gama-lsp` directly, no Python needed.
3. **`serverScript` is empty and `gamal2.py` sits next to this extension**
   (`<extensionPath>/../gamal2.py`) → `pythonPath <that gamal2.py> lsp`.
4. Otherwise a warning with **Open Settings** is shown; the extension keeps
   running without diagnostics.

Interpreter fallback: `python3` → `python` → `py` (whatever was selected with
`gama-l.pythonPath` first). Each candidate is tried only when the previous one
fails with `ENOENT`.

## Run it (Extension Development Host)

1. Open VS Code.
2. `File > Open Folder...` and select this `vscode/` folder.
3. Press **F5** — a new "Extension Development Host" window opens with the
   extension loaded.
4. Trust the workspace when prompted, then open any `.gm` file: highlighting
   works and diagnostics appear in the Problems panel.

The repository layout already satisfies case 3 (`vscode/../gamal2.py`), so no
settings are required. If you install the extension elsewhere, either set
`gama-l.serverScript` to the absolute path of `gamal2.py` or install `gama-lsp`
on `PATH`.

`GAMA-L: Run Doctor` (Command Palette) runs `<python> <serverScript> doctor
--json`, or `gama-l doctor --json` when no script is configured, and reports
`ok` plus the names of any failed checks. Doctor also requires a trusted
workspace.

## Automatic recovery

- A normal stop (window reload, settings change, deactivate) does **not**
  report "stopped unexpectedly".
- An unexpected `exit`/`close` restarts the server with backoff:
  **1s, 2s, 4s**, at most **3** attempts, with a message on each attempt. After
  the budget is exhausted the extension stops retrying and points at the
  settings instead of looping forever.
- The restart budget is reset once the server answers `initialize`.

To install permanently, copy this folder into your VS Code extensions
directory, or package it with `vsce` (not included here).

## Notes

- No `npm install` is needed; `extension.js` uses only Node built-ins
  (`child_process`, `fs`, `path`) plus the VS Code API.
- Diagnostics come from the server via `textDocument/publishDiagnostics`
  (severity 1 → Error, 2 → Warning, 3 → Information).
- If the server script is missing or the process dies, the extension shows a
  single warning and keeps running; it never takes down the extension host.

## What is highlighted

- Comments starting with `#`
- Single- and double-quoted strings with escapes
- Integers and decimals
- Keywords, including `match`, `make`, `use`, `wait`, `for`, `while`,
  `break`, and `continue`
- Type names (`Int`, `Decimal`, `Text`, `Bool`, `List`, `Table`, `Result`,
  `Json`, `Null`, `Any`, `Function`)
- Result constructors `Ok` / `Err` and the match wildcard `_`
- Builtin functions from the v2 core: `sql`, `ask`, `wait_user`, `remember`,
  `recall`, `sql_write`, `mail`, `len`, `text`, `number`, `round`, and the pure
  helpers `unwrap`, `unwrap_or`, `is_ok`, `is_err`, `split`, `join`, `contains`,
  `trim`, `upper`, `lower`, `replace`, `starts_with`, `ends_with`, `keys`,
  `values`, `has`
- The postfix Result propagation operator `?`
- Pipe stages (`filter`, `select`, `sum`, `limit`, `count`, `map`)
- Record declarations (`record Point { x: Int }`) with field names and field
  types, record literals (`Point { x: 1 }`), and member access (`p.x`)
- Record copy/update (`p with { x = 3 }`): `with` gets its own keyword scope and
  the updated field names and `=` are scoped inside the update block
- Enum declarations, including single-payload (`Some(Int)`) and multi-payload
  (`Two(Int, Text)`) variants; payload type names are scoped as types
- Enum variants as values (`Color.Red`) and aliased variants across an import
  (`geo.Color.Red`: namespace, enum type, variant)
- Capitalized payload constructors in expressions and patterns
  (`Some(5)`, `Pair(1, "a")`, `Some(v)`, `Pair(a, b)`)
- `export fn`, `export let`, and `export record` (the leading `export` gets a
  keyword scope; `export enum` is rejected by the core and is not highlighted
  as an export)
- Operators and punctuation
- Function names after `fn` / `make`, bindings after `let`, and `prove` targets
- Lambda expressions in expression position: `fn(x: Int) -> Int { ... }`
  (keyword, typed parameters, arrow, return type)
- The `Function` type in parameters and annotations
- Function values: a lambda bound to `let` and calls through that name
  (`let inc = fn(...)`, `f(41)`); the call name gets function-name scope
- `wait user "..."` as a unit (keyword + `user`)

The grammar is syntax-only and intentionally tracks only names that exist in
the v2 core (`gamal2.py`). Result helpers (`unwrap`, `unwrap_or`, `is_ok`,
`is_err`), text helpers (`split`, `join`, `contains`, `trim`, `upper`, `lower`,
`replace`, `starts_with`, `ends_with`) and collection helpers (`keys`,
`values`, `has`) are part of the v2 core, so they are highlighted.

## Language-core limitations (kept truthful in the grammar)

- **IIFE is supported:** `(fn(x: Int) -> Int { ... })(41)` parses and runs;
  the grammar highlights the lambda head and the call is ordinary syntax.
- **Named `fn` declarations are first-class values after definition:**
  `fn add(a: Int, b: Int) -> Int { ... }` then `let f = add` works. The
  checker knows top-level function names up front; the runtime resolves the
  value when the `fn` declaration has executed, so bind after the declaration.
  `let add = fn(a: Int, b: Int) -> Int { ... }`, `let f = add`, `f(2, 3)` also
  works.
- **`Function` is a valid parameter/annotation type.** Higher-order calls and
  passing a lambda directly both work:
  `fn apply(f: Function, x: Int) -> Int { return f(x) }`,
  `apply(fn(x: Int) -> Int { return x + 1 }, 41)`, and
  `let f: Function = fn(x: Int) -> Int { return x + 1 }`.
- Lambda effects must be declared when the body calls an effectful builtin
  (`effects { mail }`), matching named functions.

## Grammar coverage limits

The grammar is a TextMate tokenizer, not a parser, and intentionally stays
conservative:

- `with` is highlighted as a record-update keyword only when it is immediately
  followed by `{`; `ask "..." with context` keeps its existing highlighting.
- The record-update block ends at the first `}`. A field value that itself
  contains a braced block (for example a nested record literal) can close the
  update scope early, exactly like the existing record-literal rule.
- `alias.Enum.Variant` is highlighted for three-part dotted chains. Arbitrary
  deeper chains or alias type annotations such as `let p: geo.Point` are left to
  the ordinary member-access/type rules.
- Payload declarations highlight simple identifier payload types only, because
  the core rejects qualified payload types (`Some(geo.Point)`).
- `export` is highlighted only for `export fn`, `export let`, and
  `export record`; `export enum` is rejected by the core, so it is not given an
  export scope.
- `export make` is accepted by the core parser but is outside the documented
  `export fn/let/record` surface and is not given its own export scope.

## Snippets

Besides `fn`, `let`, `if`, `prove`, pipe, and `import` (plus namespaced `import ... as`, snippet `impas`), the snippet set covers
the Result flow (`fnres`, `match`, `try`/`tryexpr`, `ok`, `err`), the core
builtins (`ask`, `askwith`, `sql`, `sqlwrite`, `remember`, `wait`, `mail`,
`round`, `len`, `text`, `number`), and functions/lambdas: `lambda`/`fnx`
(anonymous expression), `fnlet` (lambda bound to `let`), `fntype`/`higher`
(higher-order `Function` parameter), and `fnvalue` (lambda alias called through
a new name).

The latest syntax additions have snippets too: `recordwith` (record copy/update
`value with { field = expr }`), `payloadenum`/`enumpayload` and
`multipayloadenum`/`enumpair` (payload enum declarations), `payload`/`some` and
`pair`/`payload2` (payload constructors), `aliasvariant`/`aliasenum`
(`geo.Color.Red`), `matchpayload`/`matchenum` (payload pattern arms), and
`exportfn`, `exportlet`, `exportrecord` (explicit exports). `export enum` is not
offered because the core rejects it.

## License

MIT — see `LICENSE`.
