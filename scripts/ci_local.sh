#!/usr/bin/env bash
# Local equivalent of the CI checks. Fails on the first error.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
V2="$ROOT/gama-lang/v2"

step() {
  printf '\n== %s ==\n' "$1"
}

step "py_compile"
mapfile -t PYFILES < <(find "$ROOT/gama-lang" -name '*.py' -not -path '*/__pycache__/*' | sort)
if [ "${#PYFILES[@]}" -eq 0 ]; then
  echo "error: no Python files found" >&2
  exit 1
fi
"$PYTHON" -m py_compile "${PYFILES[@]}"
printf 'compiled %d files\n' "${#PYFILES[@]}"

step "full unittest"
(cd "$V2" && "$PYTHON" -m unittest discover -s tests)

step "doctor --json"
DOCTOR_JSON="$(mktemp)"
trap 'rm -f "$DOCTOR_JSON"' EXIT
"$PYTHON" "$V2/gamal2.py" doctor --json > "$DOCTOR_JSON"
cat "$DOCTOR_JSON"
"$PYTHON" - "$DOCTOR_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
checks = data.get("checks") or []
failed = [c.get("name", "<unnamed>") for c in checks if not c.get("ok")]
if not data.get("ok") or failed:
    raise SystemExit("doctor failed: %s" % (", ".join(failed) or "ok=false"))
print("doctor OK (%d/%d checks)" % (len(checks), len(checks)))
PY

step "VS Code JSON validation"
for rel in package.json language-configuration.json syntaxes/gama-l.tmLanguage.json snippets/gama-l.json; do
  "$PYTHON" -m json.tool "$V2/vscode/$rel" > /dev/null
  printf 'valid JSON: %s\n' "$rel"
done

step "VS Code extension smoke test"
if ! command -v node > /dev/null 2>&1; then
  echo "error: node is required for the VS Code smoke test" >&2
  exit 1
fi
(cd "$V2/vscode" && node smoke.js)

printf '\nCI local: all checks passed\n'
