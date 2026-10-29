"""Local / static package registry for GAMA-L.

A registry is a JSON document:

    {"version": 1, "packages": {
        "math": {"version": "0.1.0", "git": "../mathrepo", "rev": "<sha>"}
    }}

A registry may be loaded from a local path, a ``file://`` URL, or an
``http://`` / ``https://`` URL.  Remote registries are fetched with a 10 s
timeout, a 1 MiB body limit, a ``application/json`` or ``text/plain``
content-type, and the same schema validation as local files.  Every redirect
hop is validated before it is followed: scheme downgrades are rejected, and a
public host may not redirect into a private, loopback, link-local or CGNAT
target.  This module is intentionally independent from ``gamal2`` so it can be
used before the interpreter boots, while still writing the same ``gamal.toml``
shape that ``gamal2._read_toml`` / ``_pkg_install`` understand.
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

__all__ = [
    "RegistryError",
    "load_registry",
    "search",
    "resolve",
    "add_dependency",
]

REGISTRY_VERSION = 1
_PACKAGE_FIELDS = ("name", "version", "git", "rev")

_MAX_REGISTRY_BYTES = 1024 * 1024
_HTTP_TIMEOUT = 10.0
_HTTP_CONTENT_TYPES = ("application/json", "text/plain")
_PRIVATE_ALLOW_ENV = "GAMA_L_REGISTRY_ALLOW_PRIVATE"


class RegistryError(Exception):
    """Raised when a registry or a dependency entry is invalid."""


# --------------------------------------------------------------------------- helpers
def _safe_name(name: object) -> bool:
    """A dependency name that is safe as a path segment and TOML bare key."""
    if not isinstance(name, str) or not name:
        return False
    if name.startswith(".") or ".." in name:
        return False
    if "/" in name or "\\" in name:
        return False
    # Characters that would break the small TOML subset used by gamal2.
    return not any(c.isspace() or c in '=,{}[]"\'#' for c in name)


def _fail(msg: str) -> "RegistryError":
    return RegistryError(msg)


def _source_path(source: object) -> pathlib.Path:
    s = str(source)
    if s.startswith("file:") or "://" in s:
        parts = urllib.parse.urlsplit(s)
        if parts.scheme != "file":
            raise _fail(
                "unsupported registry source %r: use a path or file:// URL"
                % s
            )
        path = urllib.parse.unquote(parts.path)
        if parts.netloc and parts.netloc.lower() != "localhost":
            path = "//" + parts.netloc + path
        return pathlib.Path(path)
    # Plain path (including Windows drive letters such as C:\pkg).
    return pathlib.Path(s)


def _validate(data: object) -> dict:
    if not isinstance(data, dict):
        raise _fail("registry root must be a JSON object")
    version = data.get("version")
    if type(version) is not int or version != REGISTRY_VERSION:
        raise _fail(
            "unsupported registry version %r (expected %d)"
            % (version, REGISTRY_VERSION)
        )
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise _fail("registry 'packages' must be an object")

    for name, entry in packages.items():
        if not _safe_name(name):
            raise _fail("invalid package name %r (no '..', '/', '\\' or whitespace)" % (name,))
        if not isinstance(entry, dict):
            raise _fail("package %r must be an object" % (name,))
        for field in ("version", "git"):
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                raise _fail("package %r: %r must be a non-empty string" % (name, field))
        if "rev" in entry and not isinstance(entry["rev"], str):
            raise _fail("package %r: 'rev' must be a string" % (name,))
    return data


def _packages(index: object) -> dict:
    if not isinstance(index, dict):
        raise _fail("registry index must be an object")
    packages = index.get("packages")
    if not isinstance(packages, dict):
        raise _fail("registry index has no 'packages' object")
    return packages


def _record(name: str, entry: dict) -> dict:
    return {
        "name": name,
        "version": entry.get("version"),
        "git": entry.get("git"),
        "rev": entry.get("rev"),
    }


def _split_source(source: object) -> tuple[str, str]:
    """Return ``(scheme, original)``; plain paths yield an empty scheme."""
    s = str(source)
    if "://" in s:
        return urllib.parse.urlsplit(s).scheme.lower(), s
    if s[:5].lower() == "file:":
        return "file", s
    return "", s


def _load_path(path: pathlib.Path) -> dict:
    if not path.exists():
        raise _fail("registry not found: %s" % path)
    if not path.is_file():
        raise _fail("registry is not a file: %s" % path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        raise _fail("cannot read registry %s: %s" % (path, ex)) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        raise _fail("invalid registry JSON in %s: %s" % (path, ex)) from None
    return _validate(data)


def _http_media_type(headers: object) -> str:
    raw = headers.get("Content-Type", "") if headers is not None else ""
    return raw.split(";", 1)[0].strip().lower()


_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED = ("is_private", "is_loopback", "is_link_local", "is_reserved",
            "is_multicast", "is_unspecified")


def _private_allowed() -> bool:
    return os.environ.get(_PRIVATE_ALLOW_ENV) == "1"


def _addr_forbidden(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # ``is_global`` already covers private, loopback, link-local, reserved and
    # unspecified ranges on modern Python; multicast is still "global" there,
    # and CGNAT (100.64/10) is not ``is_private`` on some versions.
    if not addr.is_global:
        return True
    if any(getattr(addr, name) for name in _BLOCKED):
        return True
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_V4:
        return True
    return False


def _host_is_private(host: str) -> bool:
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(host, None)
            ]
        except (OSError, ValueError):
            # Fail closed: an unresolvable host must not be treated as public.
            return not _private_allowed()
    return any(_addr_forbidden(a) for a in addrs)


class _GuardRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every redirect hop before urllib follows it.

    Checking only the final URL after ``urlopen`` returns would already have
    issued the request to the (possibly internal) redirect target.  This
    handler rejects scheme downgrades and private/loopback/CGNAT targets at
    each hop, so the guarded request never leaves the allowed origin space.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(newurl)
        scheme = target.scheme.lower()
        if scheme not in ("http", "https"):
            raise _fail(
                "registry redirected to unsupported scheme %r (from %s)"
                % (scheme, req.full_url)
            )
        if original.scheme.lower() == "https" and scheme == "http":
            raise _fail(
                "registry redirect downgraded https to http: %s" % newurl
            )
        host = target.hostname
        if not host:
            raise _fail("registry redirect target has no host: %s" % newurl)
        if not _private_allowed() and _host_is_private(host):
            raise _fail(
                "registry redirect target is private/loopback: %s" % newurl
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _load_http(url: str) -> dict:
    parsed = urllib.parse.urlsplit(url)
    original_scheme = parsed.scheme.lower()
    if not parsed.hostname:
        raise _fail("registry URL has no host: %s" % url)
    if not _private_allowed() and _host_is_private(parsed.hostname):
        raise _fail("registry host is private/loopback; set %s=1 to allow" % _PRIVATE_ALLOW_ENV)
    opener = urllib.request.build_opener(_GuardRedirectHandler())
    try:
        with opener.open(url, timeout=_HTTP_TIMEOUT) as resp:
            final_url = resp.geturl() or url
            final = urllib.parse.urlsplit(final_url)
            scheme = final.scheme.lower()
            if scheme not in ("http", "https"):
                raise _fail(
                    "registry redirected to unsupported scheme %r (from %s)"
                    % (scheme, url)
                )
            if original_scheme == "https" and scheme == "http":
                raise _fail("registry redirect downgraded https to http: %s" % final_url)
            if not _private_allowed():
                if not final.hostname:
                    raise _fail("registry redirect has no host: %s" % final_url)
                if _host_is_private(final.hostname):
                    raise _fail(
                        "registry redirect host is private/loopback: %s" % final_url
                    )
            media = _http_media_type(resp.headers)
            if not media or media not in _HTTP_CONTENT_TYPES:
                raise _fail(
                    "unsupported registry content type %r at %s: "
                    "expected application/json or text/plain" % (media, url)
                )
            raw = resp.read(_MAX_REGISTRY_BYTES + 1)
            charset = resp.headers.get_content_charset() or "utf-8"
    except RegistryError:
        raise
    except urllib.error.HTTPError as ex:
        raise _fail(
            "cannot fetch registry %s: HTTP %s %s" % (url, ex.code, ex.reason)
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise _fail("cannot fetch registry %s: %s" % (url, ex)) from None

    if len(raw) > _MAX_REGISTRY_BYTES:
        raise _fail(
            "registry at %s is larger than %d bytes" % (url, _MAX_REGISTRY_BYTES)
        )
    try:
        text = raw.decode(charset)
    except (UnicodeDecodeError, LookupError) as ex:
        raise _fail("cannot decode registry %s as %s: %s" % (url, charset, ex)) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        raise _fail("invalid registry JSON at %s: %s" % (url, ex)) from None
    return _validate(data)


# --------------------------------------------------------------------------- public
def load_registry(source: str) -> dict:
    """Read and validate a registry from a path, ``file://``, or http(s) URL."""
    scheme, ref = _split_source(source)
    if scheme in ("http", "https"):
        return _load_http(ref)
    if scheme == "file":
        return _load_path(_source_path(ref))
    if scheme:
        raise _fail(
            "unsupported registry source %r: use a path, file://, "
            "http:// or https:// URL" % ref
        )
    return _load_path(pathlib.Path(ref))


def search(index: dict, query: str = "") -> list[dict]:
    """Return package records whose name contains ``query`` (case-insensitive)."""
    packages = _packages(index)
    needle = (query or "").lower()
    rows = [
        _record(name, entry)
        for name, entry in packages.items()
        if needle in name.lower()
    ]
    rows.sort(key=lambda row: (row["name"].lower(), row["name"]))
    return rows


def resolve(index: dict, name: str, version: str | None = None) -> dict:
    """Return the registry record for ``name``, optionally enforcing a version."""
    if not _safe_name(name):
        raise _fail("invalid package name %r" % (name,))
    packages = _packages(index)
    if name not in packages:
        raise _fail("package not found: %s" % name)
    entry = packages[name]
    if version is not None and entry.get("version") != version:
        raise _fail(
            "version mismatch for %s: requested %r, available %r"
            % (name, version, entry.get("version"))
        )
    return _record(name, entry)


# --------------------------------------------------------------------------- gamal.toml
def _quote(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("%s must be a non-empty string" % field)
    # gamal2._read_toml strips inline comments and splits inline tables on
    # commas without honouring quotes, so these characters would corrupt it.
    if any(ch in value for ch in ('"', "\n", "\r", ",", "#", "{", "}")):
        raise _fail("%s contains an unsupported character" % field)
    return '"%s"' % value


def _format_dependency(name: str, entry: dict) -> str:
    if not _safe_name(name):
        raise _fail("invalid package name %r (no '..', '/', '\\' or whitespace)" % (name,))
    if not isinstance(entry, dict):
        raise _fail("dependency %r must be an object" % (name,))
    parts = ["git = %s" % _quote(entry.get("git"), "git")]
    rev = entry.get("rev")
    if rev:  # absent / None / "" means: do not pin a revision
        parts.append("rev = %s" % _quote(rev, "rev"))
    return "%s = { %s }" % (name, ", ".join(parts))


def _section_name(line: str) -> str | None:
    text = line.split("#", 1)[0].strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    return None


def _upsert_dependency(text: str, name: str, new_line: str) -> str:
    lines = text.splitlines()
    dep_start = next(
        (i for i, line in enumerate(lines) if _section_name(line) == "dependencies"),
        None,
    )
    if dep_start is None:
        out = text
        if out and not out.endswith("\n"):
            out += "\n"
        if out.strip():
            out += "\n"
        return out + "[dependencies]\n" + new_line + "\n"

    end = len(lines)
    for i in range(dep_start + 1, len(lines)):
        if _section_name(lines[i]) is not None:
            end = i
            break

    key_re = re.compile(r"^\s*" + re.escape(name) + r"\s*=")
    matches = [i for i in range(dep_start + 1, end) if key_re.match(lines[i])]
    if len(matches) == 1 and lines[matches[0]].strip() == new_line.strip():
        return text  # already exactly what we would write

    out: list[str] = []
    inserted = False
    for i, line in enumerate(lines):
        if dep_start < i < end and key_re.match(line):
            if not inserted:
                out.append(new_line)
                inserted = True
            continue  # drop duplicate occurrences
        out.append(line)

    if not inserted:
        insert_at = dep_start + 1
        for i in range(dep_start + 1, end):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("#"):
                insert_at = i + 1
        out.insert(insert_at, new_line)

    return "\n".join(out) + "\n"


def add_dependency(root: pathlib.Path, name: str, entry: dict) -> bool:
    """Add or update ``name`` in ``root/gamal.toml`` ``[dependencies]``.

    Returns ``True`` when the dependency is present after the call.  The file
    is only rewritten when its content actually changes; missing files are
    created with a minimal ``[package]`` + ``[dependencies]`` skeleton.
    """
    root = pathlib.Path(root)
    new_line = _format_dependency(name, entry)
    cfg = root / "gamal.toml"

    if not cfg.exists():
        root.mkdir(parents=True, exist_ok=True)
        pkg_name = root.name if _safe_name(root.name) else "app"
        cfg.write_text(
            '[package]\nname = "%s"\nversion = "0.1.0"\n\n'
            "[dependencies]\n%s\n" % (pkg_name, new_line),
            encoding="utf-8",
        )
        return True

    original = cfg.read_text(encoding="utf-8")
    updated = _upsert_dependency(original, name, new_line)
    if updated != original:
        cfg.write_text(updated, encoding="utf-8")
    return True
