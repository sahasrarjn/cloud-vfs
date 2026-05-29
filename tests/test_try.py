from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from cloud_vfs.try_demo import cmd_try


class TryDemoTests(unittest.TestCase):
    def test_try_creates_demo_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sandbox"
            self.assertEqual(cmd_try(dest, force=False), 0)
            self.assertTrue((dest / ".cloud-vfs" / "manifest.json").exists())
            self.assertTrue((dest / "scripts" / "create-sample.sh").exists())
            os.chmod(dest / "scripts" / "create-sample.sh", 0o755)
            self.assertEqual(cmd_try(dest, force=False), 0)


if __name__ == "__main__":
    unittest.main()
