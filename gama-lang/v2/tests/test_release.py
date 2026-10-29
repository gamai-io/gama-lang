import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
if str(V2) not in sys.path:
    sys.path.insert(0, str(V2))

import release  # noqa: E402


def _existing_junk():
    names = {p.name for p in V2.glob("build")}
    names |= {p.name for p in V2.glob("*.egg-info")}
    return names


class ReleaseTests(unittest.TestCase):
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

        cls._junk_before = _existing_junk()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = pathlib.Path(cls._tmp.name) / "dist"
        try:
            cls.result = release.build(cls.out)
        except release.ReleaseError as exc:
            message = str(exc)
            if "not available" in message or "timed out" in message:
                raise unittest.SkipTest(message)
            raise

    @classmethod
    def tearDownClass(cls):
        tmp = getattr(cls, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()
        for pattern in ("build", "*.egg-info"):
            for path in V2.glob(pattern):
                if path.name in cls._junk_before:
                    continue
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)

    def test_00_artifacts_exist(self):
        self.assertTrue(self.result["ok"], self.result)
        wheel = self.out / self.result["wheel"]
        sdist = self.out / self.result["sdist"]
        self.assertTrue(wheel.is_file(), "missing wheel")
        self.assertTrue(sdist.is_file(), "missing sdist")
        self.assertTrue(wheel.name.endswith(".whl"))
        self.assertTrue(sdist.name.endswith(".tar.gz"))
        self.assertTrue((self.out / "SHA256SUMS").is_file())
        self.assertTrue((self.out / "RELEASE.json").is_file())

    def test_01_manifest(self):
        manifest = json.loads((self.out / "RELEASE.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], release._version())
        self.assertTrue(manifest["python"])
        self.assertTrue(manifest["built_at"])
        names = {entry["name"] for entry in manifest["files"]}
        self.assertEqual(names, {self.result["wheel"], self.result["sdist"]})
        for entry in manifest["files"]:
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertGreater(entry["size"], 0)
        self.assertEqual(set(manifest["sha256"]), names)

    def test_02_sha256sums(self):
        lines = (self.out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        rows = {name: digest for digest, name in
                (line.split(None, 1) for line in lines if line.strip())}
        self.assertEqual(set(rows), {self.result["wheel"], self.result["sdist"]})
        for name, digest in rows.items():
            self.assertEqual(digest, release._sha256(self.out / name))

    def test_03_verify_ok(self):
        result = release.verify(self.out)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["errors"], [])

    def test_04_verify_detects_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            copy = pathlib.Path(td) / "dist"
            shutil.copytree(self.out, copy)
            target = copy / self.result["sdist"]
            target.write_bytes(target.read_bytes() + b"tampered")
            result = release.verify(copy)
            self.assertFalse(result["ok"])
            self.assertTrue(any("sha256 mismatch" in e for e in result["errors"]),
                            result["errors"])

    def test_05_verify_detects_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            copy = pathlib.Path(td) / "dist"
            shutil.copytree(self.out, copy)
            (copy / self.result["wheel"]).unlink()
            result = release.verify(copy)
            self.assertFalse(result["ok"])

    def test_06_cli(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "cli-dist"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(release.main(["build", "--out", str(out)]), 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertTrue((out / "RELEASE.json").is_file())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(release.main(["verify", "--out", str(out)]), 0)
            self.assertTrue(json.loads(buf.getvalue())["ok"])
            (out / "RELEASE.json").unlink()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(release.main(["verify", "--out", str(out)]), 1)
            self.assertFalse(json.loads(buf.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
