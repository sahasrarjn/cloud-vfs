from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_offload, cmd_reconcile
from cloud_vfs.project import project_root
from cloud_vfs.storage.inventory import detect_drift, load_policy, upsert_row
from cloud_vfs.storage.offload_progress import new_offload_progress, save_offload_progress
from cloud_vfs.storage.paths import is_real_local
from cloud_vfs.storage.stub import is_ref, write_inline_ref


class _AwsProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        cfg = self.root / ".cloud-vfs"
        cfg.mkdir()
        (cfg / "index").mkdir()
        (cfg / "config.env").write_text("LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=test-bucket\n")
        (cfg / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "local_archive": {"provider": "aws", "bucket": "test-bucket"},
                    "entries": [],
                }
            )
            + "\n"
        )
        (cfg / "inventory-policy.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "index_dir": ".cloud-vfs/index",
                    "min_size_bytes": 1,
                    "include_prefixes": ["data/"],
                    "exclude_prefixes": [],
                }
            )
            + "\n"
        )
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

    def _make_cloud_only(self, rel: str, *, size: int = 1024, blob: str | None = None) -> None:
        blob = blob or rel
        upsert_row(
            rel.split("/")[0],
            rel,
            {
                "local": rel,
                "archive": "local_archive",
                "state": "cloud-only",
                "size": size,
                "blob": blob,
                "sha256": "abc",
            },
            load_policy(),
        )
        write_inline_ref(rel, {"blob": blob, "archive": "local_archive"})


class ReconcileBlobVerificationTests(_AwsProjectTestCase):
    """Issue #38 — reconcile must flag inventory entries whose remote blob is gone."""

    def test_reconcile_flags_missing_remote_blob_by_default(self) -> None:
        rel = "data/model.bin"
        self._make_cloud_only(rel)

        # Remote blob is absent (the silent-upload data-loss scenario).
        with patch("cloud_vfs.storage.inventory.blob_content_length", return_value=None):
            rc = cmd_reconcile(
                as_json=False,
                from_blob=False,
                fix_index=False,
                repair_stubs_flag=False,
                orphan_blobs=False,
                prefix=None,
            )

        self.assertEqual(rc, 1)

    def test_detect_drift_default_verifies_blobs(self) -> None:
        rel = "data/model.bin"
        self._make_cloud_only(rel)

        with patch("cloud_vfs.storage.inventory.blob_content_length", return_value=None):
            issues = detect_drift(verify_blobs=True)
        types = {i["type"] for i in issues}
        self.assertIn("ghost-index", types)

    def test_detect_drift_flags_size_mismatch(self) -> None:
        rel = "data/model.bin"
        self._make_cloud_only(rel, size=1024)

        # Remote object exists but is truncated.
        with patch("cloud_vfs.storage.inventory.blob_content_length", return_value=10):
            issues = detect_drift(verify_blobs=True)
        types = {i["type"] for i in issues}
        self.assertIn("blob-size-mismatch", types)

    def test_detect_drift_no_drift_when_blob_present_and_sized(self) -> None:
        rel = "data/model.bin"
        self._make_cloud_only(rel, size=1024)

        with patch("cloud_vfs.storage.inventory.blob_content_length", return_value=1024):
            issues = detect_drift(verify_blobs=True)
        self.assertEqual(issues, [])


class VerifyBeforeDeleteTests(_AwsProjectTestCase):
    """Issue #38 — offload must confirm the remote object before deleting local,
    even when a resumed run skips the upload step."""

    def test_resume_keeps_local_when_remote_absent(self) -> None:
        rel = "data/resume-me.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"resume-payload" * 100
        path.write_bytes(payload)

        # A prior run recorded a 'completed' upload, so this run skips upload.
        progress = new_offload_progress(
            rel,
            archive="local_archive",
            delete_local=True,
            precomputed={rel: "abc123"},
        )
        progress["uploaded"] = True
        save_offload_progress(progress)

        # ...but the object is not actually in the bucket.
        with patch("cloud_vfs.cli.blob_content_length", return_value=None):
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 1)
        self.assertTrue(is_real_local(rel))
        self.assertFalse(is_ref(rel))
        self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
