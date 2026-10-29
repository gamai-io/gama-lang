"""Reproducible release build + checksums for GAMA-L 2 core.

Creates, in ``out_dir`` (nothing is uploaded or published):

* ``gama_l-<version>-py3-none-any.whl`` -- built through ``pip wheel`` with a
  pinned ``SOURCE_DATE_EPOCH`` so repeated builds on the same tree are
  byte-identical;
* ``gama-l-<version>.tar.gz`` -- deterministic source archive (fixed mtime,
  uid/gid, uname/gname, sorted member order, gzip mtime 0);
* ``SHA256SUMS`` and ``RELEASE.json`` describing the artifacts.

``verify(out_dir)`` re-checks the recorded hashes and inspects both archives,
so a release directory can be validated after copying it around.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

__all__ = ["ReleaseError", "build", "verify", "main"]

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent

#: Files that make up the source archive, besides ``examples/*.gm``.
SOURCE_FILES = (
    "gamal2.py",
    "gama_lsp.py",
    "gama_cli.py",
    "gama_fmt.py",
    "gama_doctor.py",
    "gama_mail.py",
    "gama_registry.py",
    "gama_registry_server.py",
    "gama_ai.py",
    "release.py",
    "pyproject.toml",
    "README.md",
    "MANIFEST.in",
)

#: Modules the wheel must expose (kept in sync with ``pyproject.toml``).
WHEEL_MODULES = tuple(name for name in SOURCE_FILES if name.endswith(".py"))

#: Fixed timestamp for every archive member (1980-01-01T00:00:00Z).
SOURCE_EPOCH = 0

WHEEL_TIMEOUT = 120
_MARKER = "release"


class ReleaseError(Exception):
    """Raised when the release cannot be built or is invalid."""


# --------------------------------------------------------------------------- helpers
def _version() -> str:
    """Project version from ``pyproject.toml`` (tomllib, regex fallback)."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib

        data = tomllib.loads(text)
        version = data.get("project", {}).get("version")
    except ImportError:  # Python 3.10
        version = None
    if not version:
        match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
        if match:
            version = match.group(1)
    if not version:
        raise ReleaseError("no [project].version in pyproject.toml")
    return str(version)


def _name() -> str:
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib

        name = tomllib.loads(text).get("project", {}).get("name")
    except ImportError:
        name = None
    if not name:
        match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
        name = match.group(1) if match else "gama-l"
    return str(name)


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_entries() -> list[tuple[str, pathlib.Path]]:
    """(archive name, source path) pairs in a fixed order."""
    entries: list[tuple[str, pathlib.Path]] = []
    for name in SOURCE_FILES:
        source = PROJECT_ROOT / name
        if not source.is_file():
            raise ReleaseError("missing source file: %s" % name)
        entries.append((name, source))
    for example in sorted((PROJECT_ROOT / "examples").glob("*.gm")):
        entries.append(("examples/" + example.name, example))
    return entries


#: Names the release build itself writes into ``out_dir``.
_KNOWN_RELEASE_NAMES = frozenset({"SHA256SUMS", "RELEASE.json"})
#: Suffixes of artifacts the release build itself writes.
_KNOWN_RELEASE_SUFFIXES = (".whl", ".tar.gz")


def _validate_out_dir(out_dir: pathlib.Path) -> pathlib.Path:
    """Return the resolved output path, refusing dangerous locations."""
    raw = pathlib.Path(out_dir)
    resolved = raw.expanduser().resolve()
    root = PROJECT_ROOT.resolve()
    home = pathlib.Path.home().resolve()

    if resolved == pathlib.Path(resolved.anchor):
        raise ReleaseError("refusing to use filesystem root as release output: %s" % resolved)
    if resolved == home:
        raise ReleaseError("refusing to use home directory as release output: %s" % resolved)
    if any(".git" in part for part in resolved.parts):
        raise ReleaseError("refusing to use a path containing .git as release output: %s" % resolved)
    if resolved == root or root in resolved.parents or resolved in root.parents:
        raise ReleaseError(
            "refusing to use project/source directory as release output: %s" % out_dir
        )
    return resolved


def _is_known_artifact(path: pathlib.Path) -> bool:
    """True for a regular file the release build owns (wheel/sdist/checksums)."""
    return (
        path.is_file()
        and not path.is_symlink()
        and (path.name in _KNOWN_RELEASE_NAMES or path.name.endswith(_KNOWN_RELEASE_SUFFIXES))
    )


def _assert_cleanable(out_dir: pathlib.Path, force: bool = False) -> pathlib.Path:
    """Validate ``out_dir`` and ensure it can be cleaned without data loss."""
    raw = pathlib.Path(out_dir)
    resolved = _validate_out_dir(raw)
    if raw.is_symlink():
        raise ReleaseError("refusing to clean a symlink: %s" % raw)
    if not resolved.exists():
        return resolved
    if resolved.is_symlink():
        raise ReleaseError("refusing to clean a symlink: %s" % resolved)
    if not resolved.is_dir():
        if not force:
            raise ReleaseError(
                "release output is a file; use --force to replace it: %s" % resolved
            )
        return resolved

    children = list(resolved.iterdir())
    if not children:
        return resolved
    unknown = sorted(child.name for child in children if not _is_known_artifact(child))
    if unknown and not force:
        raise ReleaseError(
            "refusing to delete unknown entries in %s: %s (use --force)"
            % (resolved, ", ".join(unknown))
        )
    return resolved


def _clean_dir(out_dir: pathlib.Path, force: bool = False) -> None:
    """Prepare ``out_dir`` for a release, deleting only owned or forced entries."""
    resolved = _assert_cleanable(out_dir, force=force)
    if resolved.exists():
        if resolved.is_symlink():
            raise ReleaseError("refusing to clean a symlink: %s" % resolved)
        if resolved.is_dir():
            for child in resolved.iterdir():
                if not force and not _is_known_artifact(child):
                    raise ReleaseError(
                        "refusing to delete unknown entry in %s: %s"
                        % (resolved, child.name)
                    )
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            if not force:
                raise ReleaseError(
                    "release output is a file; use --force to replace it: %s" % resolved
                )
            resolved.unlink()
    resolved.mkdir(parents=True, exist_ok=True)


def _pip_env() -> dict:
    env = dict(os.environ)
    # Reproducible wheel entries and no network access / index lookups.
    env.update({
        "SOURCE_DATE_EPOCH": str(SOURCE_EPOCH),
        "PYTHONHASHSEED": "0",
        "PIP_NO_INDEX": "1",
        "PIP_NO_INPUT": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_CACHE_DIR": "1",
    })
    return env


def _wheel(out_dir: pathlib.Path) -> pathlib.Path:
    cmd = [
        sys.executable, "-m", "pip", "wheel", ".",
        "--no-deps", "--no-build-isolation", "--no-index", "-w", str(out_dir),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=WHEEL_TIMEOUT,
            env=_pip_env(),
        )
    except FileNotFoundError as exc:
        raise ReleaseError("pip is not available: %s" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError(
            "pip wheel timed out after %ss" % WHEEL_TIMEOUT
        ) from exc
    if proc.returncode != 0:
        raise ReleaseError(
            "pip wheel failed (rc=%s):\n%s\n%s"
            % (proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip())
        )
    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseError(
            "expected exactly one wheel in %s, found %d" % (out_dir, len(wheels))
        )
    return wheels[0]


def _tar_member(name: str, data: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = SOURCE_EPOCH
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    info.type = tarfile.REGTYPE
    return info


def _sdist(out_dir: pathlib.Path, name: str, version: str) -> pathlib.Path:
    target = out_dir / ("%s-%s.tar.gz" % (name, version))
    prefix = "%s-%s/" % (name, version)
    entries = _source_entries()

    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=buffer, mtime=SOURCE_EPOCH,
        compresslevel=9,
    ) as gz:
        with tarfile.open(fileobj=gz, mode="w:", format=tarfile.PAX_FORMAT) as tar:
            for arcname, source in entries:
                data = source.read_bytes()
                info = _tar_member(prefix + arcname, data)
                tar.addfile(info, io.BytesIO(data))
    target.write_bytes(buffer.getvalue())
    return target


def _cleanup_build_junk(before: set[str]) -> None:
    """Remove ``build/`` and ``*.egg-info`` only when this build created them."""
    for pattern in ("build", "*.egg-info"):
        for path in PROJECT_ROOT.glob(pattern):
            if path.name in before:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- API
def build(out_dir: pathlib.Path, force: bool = False) -> dict:
    """Build wheel + deterministic sdist + SHA256SUMS + RELEASE.json.

    The artifacts are produced in a temporary staging directory and moved into
    ``out_dir`` only after the whole build succeeds, so a failed build never
    destroys an existing release.  Unknown files in ``out_dir`` are refused
    unless ``force`` is true; known release artifacts may be replaced.
    """
    out_dir = _validate_out_dir(pathlib.Path(out_dir))
    _assert_cleanable(out_dir, force=force)
    name = _name()
    version = _version()
    junk_before = {p.name for p in PROJECT_ROOT.glob("build")}
    junk_before |= {p.name for p in PROJECT_ROOT.glob("*.egg-info")}

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(
        tempfile.mkdtemp(prefix=".gama-release-", dir=str(out_dir.parent))
    )
    try:
        try:
            wheel = _wheel(staging)
            sdist = _sdist(staging, name, version)
        finally:
            _cleanup_build_junk(junk_before)

        files = []
        for path in (wheel, sdist):
            files.append({
                "name": path.name,
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            })

        stamp = _now()
        manifest = {
            "name": name,
            "version": version,
            "python": "%d.%d.%d" % sys.version_info[:3],
            "built_at": stamp,
            "build_time": stamp,
            "files": files,
            "sha256": {entry["name"]: entry["sha256"] for entry in files},
        }

        sums_path = staging / "SHA256SUMS"
        sums_path.write_text(
            "".join("%s  %s\n" % (entry["sha256"], entry["name"]) for entry in files),
            encoding="utf-8",
        )
        release_path = staging / "RELEASE.json"
        release_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _clean_dir(out_dir, force=force)
        for child in sorted(staging.iterdir()):
            os.replace(str(child), str(out_dir / child.name))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "ok": True,
        "out": str(out_dir),
        "version": version,
        "wheel": wheel.name,
        "sdist": sdist.name,
        "sha256sums": sums_path.name,
        "release": release_path.name,
        "files": manifest["sha256"],
        "python": manifest["python"],
        "built_at": stamp,
    }


def _check_wheel(path: pathlib.Path, errors: list[str]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            present = {name.rsplit("/", 1)[-1] for name in archive.namelist()}
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append("wheel unreadable (%s): %s" % (path.name, exc))
        return
    missing = [module for module in WHEEL_MODULES if module not in present]
    if missing:
        errors.append("wheel missing modules: %s" % ", ".join(missing))


def _check_sdist(path: pathlib.Path, errors: list[str]) -> None:
    try:
        with tarfile.open(str(path), mode="r:gz") as archive:
            names = archive.getnames()
    except (OSError, tarfile.TarError) as exc:
        errors.append("sdist unreadable (%s): %s" % (path.name, exc))
        return
    if not any(name == "pyproject.toml" or name.endswith("/pyproject.toml") for name in names):
        errors.append("sdist does not contain pyproject.toml")
    for module in WHEEL_MODULES:
        if not any(name == module or name.endswith("/" + module) for name in names):
            errors.append("sdist missing module: %s" % module)


def _safe_member(out_dir: pathlib.Path, name: str) -> pathlib.Path | None:
    """Resolve a manifest file name, refusing anything outside ``out_dir``."""
    if not isinstance(name, str) or not name:
        return None
    relative = pathlib.PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (out_dir / name).resolve()
    try:
        candidate.relative_to(out_dir.resolve())
    except ValueError:
        return None
    return candidate


def verify(out_dir: pathlib.Path) -> dict:
    """Verify hashes recorded in RELEASE.json plus archive contents."""
    out_dir = pathlib.Path(out_dir)
    errors: list[str] = []
    release_path = out_dir / "RELEASE.json"
    if not release_path.is_file():
        return {"ok": False, "errors": ["missing RELEASE.json in %s" % out_dir]}
    if not (out_dir / "SHA256SUMS").is_file():
        return {"ok": False, "errors": ["missing SHA256SUMS in %s" % out_dir]}
    try:
        manifest = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": ["invalid RELEASE.json: %s" % exc]}

    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["invalid RELEASE.json: not an object"]}
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return {"ok": False, "errors": ["RELEASE.json has no files"]}
    if not manifest.get("version"):
        errors.append("RELEASE.json has no version")

    wheels = []
    sdists = []
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            errors.append("malformed file entry: %r" % (entry,))
            continue
        fname = entry.get("name")
        expected = entry.get("sha256")
        if not isinstance(fname, str) or not fname or not isinstance(expected, str) or not expected:
            errors.append("file entry without name/sha256: %r" % (entry,))
            continue
        if fname in seen:
            errors.append("duplicate file entry: %s" % fname)
            continue
        seen.add(fname)
        path = _safe_member(out_dir, fname)
        if path is None:
            errors.append("file entry escapes out_dir: %s" % fname)
            continue
        if not path.is_file():
            errors.append("missing file: %s" % fname)
            continue
        actual = _sha256(path)
        if actual != expected:
            errors.append("sha256 mismatch for %s (expected %s, got %s)"
                          % (fname, expected, actual))
        if fname.endswith(".whl"):
            wheels.append(path)
        elif fname.endswith((".tar.gz", ".tgz")):
            sdists.append(path)

    if len(wheels) != 1:
        errors.append("expected exactly one wheel, found %d" % len(wheels))
    if len(sdists) != 1:
        errors.append("expected exactly one sdist, found %d" % len(sdists))

    for wheel in wheels:
        _check_wheel(wheel, errors)
    for sdist in sdists:
        _check_sdist(sdist, errors)

    if (out_dir / "SHA256SUMS").is_file():
        recorded = {}
        for line in (out_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                recorded[parts[1].strip()] = parts[0]
        for name, digest in (manifest.get("sha256") or {}).items():
            if recorded.get(name) != digest:
                errors.append("SHA256SUMS disagrees with RELEASE.json for %s" % name)

    return {"ok": not errors, "errors": errors}


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="release", description="Build or verify a GAMA-L 2 release directory."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="build wheel, sdist and checksums")
    build_parser.add_argument(
        "--out", default="dist",
        help="output directory (must be outside the package source tree)",
    )
    build_parser.add_argument(
        "--force", action="store_true",
        help="replace unknown files and non-empty output directories",
    )
    verify_parser = sub.add_parser("verify", help="verify an existing release directory")
    verify_parser.add_argument("--out", default="dist", help="release directory")
    args = parser.parse_args(argv)

    out = pathlib.Path(args.out).expanduser().resolve()
    try:
        result = (
            build(out, force=args.force) if args.command == "build" else verify(out)
        )
    except ReleaseError as exc:
        result = {"ok": False, "errors": [str(exc)]}
    except OSError as exc:
        result = {"ok": False, "errors": ["I/O error: %s" % exc]}

    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
