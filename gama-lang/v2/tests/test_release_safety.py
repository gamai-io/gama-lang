import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

V2 = pathlib.Path(__file__).resolve().parents[1]
if str(V2) not in sys.path:
    sys.path.insert(0, str(V2))

import release  # noqa: E402


class CleanDirSafetyTests(unittest.TestCase):
    def test_unknown_file_without_force_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "dist"
            out.mkdir()
            keep = out / "keep.txt"
            keep.write_text("keep", encoding="utf-8")

            with self.assertRaises(release.ReleaseError):
                release._clean_dir(out)

            self.assertTrue(keep.is_file())
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")

    def test_dangerous_locations_are_refused(self):
        targets = [
            pathlib.Path("/"),
            pathlib.Path.home(),
            release.PROJECT_ROOT,
            release.PROJECT_ROOT.parent,          # ancestor of the source tree
            release.PROJECT_ROOT / "some-out",    # descendant of the source tree
        ]
        for target in targets:
            with self.subTest(target=str(target)):
                with self.assertRaises(release.ReleaseError):
                    release._clean_dir(target)

    def test_git_path_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / ".git" / "dist"
            with self.assertRaises(release.ReleaseError):
                release._clean_dir(out)
            self.assertFalse(out.exists())

    def test_force_removes_unknown_entries(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "dist"
            out.mkdir()
            (out / "keep.txt").write_text("keep", encoding="utf-8")
            (out / "old.whl").write_text("old", encoding="utf-8")

            release._clean_dir(out, force=True)

            self.assertTrue(out.is_dir())
            self.assertEqual(list(out.iterdir()), [])

    def test_known_artifacts_are_replaced_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "dist"
            out.mkdir()
            for name in ("old.whl", "old.tar.gz", "SHA256SUMS", "RELEASE.json"):
                (out / name).write_text("old", encoding="utf-8")

            release._clean_dir(out)

            self.assertEqual(list(out.iterdir()), [])


class VerifyPathSafetyTests(unittest.TestCase):
    def _write_manifest(self, out, name):
        (out / "SHA256SUMS").write_text("", encoding="utf-8")
        (out / "RELEASE.json").write_text(
            json.dumps({
                "version": "0.0.0",
                "files": [{"name": name, "sha256": "0" * 64, "size": 1}],
            }),
            encoding="utf-8",
        )

    def test_relative_traversal_is_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            self._write_manifest(out, "../../etc/passwd")

            with mock.patch.object(
                release, "_sha256", side_effect=AssertionError("must not read outside out_dir")
            ):
                result = release.verify(out)

            self.assertFalse(result["ok"])
            self.assertTrue(
                any("escapes out_dir" in error for error in result["errors"]),
                result["errors"],
            )

    def test_absolute_path_is_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            self._write_manifest(out, "/etc/passwd")

            with mock.patch.object(
                release, "_sha256", side_effect=AssertionError("must not read outside out_dir")
            ):
                result = release.verify(out)

            self.assertFalse(result["ok"])
            self.assertTrue(
                any("escapes out_dir" in error for error in result["errors"]),
                result["errors"],
            )


class BuildForceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = release.subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=60,
        )
        if probe.returncode != 0:
            raise unittest.SkipTest("pip is not available: %s" % probe.stderr.strip())
        try:
            import setuptools  # noqa: F401
            import wheel  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("build backend unavailable: %s" % exc)

    def test_build_force_replaces_known_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "dist"
            out.mkdir()
            for name in ("old.whl", "old.tar.gz", "SHA256SUMS", "RELEASE.json"):
                (out / name).write_text("old", encoding="utf-8")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = release.main(["build", "--force", "--out", str(out)])

            self.assertEqual(rc, 0, buf.getvalue())
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"], payload)
            self.assertFalse((out / "old.whl").exists())
            self.assertTrue((out / payload["wheel"]).is_file())
            self.assertTrue(release.verify(out)["ok"])


if __name__ == "__main__":
    unittest.main()
