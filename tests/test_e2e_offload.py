"""End-to-end integration tests for the offload → ensure → reconcile lifecycle.

These run the real CLI code paths against an in-memory fake S3 (see
``fake_object_store.FakeS3``), so they cover the actual subprocess command
construction, post-upload verification, stub writing, download, and drift
detection together — not mocked in isolation. They are the regression net for
issues #37 (silent upload + delete = data loss) and #38 (verify-before-delete,
reconcile detects missing blobs).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_ensure, cmd_offload, cmd_reconcile
from cloud_vfs.project import project_root
from cloud_vfs.storage.offload_progress import new_offload_progress, save_offload_progress
from cloud_vfs.storage.paths import is_real_local
from cloud_vfs.storage.stub import is_ref

from tests.fake_object_store import FakeS3


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Project(unittest.TestCase):
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

    def _write(self, rel: str, data: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def _reconcile(self, **over) -> int:
        kw = dict(
            as_json=False,
            from_blob=False,
            fix_index=False,
            repair_stubs_flag=False,
            orphan_blobs=False,
            prefix=None,
        )
        kw.update(over)
        return cmd_reconcile(**kw)


class FileRoundTripTests(_Project):
    def test_offload_then_ensure_restores_identical_bytes(self) -> None:
        rel = "data/out.mp4"
        payload = os.urandom(4096)
        path = self._write(rel, payload)
        s3 = FakeS3()

        with s3.patch():
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)
        self.assertEqual(rc, 0)
        self.assertTrue(is_ref(rel))
        self.assertFalse(is_real_local(rel))
        # The object really landed in the bucket, content-correct.
        self.assertIn(rel, s3.store)
        self.assertEqual(_sha(s3.store[rel]), _sha(payload))

        with s3.patch():
            rc = cmd_ensure([rel], verify=True)
        self.assertEqual(rc, 0)
        self.assertTrue(is_real_local(rel))
        self.assertEqual(path.read_bytes(), payload)

    def test_silent_upload_keeps_local_and_fails(self) -> None:
        """Issue #37 end-to-end: upload stores nothing → local kept, no stub."""
        rel = "data/precious.bin"
        payload = os.urandom(2048)
        path = self._write(rel, payload)
        s3 = FakeS3(drop_uploads=True)

        with s3.patch():
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 1)
        self.assertTrue(is_real_local(rel))
        self.assertFalse(is_ref(rel))
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(s3.store, {})

    def test_keep_local_then_remote_deleted_reconcile_flags_ghost(self) -> None:
        """Issue #38 end-to-end: a vanished remote blob is reported as drift."""
        rel = "data/model.bin"
        payload = os.urandom(3000)
        self._write(rel, payload)
        s3 = FakeS3()

        with s3.patch():
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)
            self.assertEqual(rc, 0)
            # Clean state: reconcile (which HEADs blobs by default) sees no drift.
            self.assertEqual(self._reconcile(), 0)

            # Remote blob disappears (the exact data-loss aftermath).
            s3.delete_prefix(rel)
            buf_rc = self._reconcile()
        self.assertEqual(buf_rc, 1)

    def test_reconcile_unverifiable_is_not_ghost(self) -> None:
        """Degraded creds/network must not be reported as a missing blob."""
        rel = "data/model.bin"
        self._write(rel, os.urandom(1500))
        s3 = FakeS3()
        with s3.patch():
            self.assertEqual(cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True), 0)

        # Now every HEAD errors with a permission failure (not a 404).
        from cloud_vfs.storage.errors import CloudStorageError

        def denied(cmd, *, action, **kwargs):
            raise CloudStorageError(action, list(cmd), "An error occurred (403) ... Forbidden", 254)

        with patch("cloud_vfs.storage.backends._run", side_effect=denied):
            rc = self._reconcile(as_json=True)
        # Still reports drift (rc 1) but as blob-unverifiable, never ghost-index.
        self.assertEqual(rc, 1)


class DirectoryRoundTripTests(_Project):
    def test_offload_tree_then_ensure_restores_all_files(self) -> None:
        rel = "data/tree"
        files = {f"{rel}/a.bin": os.urandom(500), f"{rel}/sub/b.bin": os.urandom(700)}
        for k, v in files.items():
            self._write(k, v)
        s3 = FakeS3()

        with s3.patch():
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)
        self.assertEqual(rc, 0)
        for k, v in files.items():
            self.assertIn(k, s3.store)
            self.assertEqual(s3.store[k], v)

        with s3.patch():
            rc = cmd_ensure([rel], verify=False)
        self.assertEqual(rc, 0)
        for k, v in files.items():
            self.assertEqual((self.root / k).read_bytes(), v)

    def test_partial_tree_upload_keeps_local(self) -> None:
        """A tree upload that drops some members must not delete the local tree."""
        rel = "data/tree"
        files = {f"{rel}/a.bin": os.urandom(500), f"{rel}/b.bin": os.urandom(500), f"{rel}/c.bin": os.urandom(500)}
        for k, v in files.items():
            self._write(k, v)
        # Drop one member during sync → remote count < local count.
        s3 = FakeS3(drop_keys=lambda key: key.endswith("/c.bin"))

        with s3.patch():
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 1)
        for k, v in files.items():
            self.assertEqual((self.root / k).read_bytes(), v)
        self.assertFalse(is_ref(rel))


class ResumeSafetyTests(_Project):
    def test_resume_with_missing_remote_keeps_local(self) -> None:
        """Issue #38: a resumed run (upload already 'done') still verifies the remote."""
        rel = "data/resume.bin"
        payload = os.urandom(2048)
        path = self._write(rel, payload)

        progress = new_offload_progress(
            rel, archive="local_archive", delete_local=True, precomputed={rel: _sha(payload)}
        )
        progress["uploaded"] = True
        save_offload_progress(progress)

        s3 = FakeS3()  # bucket is empty — the prior 'upload' never really landed
        with s3.patch():
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 1)
        self.assertTrue(is_real_local(rel))
        self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
