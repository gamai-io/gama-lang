"""Safe inspection and explicit repair of a persistent proof ledger.

The proof ledger is a JSONL file where every record holds ``name``, ``value``,
``sha256``, ``prev`` (and optionally ``intent``/``lease``/``sig``).  A healthy
file is exactly one hash chain: the first record starts at ``GENESIS`` and each
later record's ``prev`` equals the preceding record's ``sha256``.

This module is intentionally *additive*: it does not change or replace any
hashing, HMAC or ledger behaviour in :mod:`gamal2`.  It only reads the same
bytes that ``gamal2`` writes and reports whether they form one valid chain.

Safety model
------------
* :func:`verify_ledger_file` is read-only and never changes the file.
* :func:`truncate_ledger_to_last_valid` refuses unless ``confirm=True``.  When
  confirmed it first writes a timestamped ``.bak`` copy, then truncates on a
  record boundary to the longest valid prefix, and re-verifies the result.
  It never "repairs" silently and never rewrites earlier bytes.

Limitations
-----------
Verification mirrors ``gamal2._ledger_scan``: it checks JSON parseability and
``prev`` chain linkage (including forks).  It deliberately does **not**
recompute each record's ``sha256`` or HMAC signature, because that would
second-guess :mod:`gamal2`'s own append rules and could reject a ledger that
``gamal2`` still accepts.  Use ``gamal2``'s ``verify``/``prove`` for
per-record cryptographic checks; this tool only inspects chain structure and
truncates a structurally broken tail.

CLI (separate from the default GAMA-L CLI)::

    python gama_ledger.py verify <path>
    python gama_ledger.py truncate <path> [--yes]

Exit codes
----------
``0``  success / ledger already valid
``1``  ledger is invalid (verify) or post-truncation verification failed
``2``  usage error or missing file
``3``  truncation refused because ``--yes`` was not given
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time

__all__ = [
    "verify_ledger_file",
    "truncate_ledger_to_last_valid",
    "main",
]

# Stable precedence for the single ``reason`` field.  Every detected code is
# still reported in ``reasons``; this only picks the headline one.
_REASON_PRECEDENCE = (
    "malformed_line",
    "forked_chain",
    "broken_chain",
    "missing_sha",
)

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_NEEDS_CONFIRM = 3

_FILE_ERRORS = ("missing_file", "not_a_file", "unreadable")


def _add(reasons, code):
    if code not in reasons:
        reasons.append(code)


def _scan(data: bytes, path: str) -> dict:
    """Scan ledger bytes into an integrity report.

    Mirrors ``gamal2._ledger_scan`` for the integrity codes but additionally
    tracks byte offsets and the longest *leading* valid prefix so callers can
    truncate precisely on a record boundary.
    """
    reasons = []
    record_shas = []          # sha of every parsed record, in file order
    valid_records = 0
    last_valid_offset = 0
    last_valid_line = 0
    seen_sha = set()
    prev_uses = {}
    prefix_broken = False
    offset = 0
    line_count = 0

    for lineno, raw_line in enumerate(data.splitlines(keepends=True), 1):
        line_count = lineno
        end = offset + len(raw_line)
        text = raw_line.strip()
        if not text:
            offset = end
            continue

        try:
            rec = json.loads(text.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            rec = None
        if not isinstance(rec, dict):
            _add(reasons, "malformed_line")
            prefix_broken = True
            offset = end
            continue

        index = len(record_shas)
        sha = str(rec.get("sha256", "") or "")
        prev = rec.get("prev")
        expected = "GENESIS" if index == 0 else str(record_shas[index - 1] or "")

        record_reason = None
        if not sha:
            _add(reasons, "missing_sha")
        if prev != expected:
            if isinstance(prev, str) and (
                    prev in seen_sha or prev_uses.get(prev, 0) > 0):
                _add(reasons, "forked_chain")
                record_reason = "forked_chain"
            else:
                _add(reasons, "broken_chain")
                record_reason = "broken_chain"

        record_bad = record_reason is not None or not sha
        if not prefix_broken and not record_bad:
            valid_records += 1
            last_valid_offset = end
            last_valid_line = lineno
        elif not prefix_broken:
            prefix_broken = True

        record_shas.append(sha)
        if isinstance(prev, str):
            prev_uses[prev] = prev_uses.get(prev, 0) + 1
        if sha:
            seen_sha.add(sha)
        offset = end

    ok = not reasons
    if ok:
        last_valid_offset = len(data)

    reason = next((code for code in _REASON_PRECEDENCE if code in reasons), None)
    return {
        "path": path,
        "exists": True,
        "ok": ok,
        "reason": reason,
        "reasons": reasons,
        "records": len(record_shas),
        "valid_records": valid_records,
        "last_valid_offset": last_valid_offset,
        "last_valid_line": last_valid_line,
        "lines": line_count,
        "size": len(data),
    }


def verify_ledger_file(path) -> dict:
    """Return an integrity report for the proof-ledger JSONL file at ``path``.

    The file is never modified.  ``ok`` is true only when the whole file is one
    valid hash chain with no malformed lines.  ``reason`` is one of
    ``forked_chain``, ``broken_chain``, ``malformed_line`` (or ``missing_sha`` /
    ``missing_file`` for the edge cases).
    """
    p = pathlib.Path(path)
    if not p.exists():
        return _file_error(p, "missing_file", exists=False)
    if not p.is_file():
        return _file_error(p, "not_a_file", exists=True)
    try:
        data = p.read_bytes()
    except OSError:
        return _file_error(p, "unreadable", exists=True)
    return _scan(data, str(p))


def _file_error(path: pathlib.Path, reason: str, exists: bool) -> dict:
    return {
        "path": str(path),
        "exists": exists,
        "ok": False,
        "reason": reason,
        "reasons": [reason],
        "records": 0,
        "valid_records": 0,
        "last_valid_offset": 0,
        "last_valid_line": 0,
        "lines": 0,
        "size": 0,
    }


def _timestamp():
    ticks = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(ticks))
    millis = int(ticks * 1000) % 1000
    return "%s%03dZ" % (stamp, millis)


def _backup_path(path: pathlib.Path):
    base = path.name + "." + _timestamp()
    candidate = path.with_name(base + ".bak")
    n = 1
    while candidate.exists():
        candidate = path.with_name("%s-%d.bak" % (base, n))
        n += 1
    return candidate


def truncate_ledger_to_last_valid(path, confirm: bool = False) -> dict:
    """Truncate ``path`` to its longest valid leading record prefix.

    Refuses (and returns ``confirm_required=True``) unless ``confirm`` is true.
    When confirmed, a timestamped ``.bak`` copy is created first, the file is
    truncated on a record boundary, and the shortened file is verified again.
    A valid ledger is left untouched.  This function never edits record bytes
    and never attempts to reconstruct or re-hash a damaged record.
    """
    p = pathlib.Path(path)
    before = verify_ledger_file(p)
    result = dict(before)
    result["confirm_required"] = False
    result["changed"] = False

    if before.get("reason") in _FILE_ERRORS:
        result["message"] = "cannot read ledger: %s" % before["reason"]
        return result

    if before["ok"]:
        result["message"] = "ledger is already a single valid chain; nothing to truncate"
        return result

    if not confirm:
        result["confirm_required"] = True
        result["message"] = (
            "refusing to modify the ledger without confirm=True (CLI: --yes)"
        )
        return result

    original = p.read_bytes()
    keep = before["last_valid_offset"]
    backup = _backup_path(p)
    shutil.copy2(p, backup)

    with open(p, "r+b") as fh:
        fh.truncate(keep)
        fh.flush()
        os.fsync(fh.fileno())

    after = verify_ledger_file(p)
    result.update({
        "ok": after["ok"],
        "changed": True,
        "backup": str(backup),
        "truncated_bytes": len(original) - keep,
        "kept_bytes": keep,
        "before": before,
        "after": after,
        "message": (
            "truncated to last valid record boundary"
            if after["ok"] else "post-truncation verification failed"
        ),
    })
    if not after["ok"]:
        # Should be unreachable: we only ever cut before a bad record.  Report
        # the post-state rather than claiming success.
        result["reason"] = after["reason"]
        result["reasons"] = after["reasons"]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gama_ledger.py",
        description="Verify or explicitly truncate a GAMA-L proof ledger.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="report ledger integrity without changing it")
    p_verify.add_argument("path", help="path to the JSONL proof ledger")

    p_truncate = sub.add_parser(
        "truncate", help="truncate to the last valid record boundary (requires --yes)"
    )
    p_truncate.add_argument("path", help="path to the JSONL proof ledger")
    p_truncate.add_argument(
        "--yes", action="store_true",
        help="confirm the destructive truncation (a timestamped .bak is kept)",
    )

    args = parser.parse_args(argv)

    if args.command == "verify":
        report = verify_ledger_file(args.path)
        if report.get("reason") in _FILE_ERRORS:
            print("ERROR: cannot read ledger (%s): %s"
                  % (report["reason"], args.path), file=sys.stderr)
            return EXIT_USAGE
        if report["ok"]:
            print("OK: %d record(s), single valid chain" % report["records"])
            return EXIT_OK
        print(
            "INVALID: %s; %d record(s), valid prefix ends at line %d (byte %d)"
            % (", ".join(report["reasons"]) or "unknown", report["records"],
               report["last_valid_line"], report["last_valid_offset"]),
            file=sys.stderr,
        )
        return EXIT_INVALID

    report = truncate_ledger_to_last_valid(args.path, confirm=args.yes)
    if report.get("reason") in _FILE_ERRORS:
        print("ERROR: cannot read ledger (%s): %s"
              % (report["reason"], args.path), file=sys.stderr)
        return EXIT_USAGE
    if report["confirm_required"]:
        print(
            "REFUSED: %s (pass --yes to truncate; a .bak copy is created)"
            % report["message"],
            file=sys.stderr,
        )
        return EXIT_NEEDS_CONFIRM
    if not report["changed"]:
        print("OK: %s" % report["message"])
        return EXIT_OK
    if report["ok"]:
        print(
            "TRUNCATED: kept %d byte(s) through line %d; %d byte(s) removed; backup: %s"
            % (report["kept_bytes"], report["before"]["last_valid_line"],
               report["truncated_bytes"], report["backup"])
        )
        return EXIT_OK
    print(
        "ERROR: post-truncation verification failed (%s); backup: %s"
        % (report.get("reason"), report.get("backup")),
        file=sys.stderr,
    )
    return EXIT_INVALID


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess tests
    sys.exit(main())
