from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from cloud_vfs.project import project_root
from cloud_vfs.scan import cmd_scan, discover_large_local
from cloud_vfs.storage.inventory import load_policy


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        cfg = self.root / ".cloud-vfs"
        cfg.mkdir()
        (cfg / "index").mkdir()
        (cfg / "config.env").write_text("LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=b\n")
        (cfg / "manifest.json").write_text(
            json.dumps({"version": 3, "local_archive": {"provider": "aws"}, "entries": []}) + "\n"
        )
        (cfg / "inventory-policy.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "min_size_bytes": 100,
                    "include_prefixes": ["data/"],
                    "exclude_prefixes": [],
                }
            )
            + "\n"
        )
        big = self.root / "data" / "heavy.bin"
        big.parent.mkdir(parents=True)
        big.write_bytes(b"x" * 200)
        small = self.root / "data" / "tiny.bin"
        small.write_bytes(b"x")
        self._prev = os.environ.get("CLOUD_VFS_PROJECT_ROOT")
        os.environ["CLOUD_VFS_PROJECT_ROOT"] = str(self.root)
        project_root.cache_clear()

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("CLOUD_VFS_PROJECT_ROOT", None)
        else:
            os.environ["CLOUD_VFS_PROJECT_ROOT"] = self._prev
        project_root.cache_clear()
        self._tmpdir.cleanup()

    def test_discover_large_local(self) -> None:
        rows = discover_large_local(load_policy())
        paths = {r["path"] for r in rows}
        self.assertIn("data/heavy.bin", paths)
        self.assertNotIn("data/tiny.bin", paths)

    def test_scan_add_updates_manifest(self) -> None:
        self.assertEqual(cmd_scan(as_json=False, add=True, prefix=None), 0)
        manifest = json.loads((self.root / ".cloud-vfs" / "manifest.json").read_text())
        locals = [e["local"] for e in manifest.get("entries", [])]
        self.assertTrue(any("heavy.bin" in p or p == "data" for p in locals))


if __name__ == "__main__":
    unittest.main()
