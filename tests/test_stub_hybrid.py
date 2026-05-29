from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from cloud_vfs.project import project_root
from cloud_vfs.cli import cmd_offload
from cloud_vfs.storage.manifest import load_manifest
from cloud_vfs.storage.paths import is_real_local, stub_file_for
from cloud_vfs.storage.stub import (
    CVFS_MARKER,
    STUB_TYPE_BLOB,
    is_ref,
    migrate_legacy_file_sidecar,
    read_stub,
    remove_stub,
    stub_placement,
    write_inline_ref,
    write_stub,
)


class HybridStubTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        (self.root / ".cloud-vfs").mkdir()
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

    def test_inline_ref_after_file_offload(self) -> None:
        rel = "data/embeddings.npy"
        target = self.root / rel
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\x00" * 128)

        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})

        self.assertTrue(is_ref(rel))
        self.assertFalse(is_real_local(rel))
        self.assertEqual(stub_placement(rel), "inline")
        stub = read_stub(rel)
        assert stub is not None
        self.assertEqual(stub["cvfs"], CVFS_MARKER)
        self.assertEqual(stub["type"], STUB_TYPE_BLOB)
        self.assertEqual(stub["placement"], "inline")
        self.assertEqual(stub["blob"], rel)

    def test_dir_sidecar_unchanged(self) -> None:
        rel = "data/generated/run42"
        sidecar = write_stub(
            rel,
            {
                "archive": "local_archive",
                "blob_prefix": "data/generated/run42/",
                "manifest_id": "run42",
            },
        )
        self.assertEqual(sidecar.name, ".cloudstub")
        self.assertFalse(is_ref(rel))
        self.assertFalse(is_real_local(rel))
        self.assertEqual(stub_placement(rel), "sidecar")

    def test_real_local_file(self) -> None:
        rel = "data/local.bin"
        target = self.root / rel
        target.parent.mkdir(parents=True)
        target.write_bytes(b"real-data")
        self.assertTrue(is_real_local(rel))
        self.assertFalse(is_ref(rel))

    def test_legacy_sidecar_migration(self) -> None:
        rel = "data/legacy.npy"
        sidecar = stub_file_for(rel)
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            json.dumps(
                {
                    "type": STUB_TYPE_BLOB,
                    "version": 1,
                    "local": rel,
                    "blob": rel,
                    "archive": "local_archive",
                }
            )
            + "\n"
        )
        self.assertIsNotNone(read_stub(rel))
        migrated = migrate_legacy_file_sidecar(rel)
        self.assertIsNotNone(migrated)
        self.assertEqual(migrated.resolve(), (self.root / rel).resolve())
        self.assertFalse(sidecar.exists())
        self.assertTrue(is_ref(rel))

    def test_remove_inline_ref(self) -> None:
        rel = "data/tmp.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        remove_stub(rel)
        self.assertFalse((self.root / rel).exists())

    def test_binary_npy_is_real_local_not_ref(self) -> None:
        """Regression: issue #1 — must not read .npy as UTF-8 stub JSON."""
        rel = "data/weights.npy"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x93NUMPY\x01\x00" + bytes(range(256)))

        self.assertFalse(is_ref(rel))
        self.assertTrue(is_real_local(rel))

    def test_offload_binary_npy_no_unicode_error(self) -> None:
        from unittest.mock import patch

        rel = "data/model.npy"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x93NUMPY\x00" + b"\x00" * 200)
        (self.root / ".cloud-vfs" / "config.env").write_text(
            "LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=test\n"
        )
        (self.root / ".cloud-vfs" / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "local_archive": {"provider": "aws", "bucket": "test"},
                    "entries": [],
                }
            )
            + "\n"
        )
        (self.root / ".cloud-vfs" / "inventory-policy.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "min_size_bytes": 1,
                    "include_prefixes": ["data/"],
                    "exclude_prefixes": [],
                }
            )
            + "\n"
        )

        with patch("cloud_vfs.cli.upload_path", return_value=rel):
            rc = cmd_offload([rel], dry_run=True, archive_override=None)

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
