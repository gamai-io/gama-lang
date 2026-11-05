#!/usr/bin/env bash
# Reproducible release dry run for the GAMA-L 2 core.
#
# This is the "everything except publish" gate. It:
#   1. builds the wheel + deterministic sdist twice and compares their SHA-256
#      (reproducibility), without writing artifacts into the repository;
#   2. runs `release.py verify` and `sha256sum -c SHA256SUMS`;
#   3. installs the wheel into a fresh virtualenv and runs the shipped CLI
#      (`--help`, `run examples/hello.gm`, `doctor --json --root ...`);
#   4. runs `twine check` when twine is installed;
#   5. packages the VS Code extension when `vsce` (or a cached
#      `@vscode/vsce`) is available offline;
#   6. prints a manifest with versions, sizes, hashes, and tool availability.
#
# Optional tools (twine, vsce) are skipped with an explicit message. Real
# errors (build, checksum, install, CLI, doctor) fail the run.
#
# Nothing is published and no artifacts are committed. All work happens in a
# temporary directory that is removed on exit.
#
# Usage:
#   bash scripts/release_dry_run.sh
#
# Environment:
#   PYTHON        interpreter for build/venv (default: python3)
#   KEEP_DIST     if set, copy verified artifacts + manifest there (e.g. CI)
#   MANIFEST_OUT  if set, also write the JSON manifest to this path
#
# Exit status is non-zero on the first real failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/gama-l-release-dryrun.XXXXXX")"
OUT_A="$TMP/dist-a"
OUT_B="$TMP/dist-b"
VENV="$TMP/venv"
DOC_ROOT="$TMP/doctor-root"
VSIX_DIR="$TMP/vsix"
KEEP_DIST="${KEEP_DIST:-}"
MANIFEST_OUT="${MANIFEST_OUT:-}"
VSIX_PATH=""
HELLO=""

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

have() {
  command -v "$1" >/dev/null 2>&1
}

PY_VER="$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

# ------------------------------------------------------------------ tool matrix
SHA256SUM_STATUS="no"
if have sha256sum; then
  SHA256SUM_STATUS="yes"
fi

TWINE_STATUS="no"
TWINE_CMD=()
if have twine; then
  TWINE_STATUS="yes"
  TWINE_CMD=(twine)
elif "$PYTHON" -m twine --version >/dev/null 2>&1; then
  TWINE_STATUS="yes (python -m twine)"
  TWINE_CMD=("$PYTHON" -m twine)
fi

VSCE_STATUS="no"
VSCE_CMD=()
if have vsce; then
  VSCE_STATUS="yes"
  VSCE_CMD=(vsce)
elif have npx && npx --no-install @vscode/vsce --version >/dev/null 2>&1; then
  VSCE_STATUS="yes (npx --no-install @vscode/vsce)"
  VSCE_CMD=(npx --no-install @vscode/vsce)
fi

NODE_STATUS="no"
if have node; then
  NODE_STATUS="yes"
fi
GIT_STATUS="no"
if have git; then
  GIT_STATUS="yes"
fi

log "tools"
printf 'python    : %s (%s)\n' "$PYTHON" "$PY_VER"
printf 'sha256sum : %s\n' "$SHA256SUM_STATUS"
printf 'twine     : %s\n' "$TWINE_STATUS"
printf 'vsce      : %s\n' "$VSCE_STATUS"
printf 'node      : %s\n' "$NODE_STATUS"
printf 'git       : %s\n' "$GIT_STATUS"

# ------------------------------------------------------------------ 1) build A
log "1/8 build release A"
if ! "$PYTHON" release.py build --out "$OUT_A" >"$TMP/build-a.json" 2>"$TMP/build-a.err"; then
  cat "$TMP/build-a.json" 2>/dev/null || true
  cat "$TMP/build-a.err" >&2 || true
  fail "release.py build (A) failed"
fi
cat "$TMP/build-a.json"

# ------------------------------------------------------------------ 2) verify A
log "2/8 verify release metadata and checksums"
if ! "$PYTHON" release.py verify --out "$OUT_A" >"$TMP/verify-a.json" 2>"$TMP/verify-a.err"; then
  cat "$TMP/verify-a.json" 2>/dev/null || true
  cat "$TMP/verify-a.err" >&2 || true
  fail "release.py verify failed"
fi
cat "$TMP/verify-a.json"

if have sha256sum; then
  (cd "$OUT_A" && sha256sum -c SHA256SUMS)
else
  echo "sha256sum: MISSING - verifying with a Python fallback" >&2
  "$PYTHON" - "$OUT_A" <<'PY'
import hashlib
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
for line in (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    parts = line.split(None, 1)
    if len(parts) != 2:
        continue
    expected, name = parts[0], parts[1].strip()
    path = out / name
    if not path.is_file():
        raise SystemExit("missing artifact: %s" % name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit("sha256 mismatch for %s" % name)
    print("%s: OK" % name)
PY
fi

# ------------------------------------------------------- 3) reproducible rebuild
log "3/8 rebuild release B and compare for reproducibility"
if ! "$PYTHON" release.py build --out "$OUT_B" >"$TMP/build-b.json" 2>"$TMP/build-b.err"; then
  cat "$TMP/build-b.json" 2>/dev/null || true
  cat "$TMP/build-b.err" >&2 || true
  fail "release.py build (B) failed"
fi
"$PYTHON" - "$OUT_A" "$OUT_B" <<'PY'
import json
import pathlib
import sys

a_dir = pathlib.Path(sys.argv[1])
b_dir = pathlib.Path(sys.argv[2])
a = json.loads((a_dir / "RELEASE.json").read_text(encoding="utf-8"))
b = json.loads((b_dir / "RELEASE.json").read_text(encoding="utf-8"))
hash_a = a.get("sha256") or {}
hash_b = b.get("sha256") or {}
if hash_a != hash_b:
    for name in sorted(set(hash_a) | set(hash_b)):
        if hash_a.get(name) != hash_b.get(name):
            print("unstable: %s\n  A: %s\n  B: %s"
                  % (name, hash_a.get(name), hash_b.get(name)))
    raise SystemExit("release build is not reproducible")
for name in sorted(hash_a):
    print("reproducible: %s %s" % (hash_a[name], name))
PY

# ------------------------------------------------------- 4) fresh venv install
log "4/8 install wheel into a fresh virtualenv"
"$PYTHON" -m venv "$VENV"
WHEEL="$(find "$OUT_A" -maxdepth 1 -name '*.whl' -print -quit)"
[ -n "$WHEEL" ] || fail "no wheel produced in $OUT_A"
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-index --no-deps "$WHEEL"

# ------------------------------------------------------- 5) CLI and doctor
log "5/8 CLI and doctor smoke tests"
"$VENV/bin/gama-l" --help >/dev/null
HELLO="$("$VENV/bin/gama-l" run "$ROOT/examples/hello.gm")"
printf 'gama-l run examples/hello.gm -> %s\n' "$HELLO"
case "$HELLO" in
  *"Zdravo iz GAMA-L 2"*) ;;
  *) fail "unexpected hello output: $HELLO" ;;
esac

# The wheel ships code only. Point doctor at a temp root that exposes the
# source examples so all checks run against the installed wheel.
mkdir -p "$DOC_ROOT"
ln -s "$ROOT/examples" "$DOC_ROOT/examples"
"$VENV/bin/gama-l" doctor --json --root "$DOC_ROOT" >"$TMP/doctor.json"
"$PYTHON" - "$TMP/doctor.json" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
if not data.get("ok"):
    raise SystemExit("doctor reported failures: %r" % data.get("checks"))
passed = sum(1 for check in data["checks"] if check["ok"])
print("doctor: %d/%d checks passed" % (passed, len(data["checks"])))
PY

# ------------------------------------------------------- 6) twine check (opt)
log "6/8 twine check (optional)"
if [ "${#TWINE_CMD[@]}" -gt 0 ]; then
  # release.py writes a deterministic *source* archive (fixed mtimes, no
  # PKG-INFO), not a PEP 517 sdist, so `twine check` can only validate the
  # wheel. The sdist is still covered by release.py verify + sha256sum.
  TWINE_WHEEL="$(find "$OUT_A" -maxdepth 1 -name '*.whl' -print -quit)"
  [ -n "$TWINE_WHEEL" ] || fail "no wheel for twine check"
  "${TWINE_CMD[@]}" check "$TWINE_WHEEL"
  echo "twine: checked the wheel; the deterministic sdist has no PKG-INFO and is not PyPI-uploadable."
else
  echo "SKIP: twine is not installed; package metadata was still validated by release.py verify."
fi

# ------------------------------------------------------- 7) VSIX package (opt)
log "7/8 VSIX package (optional)"
if [ "${#VSCE_CMD[@]}" -gt 0 ]; then
  EXT_VERSION="$("$PYTHON" -c 'import json;print(json.load(open("vscode/package.json"))["version"])')"
  mkdir -p "$VSIX_DIR"
  VSIX_PATH="$VSIX_DIR/gama-l-$EXT_VERSION.vsix"
  (
    cd vscode
    "${VSCE_CMD[@]}" package --no-dependencies --allow-missing-repository --out "$VSIX_PATH"
  )
  "$PYTHON" - "$VSIX_PATH" "$EXT_VERSION" <<'PY'
import json
import sys
import zipfile

path, expected_version = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(path) as archive:
    names = set(archive.namelist())
    required = {
        "[Content_Types].xml",
        "extension.vsixmanifest",
        "extension/package.json",
        "extension/extension.js",
        "extension/language-configuration.json",
        "extension/readme.md",
        "extension/syntaxes/gama-l.tmLanguage.json",
        "extension/snippets/gama-l.json",
    }
    missing = required - names
    if missing:
        raise SystemExit("VSIX missing entries: %s" % ", ".join(sorted(missing)))
    forbidden = [name for name in names
                 if name.endswith(".py") or "/node_modules/" in name
                 or name.endswith("/smoke.js") or name.endswith("/package.sh")]
    if forbidden:
        raise SystemExit("VSIX contains dev/test junk: %s" % ", ".join(sorted(forbidden)))
    manifest = json.loads(archive.read("extension/package.json").decode("utf-8"))
    if manifest.get("name") != "gama-l" or manifest.get("version") != expected_version:
        raise SystemExit("VSIX package.json name/version mismatch")
print("vsix: gama-l@%s, %d files validated" % (expected_version, len(names)))
PY
else
  echo "SKIP: vsce / @vscode/vsce is not available offline; the extension assets were not packaged."
fi

# ------------------------------------------------------- 8) manifest
log "8/8 release manifest"
export REL_PY_VER="$PY_VER"
export REL_TMP="$TMP"
export REL_OUT_A="$OUT_A"
export REL_OUT_B="$OUT_B"
export REL_VSIX="$VSIX_PATH"
export REL_TWINE_STATUS="$TWINE_STATUS"
export REL_VSCE_STATUS="$VSCE_STATUS"
export REL_SHA256SUM_STATUS="$SHA256SUM_STATUS"
export REL_NODE_STATUS="$NODE_STATUS"
export REL_GIT_STATUS="$GIT_STATUS"
export REL_HELLO="$HELLO"
"$PYTHON" - <<'PY'
import datetime
import hashlib
import json
import os
import pathlib

tmp = pathlib.Path(os.environ["REL_TMP"])
out_a = pathlib.Path(os.environ["REL_OUT_A"])
out_b = pathlib.Path(os.environ["REL_OUT_B"])
release = json.loads((out_a / "RELEASE.json").read_text(encoding="utf-8"))
second = json.loads((out_b / "RELEASE.json").read_text(encoding="utf-8"))
extension = json.loads(
    (pathlib.Path.cwd() / "vscode" / "package.json").read_text(encoding="utf-8"))
doctor = json.loads((tmp / "doctor.json").read_text(encoding="utf-8"))

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

artifacts = []
for entry in release.get("files", []):
    name = entry["name"]
    path = out_a / name
    actual = sha256(path)
    stable = (entry.get("sha256") == actual
              and (second.get("sha256") or {}).get(name) == actual)
    artifacts.append({
        "name": name,
        "size": path.stat().st_size,
        "sha256": actual,
        "reproducible": stable,
    })

vsix_path = pathlib.Path(os.environ["REL_VSIX"]) if os.environ.get("REL_VSIX") else None
vsix = None
if vsix_path and vsix_path.is_file():
    vsix = {
        "name": vsix_path.name,
        "size": vsix_path.stat().st_size,
        "sha256": sha256(vsix_path),
    }

doctor_checks = doctor.get("checks") or []
manifest = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "python": os.environ["REL_PY_VER"],
    "core": {
        "name": release.get("name"),
        "version": release.get("version"),
    },
    "extension": {
        "name": extension.get("name"),
        "version": extension.get("version"),
    },
    "artifacts": artifacts,
    "vsix": vsix,
    "doctor": {
        "ok": bool(doctor.get("ok")),
        "passed": sum(1 for check in doctor_checks if check.get("ok")),
        "total": len(doctor_checks),
    },
    "hello": os.environ.get("REL_HELLO", ""),
    "tools": {
        "sha256sum": os.environ["REL_SHA256SUM_STATUS"],
        "twine": os.environ["REL_TWINE_STATUS"],
        "vsce": os.environ["REL_VSCE_STATUS"],
        "node": os.environ["REL_NODE_STATUS"],
        "git": os.environ["REL_GIT_STATUS"],
    },
}
(tmp / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    encoding="utf-8")

print()
print("== RELEASE MANIFEST ==")
print("python           : %s" % manifest["python"])
print("gama-l core      : %s %s" % (manifest["core"]["name"], manifest["core"]["version"]))
print("vscode extension : %s %s" % (manifest["extension"]["name"], manifest["extension"]["version"]))
print("doctor           : %d/%d checks" % (manifest["doctor"]["passed"], manifest["doctor"]["total"]))
print("hello            : %s" % manifest["hello"])
print("artifacts:")
for item in artifacts:
    print("  %-34s %9d B  sha256=%s  reproducible=%s"
          % (item["name"], item["size"], item["sha256"],
             "yes" if item["reproducible"] else "NO"))
print("vsix             : %s" % ("none (vsce unavailable)" if vsix is None else
                                  "%s (%d B, sha256=%s)" % (
                                      vsix["name"], vsix["size"], vsix["sha256"])))
print("tools            : sha256sum=%s; twine=%s; vsce=%s; node=%s; git=%s"
      % (manifest["tools"]["sha256sum"], manifest["tools"]["twine"],
         manifest["tools"]["vsce"], manifest["tools"]["node"],
         manifest["tools"]["git"]))
PY

if [ -n "$KEEP_DIST" ]; then
  mkdir -p "$KEEP_DIST"
  cp "$OUT_A"/*.whl "$OUT_A"/*.tar.gz "$OUT_A"/SHA256SUMS "$OUT_A"/RELEASE.json "$KEEP_DIST"/
  if [ -n "$VSIX_PATH" ] && [ -f "$VSIX_PATH" ]; then
    cp "$VSIX_PATH" "$KEEP_DIST"/
  fi
  cp "$TMP/manifest.json" "$KEEP_DIST/release-manifest.json"
  echo "kept artifacts: $KEEP_DIST"
fi

if [ -n "$MANIFEST_OUT" ]; then
  mkdir -p "$(dirname "$MANIFEST_OUT")"
  cp "$TMP/manifest.json" "$MANIFEST_OUT"
  echo "manifest: $MANIFEST_OUT"
fi

log "release_dry_run: OK"
