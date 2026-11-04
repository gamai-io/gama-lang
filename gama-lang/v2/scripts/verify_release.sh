#!/usr/bin/env bash
# Reproducible release verification for the GAMA-L 2 core.
#
# Builds the wheel + deterministic sdist twice, verifies SHA256SUMS /
# RELEASE.json, installs the wheel into a fresh venv, runs the shipped CLI and
# doctor against the source examples, and validates the VS Code extension
# assets. Nothing is published and no artifacts are written into the repo.
#
# Usage:
#   bash scripts/verify_release.sh
#
# Exit status is non-zero on the first failure. Set PYTHON to pick the
# interpreter used for the build/venv (default: python3).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/gama-l-release-verify.XXXXXX")"
OUT_A="$TMP/dist-a"
OUT_B="$TMP/dist-b"
VENV="$TMP/venv"
DOC_ROOT="$TMP/doctor-root"
VSCE_OUT="$TMP/vscode"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

log() {
  printf '\n== %s ==\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log "1/6 build release A"
"$PYTHON" release.py build --out "$OUT_A"

log "2/6 verify release metadata and checksums"
"$PYTHON" release.py verify --out "$OUT_A"
(
  cd "$OUT_A"
  sha256sum -c SHA256SUMS
)

"$PYTHON" - "$OUT_A" <<'PY'
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
manifest = json.loads((out / "RELEASE.json").read_text(encoding="utf-8"))
files = manifest.get("files") or []
if len(files) != 2:
    raise SystemExit("RELEASE.json must list exactly two files, got %d" % len(files))
names = {entry["name"] for entry in files}
if names != set(manifest.get("sha256") or {}):
    raise SystemExit("RELEASE.json sha256 map disagrees with files")
for entry in files:
    if len(entry["sha256"]) != 64:
        raise SystemExit("bad sha256 for %s" % entry["name"])
    if entry["size"] <= 0:
        raise SystemExit("empty artifact %s" % entry["name"])
    if not (out / entry["name"]).is_file():
        raise SystemExit("missing artifact %s" % entry["name"])
print("manifest: %s %s, %d files" % (manifest["name"], manifest["version"], len(files)))
PY

log "3/6 rebuild and compare for reproducibility"
"$PYTHON" release.py build --out "$OUT_B"
"$PYTHON" - "$OUT_A" "$OUT_B" <<'PY'
import json
import sys

a = json.loads((open(sys.argv[1] + "/RELEASE.json", encoding="utf-8")).read())["sha256"]
b = json.loads((open(sys.argv[2] + "/RELEASE.json", encoding="utf-8")).read())["sha256"]
if a != b:
    for name in sorted(set(a) | set(b)):
        if a.get(name) != b.get(name):
            print("unstable: %s\n  %s\n  %s" % (name, a.get(name), b.get(name)))
    raise SystemExit("release build is not reproducible")
for name in sorted(a):
    print("reproducible: %s %s" % (a[name], name))
PY

log "4/6 install wheel into a fresh venv"
"$PYTHON" -m venv "$VENV"
WHEEL="$(find "$OUT_A" -maxdepth 1 -name '*.whl' -print -quit)"
[ -n "$WHEEL" ] || fail "no wheel produced"
"$VENV/bin/python" -m pip install --no-index --no-deps "$WHEEL"

log "5/6 CLI and doctor smoke tests"
"$VENV/bin/gama-l" --help >/dev/null
HELLO="$("$VENV/bin/gama-l" run "$ROOT/examples/hello.gm")"
printf 'gama-l run examples/hello.gm -> %s\n' "$HELLO"
case "$HELLO" in
  *"Zdravo iz GAMA-L 2"*) ;;
  *) fail "unexpected hello output: $HELLO" ;;
esac

# The wheel intentionally ships code only; point doctor at a root that exposes
# the source examples so all eight checks can run against the installed wheel.
mkdir -p "$DOC_ROOT"
ln -s "$ROOT/examples" "$DOC_ROOT/examples"
"$VENV/bin/gama-l" doctor --json --root "$DOC_ROOT" | tee "$TMP/doctor.json"
"$PYTHON" - "$TMP/doctor.json" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
if not data.get("ok"):
    raise SystemExit("doctor reported failures: %r" % data.get("checks"))
passed = sum(1 for check in data["checks"] if check["ok"])
print("doctor: %d/%d checks passed" % (passed, len(data["checks"])))
PY

log "6/6 validate VS Code extension assets"
VSCODE_DIR="$ROOT/vscode"
[ -d "$VSCODE_DIR" ] || fail "missing vscode/ directory"
"$PYTHON" - "$VSCODE_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
required = ["name", "version", "publisher", "main", "engines", "contributes"]
missing = [key for key in required if key not in manifest]
if missing:
    raise SystemExit("package.json missing keys: %s" % ", ".join(missing))
files = [
    manifest["main"],
    manifest["contributes"]["languages"][0]["configuration"],
    manifest["contributes"]["grammars"][0]["path"],
    manifest["contributes"]["snippets"][0]["path"],
    "README.md",
    "LICENSE",
    ".vscodeignore",
]
for relative in files:
    if not (root / relative).is_file():
        raise SystemExit("missing extension file: %s" % relative)
print("vscode package.json valid: %s@%s (%s)" % (
    manifest["name"], manifest["version"], manifest["engines"].get("vscode")))
PY

mkdir -p "$VSCE_OUT"
if command -v vsce >/dev/null 2>&1; then
  (cd "$VSCODE_DIR" && vsce package --no-dependencies --out "$VSCE_OUT/gama-l.vsix")
  echo "vsce: packaged $(basename "$VSCE_OUT/gama-l.vsix")"
elif command -v npx >/dev/null 2>&1 && \
     npx --no-install @vscode/vsce --version >/dev/null 2>&1; then
  (cd "$VSCODE_DIR" && npx --no-install @vscode/vsce package --no-dependencies --out "$VSCE_OUT/gama-l.vsix")
  echo "vsce: packaged $(basename "$VSCE_OUT/gama-l.vsix")"
else
  echo "vsce: SKIP packaging - 'vsce' / '@vscode/vsce' is not installed and cannot be fetched offline"
  echo "vsce: package.json and all referenced extension assets were validated instead (no publish)"
fi

log "release artifacts"
ls -l "$OUT_A"/*.whl "$OUT_A"/*.tar.gz
cat "$OUT_A/SHA256SUMS"

log "verify_release: OK"
