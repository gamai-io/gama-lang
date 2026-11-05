#!/usr/bin/env bash
# Build PyPI-compatible wheel+sdist, verify them, and upload to PyPI.
# Requires PYPI_TOKEN in the environment (or /root/.gama_api_keys.env).
# Usage: scripts/publish_pypi.sh [--yes] [--check]
set -euo pipefail
cd "$(dirname "$0")/.."

YES=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --check) CHECK=1 ;;
    *) echo "usage: $0 [--yes] [--check]" >&2; exit 2 ;;
  esac
done

if [[ -z "${PYPI_TOKEN:-}" && -f /root/.gama_api_keys.env ]]; then
  set -a; . /root/.gama_api_keys.env; set +a
fi
if [[ -z "${PYPI_TOKEN:-}" ]]; then
  echo "PYPI_TOKEN is not set" >&2
  exit 2
fi

out="$(mktemp -d /tmp/gama-pypi-XXXXXX)"
venv=""
cleanup() { rm -rf "$out" ${venv:+"$venv"}; }
trap cleanup EXIT
PY=python3
if ! "$PY" -m build --version >/dev/null 2>&1 || ! "$PY" -m twine --version >/dev/null 2>&1; then
  venv="$(mktemp -d /tmp/gama-pypi-tools-XXXXXX)"
  "$PY" -m venv "$venv"
  "$venv/bin/pip" install -q build twine
  PY="$venv/bin/python"
fi
"$PY" -m build --outdir "$out" . >/dev/null
"$PY" -m twine check "$out"/* >/dev/null

version="$("$PY" - <<'PY'
import tomllib
print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])
PY
)"
echo "built gama-l $version:"
ls -l "$out"

if [[ "$CHECK" == "1" ]]; then
  echo "check-only: not uploading"
  exit 0
fi
if [[ "$YES" != "1" ]]; then
  read -r -p "Upload gama-l $version to PyPI? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }
fi
"$PY" -m twine upload --non-interactive --username __token__ --password "$PYPI_TOKEN" "$out"/*
echo "uploaded gama-l $version"
