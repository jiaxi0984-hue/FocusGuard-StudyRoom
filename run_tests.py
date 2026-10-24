from pathlib import Path
import sys
import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).parent / "_deps"))
scratch = Path(__file__).parent / "build" / "test-tmp"
scratch.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(scratch)
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
