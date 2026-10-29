# -*- coding: utf-8 -*-
"""Tests for the doctor health-check module."""
import contextlib
import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gama_doctor


class DoctorTests(unittest.TestCase):
    def test_collect_is_ok(self):
        result = gama_doctor.collect()
        self.assertTrue(result["ok"], result)
        self.assertEqual(set(result), {"ok", "python", "root", "checks"})
        names = [c["name"] for c in result["checks"]]
        for expected in ("python", "examples", "formatter", "sql", "package"):
            self.assertIn(expected, names)
        for check in result["checks"]:
            self.assertEqual(set(check), {"name", "ok", "detail"})
            self.assertTrue(check["ok"], check)

    def test_main_human_exit_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gama_doctor.main([])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("doctor: OK", buf.getvalue())

    def test_main_json_exit_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gama_doctor.main(["--json"])
        self.assertEqual(rc, 0, buf.getvalue())
        data = json.loads(buf.getvalue())
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["python"], "%d.%d.%d" % sys.version_info[:3])


if __name__ == "__main__":
    unittest.main()
