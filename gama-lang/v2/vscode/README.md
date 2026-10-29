# GAMA-L

Minimal VS Code package for the GAMA-L 2 language.

- Language id: `gama-l`
- File extension: `.gm`
- Scope name: `source.gama-l`

It provides TextMate syntax highlighting, bracket/comment configuration,
snippets, and a **minimal built-in LSP client** for diagnostics
(no `vscode-languageclient`, no npm dependencies).

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
- Keywords, type names, and builtin functions
- Pipe stages (`filter`, `select`, `sum`, `limit`, `count`, `map`)
- Operators and punctuation
- Function names after `fn`, bindings after `let`, and `prove` targets

## License

MIT — see `LICENSE`.
