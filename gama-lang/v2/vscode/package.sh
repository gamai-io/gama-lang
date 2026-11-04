#!/usr/bin/env bash
#
# Reproducible packaging + validation for the GAMA-L VS Code extension.
#
#   bash package.sh            # package, validate contents, check reproducibility
#   bash package.sh --install  # additionally install into a disposable profile (needs `code`)
#
# Environment:
#   VSCE_VERSION   @vscode/vsce version to run through npx (default: 4.1.0)
#   OUT_DIR        output directory (default: ./dist)
#
# The .vsix is a build artifact: it is written to dist/ (git-ignored) and never
# committed. Contents are validated against an exact expected file list so dev
# junk (smoke.js, node_modules, tests, Python core) cannot slip into the package.
#
# Known limitation: vsce writes the current time into ZIP local headers, so two
# runs are NOT byte-identical. This script compares a content digest (entry name
# + entry SHA-256, order-stable) and requires that to match; the byte hashes are
# reported for the record.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

VSCE_VERSION="${VSCE_VERSION:-4.1.0}"
OUT_DIR="${OUT_DIR:-$HERE/dist}"

INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --install) INSTALL=1 ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
    esac
done

VERSION="$(python3 -c 'import json;print(json.load(open("package.json"))["version"])')"
VSIX="$OUT_DIR/gama-l-$VERSION.vsix"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$OUT_DIR"
rm -f "$VSIX" "$TMP/second.vsix"

pack() { # pack <output>
    local out="$1"
    # Pinned toolchain; npx caches it, so repeated runs do not re-download.
    npx -y "@vscode/vsce@$VSCE_VERSION" package \
        --allow-missing-repository \
        --out "$out"
}

echo "== GAMA-L VSIX =="
echo "source : $HERE"
echo "version: $VERSION"
echo "vsce   : @vscode/vsce@$VSCE_VERSION (npx)"
echo

echo "[1/4] packaging run 1 -> $VSIX"
pack "$VSIX" >"$TMP/run1.log" 2>&1 || { cat "$TMP/run1.log"; exit 1; }

echo "[2/4] packaging run 2 (reproducibility probe)"
pack "$TMP/second.vsix" >"$TMP/run2.log" 2>&1 || { cat "$TMP/run2.log"; exit 1; }

echo "[3/4] validating contents"
python3 - "$VSIX" "$TMP/second.vsix" "$VERSION" <<'PY'
import hashlib
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

first, second, expected_version = sys.argv[1], sys.argv[2], sys.argv[3]

EXPECTED = [
    "[Content_Types].xml",
    "extension.vsixmanifest",
    "extension/LICENSE.txt",
    "extension/extension.js",
    "extension/language-configuration.json",
    "extension/package.json",
    "extension/readme.md",
    "extension/snippets/gama-l.json",
    "extension/syntaxes/gama-l.tmLanguage.json",
]
FORBIDDEN_NAMES = {"smoke.js", "package.sh"}
FORBIDDEN_PARTS = {"node_modules", ".git", "tests", "__pycache__", "dist"}

def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)

def inspect(path, label, verbose=False):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        fail("%s is not a valid zip: %s" % (label, exc))
    bad = z.testzip()
    if bad is not None:
        fail("%s has a corrupt entry: %s" % (label, bad))

    names = sorted(z.namelist())
    if names != EXPECTED:
        missing = [n for n in EXPECTED if n not in names]
        extra = [n for n in names if n not in EXPECTED]
        fail("%s file list mismatch; missing=%r extra=%r" % (label, missing, extra))

    for name in names:
        parts = set(name.split("/"))
        base = name.rsplit("/", 1)[-1]
        if parts & FORBIDDEN_PARTS or base in FORBIDDEN_NAMES or name.endswith(".py"):
            fail("%s contains dev/test junk: %s" % (label, name))
        if name not in ("[Content_Types].xml", "extension.vsixmanifest") and not name.startswith("extension/"):
            fail("%s has unexpected top-level entry: %s" % (label, name))

    # --- manifest identity -------------------------------------------------
    manifest = z.read("extension.vsixmanifest").decode("utf-8")
    ns = {"v": "http://schemas.microsoft.com/developer/vsx-schema/2011"}
    root = ET.fromstring(manifest)
    identity = root.find("v:Metadata/v:Identity", ns)
    if identity is None:
        fail("%s manifest has no Identity" % label)
    ident = identity.attrib
    if ident.get("Id") != "gama-l":
        fail("%s manifest Id=%r, expected 'gama-l'" % (label, ident.get("Id")))
    if ident.get("Publisher") != "gama":
        fail("%s manifest Publisher=%r, expected 'gama'" % (label, ident.get("Publisher")))
    if ident.get("Version") != expected_version:
        fail("%s manifest Version=%r, expected %r" % (label, ident.get("Version"), expected_version))

    assets = {a.get("Path") for a in root.findall(".//v:Assets/v:Asset", ns)}
    for required in ("extension/package.json", "extension/readme.md", "extension/LICENSE.txt"):
        if required not in assets:
            fail("%s manifest missing asset %s" % (label, required))
    if "Microsoft.VisualStudio.Code.Manifest" not in {
        a.get("Type") for a in root.findall(".//v:Assets/v:Asset", ns)
    }:
        fail("%s manifest missing Code.Manifest asset type" % label)

    # --- extension manifest ------------------------------------------------
    pkg = json.loads(z.read("extension/package.json").decode("utf-8"))
    if pkg.get("name") != "gama-l" or pkg.get("version") != expected_version:
        fail("%s package.json name/version mismatch" % label)
    if pkg.get("main") != "./extension.js":
        fail("%s package.json main=%r" % (label, pkg.get("main")))
    if pkg.get("publisher") != "gama":
        fail("%s package.json publisher=%r" % (label, pkg.get("publisher")))
    if "^1.80.0" not in str(pkg.get("engines", {}).get("vscode", "")):
        fail("%s package.json engines.vscode=%r" % (label, pkg.get("engines")))

    contributes = pkg.get("contributes", {})
    languages = contributes.get("languages", [])
    if not languages or languages[0].get("id") != "gama-l":
        fail("%s package.json language contribution missing" % label)
    if ".gm" not in languages[0].get("extensions", []):
        fail("%s package.json .gm extension missing" % label)
    grammars = contributes.get("grammars", [])
    if not grammars or grammars[0].get("path") != "./syntaxes/gama-l.tmLanguage.json":
        fail("%s package.json grammar contribution missing" % label)
    snippets = contributes.get("snippets", [])
    if not snippets or snippets[0].get("path") != "./snippets/gama-l.json":
        fail("%s package.json snippet contribution missing" % label)

    # --- grammar -----------------------------------------------------------
    grammar = json.loads(z.read("extension/syntaxes/gama-l.tmLanguage.json").decode("utf-8"))
    if grammar.get("scopeName") != "source.gama-l":
        fail("%s grammar scopeName=%r" % (label, grammar.get("scopeName")))
    if not grammar.get("patterns"):
        fail("%s grammar has no patterns" % label)

    # --- snippets ----------------------------------------------------------
    snips = json.loads(z.read("extension/snippets/gama-l.json").decode("utf-8"))
    if not isinstance(snips, dict) or not snips:
        fail("%s snippets file is empty/invalid" % label)
    for key, item in snips.items():
        if not isinstance(item, dict) or "prefix" not in item or "body" not in item:
            fail("%s snippet %r must have prefix and body" % (label, key))

    # --- language configuration -------------------------------------------
    lang = json.loads(z.read("extension/language-configuration.json").decode("utf-8"))
    if not lang.get("brackets") or not lang.get("autoClosingPairs"):
        fail("%s language-configuration.json lacks brackets/autoClosingPairs" % label)

    # --- client code -------------------------------------------------------
    ext = z.read("extension/extension.js").decode("utf-8")
    if "require('vscode')" not in ext:
        fail("%s extension.js does not require vscode" % label)
    if "vscode-languageclient" in ext or "vscode-languageserver" in ext:
        fail("%s extension.js unexpectedly depends on languageclient" % label)
    if not re.search(r"function\s+activate\s*\(", ext):
        fail("%s extension.js has no activate() function" % label)
    if "module.exports" not in ext or "deactivate" not in ext:
        fail("%s extension.js does not export activate/deactivate" % label)

    # --- docs --------------------------------------------------------------
    for name, minimum in (("extension/readme.md", 200), ("extension/LICENSE.txt", 100)):
        if len(z.read(name)) < minimum:
            fail("%s is suspiciously small: %s" % (label, name))

    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(z.read(name)).digest())

    if verbose:
        print("  files: %d" % len(names))
        for name in names:
            print("    %-55s %7d B" % (name, z.getinfo(name).file_size))

    return digest.hexdigest(), len(names)

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

digest1, count = inspect(first, "primary", verbose=True)
digest2, count2 = inspect(second, "second run")
if count != count2:
    fail("file counts differ between runs")
if digest1 != digest2:
    fail("content digest differs between runs: %s vs %s" % (digest1, digest2))

byte1, byte2 = sha256_file(first), sha256_file(second)
import os
size = os.path.getsize(first)
print("  expected file list: OK (%d files)" % count)
print("  manifest identity: OK (gama-l %s / gama)" % expected_version)
print("  grammar+snippets+lang-config: OK")
print("  extension.js client: OK (no vscode-languageclient)")
print("  dev/test junk: none")
print("  content digest: %s" % digest1)
print("  byte-identical: %s" % ("yes" if byte1 == byte2 else "no (ZIP timestamps)"))
print("  sha256(run1)=%s" % byte1)
print("  sha256(run2)=%s" % byte2)
print("  size: %d bytes (%.2f KiB)" % (size, size / 1024.0))
PY

echo "[4/4] install check"
if [ "$INSTALL" -eq 1 ]; then
    if command -v code >/dev/null 2>&1; then
        echo "  installing into disposable profile: $TMP/profile"
        code --user-data-dir "$TMP/profile" --extensions-dir "$TMP/extensions" \
            --install-extension "$VSIX" --force
        code --user-data-dir "$TMP/profile" --extensions-dir "$TMP/extensions" \
            --list-extensions --show-versions | grep -i '^gama\.gama-l@' \
            || { echo "FAIL: extension not listed after install" >&2; exit 1; }
        echo "  install: OK"
    else
        echo "  SKIPPED: no 'code' binary on PATH; no disposable profile available."
    fi
else
    if command -v code >/dev/null 2>&1; then
        echo "  'code' found; rerun with --install for a disposable-profile install test."
    else
        echo "  SKIPPED: no 'code' binary on PATH; no disposable profile available."
    fi
fi

echo
echo "OK: $VSIX"
