"""Tests for the safe ledger inspect/truncate tool (gama_ledger).

The tool must be read-only unless truncation is explicitly confirmed, must
never alter hash/HMAC semantics, and must only ever cut on a record boundary.
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

V2_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

import gamal2 as G  # noqa: E402
import gama_ledger as L  # noqa: E402


def _record(name, value, prev):
    raw = G._proof_raw(name, value)
    payload = G._proof_payload(prev, raw)
    sha = hashlib.sha256(payload.encode()).hexdigest()
    return {
        "name": name,
        "value": value,
        "sha256": sha,
        "prev": prev,
        "id": sha[:16],
        "ts": 0,
    }


def _chain(values):
    records = []
    prev = "GENESIS"
    for name, value in values:
        rec = _record(name, value, prev)
        records.append(rec)
        prev = rec["sha256"]
    return records


def _write(path, records):
    lines = [json.dumps(r, ensure_ascii=False, separators=(",", ":"))
             for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.read_bytes()


def _append_raw(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


class LedgerVerifyTests(unittest.TestCase):
    def test_valid_ledger_reports_ok_and_offsets(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            data = _write(path, _chain([("a", 1), ("b", 2), ("c", 3)]))
            report = L.verify_ledger_file(path)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["records"], 3)
            self.assertEqual(report["valid_records"], 3)
            self.assertIsNone(report["reason"])
            self.assertEqual(report["reasons"], [])
            self.assertEqual(report["last_valid_line"], 3)
            self.assertEqual(report["last_valid_offset"], len(data))
            self.assertEqual(report["size"], len(data))

    def test_malformed_line_is_reported_with_recoverable_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            records = _chain([("a", 1), ("b", 2)])
            data = _write(path, records)
            prefix = data
            _append_raw(path, "{not json}\n")
            report = L.verify_ledger_file(path)
            self.assertFalse(report["ok"])
            self.assertEqual(report["reason"], "malformed_line")
            self.assertIn("malformed_line", report["reasons"])
            self.assertEqual(report["records"], 2)
            self.assertEqual(report["valid_records"], 2)
            self.assertEqual(report["last_valid_line"], 2)
            self.assertEqual(report["last_valid_offset"], len(prefix))

    def test_forked_chain_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            first = _record("alpha", {"a": 1}, "GENESIS")
            second = _record("beta", {"b": 2}, "GENESIS")  # same predecessor
            data = _write(path, [first, second])
            first_line_len = len(
                (json.dumps(first, ensure_ascii=False,
                            separators=(",", ":")) + "\n").encode()
            )
            report = L.verify_ledger_file(path)
            self.assertFalse(report["ok"])
            self.assertEqual(report["reason"], "forked_chain")
            self.assertIn("forked_chain", report["reasons"])
            self.assertEqual(report["last_valid_line"], 1)
            self.assertEqual(report["last_valid_offset"], first_line_len)
            self.assertEqual(len(data), first_line_len + len(
                (json.dumps(second, ensure_ascii=False,
                            separators=(",", ":")) + "\n").encode()
            ))

    def test_non_string_prev_is_broken_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            rec = _record("a", 1, "GENESIS")
            rec["prev"] = ["not", "a", "string"]
            _write(path, [rec])
            report = L.verify_ledger_file(path)
            self.assertFalse(report["ok"])
            self.assertEqual(report["reason"], "broken_chain")
            self.assertEqual(report["last_valid_offset"], 0)

    def test_valid_ledger_without_trailing_newline_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            records = _chain([("a", 1), ("b", 2)])
            text = "\n".join(json.dumps(r, ensure_ascii=False,
                                        separators=(",", ":")) for r in records)
            path.write_text(text, encoding="utf-8")
            report = L.verify_ledger_file(path)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["records"], 2)
            self.assertEqual(report["last_valid_offset"], len(text.encode()))

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            report = L.verify_ledger_file(pathlib.Path(td) / "nope.jsonl")
            self.assertFalse(report["exists"])
            self.assertFalse(report["ok"])
            self.assertEqual(report["reason"], "missing_file")

    def test_directory_is_reported_not_a_file(self):
        with tempfile.TemporaryDirectory() as td:
            report = L.verify_ledger_file(pathlib.Path(td))
            self.assertTrue(report["exists"])
            self.assertFalse(report["ok"])
            self.assertEqual(report["reason"], "not_a_file")


class LedgerTruncateTests(unittest.TestCase):
    def _broken(self, td):
        path = pathlib.Path(td) / "proofs.jsonl"
        _write(path, _chain([("a", 1), ("b", 2)]))
        _append_raw(path, "{broken\n")
        return path, path.read_bytes()

    def test_truncate_refuses_without_confirmation_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            path, original = self._broken(td)
            report = L.truncate_ledger_to_last_valid(path, confirm=False)
            self.assertFalse(report["changed"])
            self.assertTrue(report["confirm_required"])
            self.assertNotIn("backup", report)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(
                [p.name for p in pathlib.Path(td).iterdir()], [path.name]
            )

    def test_truncate_requires_explicit_true(self):
        with tempfile.TemporaryDirectory() as td:
            path, original = self._broken(td)
            # Default argument must refuse too.
            report = L.truncate_ledger_to_last_valid(path)
            self.assertFalse(report["changed"])
            self.assertTrue(report["confirm_required"])
            self.assertEqual(path.read_bytes(), original)

    def test_truncate_creates_backup_and_keeps_valid_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            path, original = self._broken(td)
            before = L.verify_ledger_file(path)
            report = L.truncate_ledger_to_last_valid(path, confirm=True)
            self.assertTrue(report["changed"], report)
            self.assertTrue(report["ok"], report)
            backup = pathlib.Path(report["backup"])
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_bytes(), original)
            expected = original[:before["last_valid_offset"]]
            self.assertEqual(path.read_bytes(), expected)
            after = L.verify_ledger_file(path)
            self.assertTrue(after["ok"], after)
            self.assertEqual(after["records"], before["valid_records"])
            self.assertEqual(report["truncated_bytes"],
                             len(original) - len(expected))

    def test_valid_ledger_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            original = _write(path, _chain([("a", 1)]))
            report = L.truncate_ledger_to_last_valid(path, confirm=True)
            self.assertFalse(report["changed"])
            self.assertTrue(report["ok"])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(
                [p.name for p in pathlib.Path(td).iterdir()], [path.name]
            )

    def test_forked_ledger_truncates_to_branch_point(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            first = _record("alpha", {"a": 1}, "GENESIS")
            second = _record("beta", {"b": 2}, "GENESIS")
            _write(path, [first, second])
            report = L.truncate_ledger_to_last_valid(path, confirm=True)
            self.assertTrue(report["changed"])
            self.assertTrue(report["ok"], report)
            after = L.verify_ledger_file(path)
            self.assertTrue(after["ok"], after)
            self.assertEqual(after["records"], 1)


class LedgerCliTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "gama_ledger.py", *args],
            cwd=str(V2_ROOT), capture_output=True, text=True, timeout=60,
        )

    def test_cli_verify_exit_codes_and_messages(self):
        with tempfile.TemporaryDirectory() as td:
            good = pathlib.Path(td) / "good.jsonl"
            _write(good, _chain([("a", 1)]))
            proc = self._run("verify", str(good))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("OK", proc.stdout)

            bad = pathlib.Path(td) / "bad.jsonl"
            _write(bad, _chain([("a", 1)]))
            _append_raw(bad, "nope\n")
            proc = self._run("verify", str(bad))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("malformed_line", proc.stderr)

            proc = self._run("verify", str(pathlib.Path(td) / "missing.jsonl"))
            self.assertEqual(proc.returncode, 2)

    def test_cli_truncate_requires_yes_and_then_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "proofs.jsonl"
            _write(path, _chain([("a", 1)]))
            _append_raw(path, "{oops\n")
            original = path.read_bytes()

            proc = self._run("truncate", str(path))
            self.assertEqual(proc.returncode, 3, proc.stderr)
            self.assertIn("REFUSED", proc.stderr)
            self.assertEqual(path.read_bytes(), original)

            proc = self._run("truncate", str(path), "--yes")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("TRUNCATED", proc.stdout)
            self.assertTrue(L.verify_ledger_file(path)["ok"])
            backups = sorted(pathlib.Path(td).glob("*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
