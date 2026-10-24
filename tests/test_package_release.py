"""发布脚本隔离测试：不启动 EXE、不访问真实配置、不执行系统动作。"""
from datetime import date
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
import zipfile
from PySide6.QtGui import QImage
from tools.release import package_release as packaging


class PackageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        names = ["dist/FocusGuard.exe", "assets/app.ico", "focusguard/__init__.py", "tests/test_example.py", "tools/__init__.py",
                 *packaging.DOCS, *packaging.SOURCE_FILES]
        for name in names:
            self.write(name, b"fixture")
        for name in packaging.SCREENSHOTS:
            # 使用真实可解码的微型测试 PNG，并满足发布尺寸下限。
            path = self.root / f"screenshots-{packaging.VERSION}" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            image = QImage(100, 100, QImage.Format_RGB32)
            image.fill(0xFFFFFF)
            self.assertTrue(image.save(str(path)))

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def build(self, stamp="20261023"):
        return packaging.build_package(self.root, stamp)

    def test_archive_manifest_sources_and_privacy(self):
        for name in ("config.json", "events.log", "build/private.log", "_deps/private.py",
                     "focusguard/__pycache__/private.pyc", "focusguard/__pycache__/private.py", "screenshots-1.2.0/private.png"):
            self.write(name, b"must-not-ship")
        result = self.build()
        archive = Path(result["zip"])
        self.assertIn("20261023", archive.name)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), result["sha256"])
        self.assertEqual(archive.with_suffix(".zip.sha256").read_text().split()[0], result["sha256"])
        with zipfile.ZipFile(archive) as handle:
            self.assertIsNone(handle.testzip())
            names = handle.namelist()
            base = Path(result["release"]).name + "/"
            manifest = json.loads(handle.read(base + "SHA256SUMS.json"))
            self.assertEqual(set(names), {base + n for n in manifest["files"]} | {base + "SHA256SUMS.json"})
            for name, checksum in manifest["files"].items():
                self.assertEqual(hashlib.sha256(handle.read(base + name)).hexdigest(), checksum)
            self.assertIn(base + "源码/tests/test_example.py", names)
            self.assertIn(base + "源码/assets/app.ico", names)
            self.assertFalse(any("private" in name or name.endswith("/config.json") or name.endswith("/events.log") for name in names))
            self.assertIn(b"--simulate", handle.read(base + "模拟模式.cmd"))

    def test_default_date_is_current_date(self):
        result = packaging.build_package(self.root)
        self.assertEqual(result["release_date"], date.today().strftime("%Y%m%d"))

    def test_missing_screenshot_fails_without_output(self):
        (self.root / f"screenshots-{packaging.VERSION}" / packaging.SCREENSHOTS[0]).unlink()
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / "outputs").exists())

    def test_invalid_screenshot_fails(self):
        self.write(f"screenshots-{packaging.VERSION}/01-home.png", b"not a PNG")
        with self.assertRaises(ValueError):
            self.build()

    def test_truncated_screenshot_fails(self):
        path = self.root / f"screenshots-{packaging.VERSION}/01-home.png"
        path.write_bytes(path.read_bytes()[:-12])
        with self.assertRaises(ValueError):
            self.build()

    def test_corrupt_screenshot_fails(self):
        path = self.root / f"screenshots-{packaging.VERSION}/01-home.png"
        data = bytearray(path.read_bytes())
        data[29] ^= 1
        path.write_bytes(data)
        with self.assertRaises(ValueError):
            self.build()

    def test_evidence_is_allowlisted(self):
        for filename in packaging.EVIDENCE_FILES:
            self.write(f"verification-{packaging.VERSION}/{filename}", b"safe fixture")
        self.write(f"verification-{packaging.VERSION}/private.log", b"private")
        result = self.build()
        with zipfile.ZipFile(result["zip"]) as handle:
            names = handle.namelist()
            self.assertFalse(any("private.log" in n for n in names))
            self.assertTrue(any(n.endswith("验证资料/automated-tests.log") for n in names))

    def test_missing_source_fails(self):
        (self.root / "main.py").unlink()
        with self.assertRaises(ValueError):
            self.build()

    def test_existing_release_is_not_merged(self):
        stale = self.write(f"outputs/FocusGuard-{packaging.VERSION}-Windows-x64/stale.log", b"preserve")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(stale.read_bytes(), b"preserve")

    def test_existing_archive_is_not_overwritten(self):
        old = self.write(f"outputs/FocusGuard-{packaging.VERSION}-Windows-x64-20261023.zip", b"preserve")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(old.read_bytes(), b"preserve")

    def test_invalid_dates_fail(self):
        for stamp in ("20260230", "2026102", "../../xx", "２０２６１０２３"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.build(stamp)

    def test_import_does_not_package(self):
        import importlib
        importlib.reload(packaging)
        self.assertFalse((self.root / "outputs").exists())

    def test_nested_functional_sources_are_packaged(self):
        nested = ("focusguard/monitoring/core.py", "focusguard/recognition/engine.py",
                  "focusguard/recognition/steam/discovery.py", "focusguard/ui/app.py",
                  "focusguard/windows/system.py", "tools/release/package_release.py",
                  "tools/verification/verify_live_simulation.py")
        for name in nested:
            self.write(name, b"nested source fixture")
        result = self.build()
        with zipfile.ZipFile(result["zip"]) as handle:
            base = Path(result["release"]).name + "/源码/"
            for name in nested:
                self.assertIn(base + name, handle.namelist())

    def test_missing_tools_directory_fails_without_output(self):
        (self.root / "tools" / "__init__.py").unlink()
        (self.root / "tools").rmdir()
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / "outputs").exists())
