from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_offload
from cloud_vfs.project import project_root
from cloud_vfs.storage.backends import upload_path
from cloud_vfs.storage.config import ArchiveConfig
from cloud_vfs.storage.errors import CloudStorageError
from cloud_vfs.storage.paths import is_real_local
from cloud_vfs.storage.stub import is_ref


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

    def _cfg(self) -> ArchiveConfig:
        return ArchiveConfig(
            name="local_archive",
            provider="aws",
            bucket="test-bucket",
            region="us-east-1",
        )


class UploadVerificationTests(_AwsProjectTestCase):
    """Issue #37 — upload must HEAD the object and fail loudly when it is absent."""

    def test_upload_raises_when_object_absent_after_cp(self) -> None:
        rel = "data/out.mp4"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 4096)

        def fake_run(cmd, *, action, **kwargs):
            # cp "succeeds" but the object never lands — head-object 404s.
            if "head-object" in cmd:
                raise CloudStorageError(action, list(cmd), "Not Found", 254)
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with self.assertRaises(CloudStorageError):
                upload_path(rel, self._cfg(), source_path=path)

    def test_upload_raises_on_size_mismatch(self) -> None:
        rel = "data/out.mp4"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 4096)

        def fake_run(cmd, *, action, **kwargs):
            if "head-object" in cmd:
                # Remote object exists but is truncated/wrong size.
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ContentLength": 10}))
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with self.assertRaises(CloudStorageError):
                upload_path(rel, self._cfg(), source_path=path)

    def test_upload_verifies_with_head_object_not_ls(self) -> None:
        rel = "data/out.mp4"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 4096)

        captured: list[list[str]] = []

        def fake_run(cmd, *, action, **kwargs):
            captured.append(list(cmd))
            if "head-object" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ContentLength": 4096}))
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            upload_path(rel, self._cfg(), source_path=path)

        # Verification uses an authoritative head-object, never a prefix `s3 ls`.
        self.assertTrue(any("head-object" in cmd for cmd in captured))
        verify_cmds = [cmd for cmd in captured if "head-object" in cmd]
        self.assertTrue(any("data/out.mp4" in cmd for cmd in verify_cmds))


class OffloadDataLossTests(_AwsProjectTestCase):
    """Issue #37 — a failed/no-op upload must never delete the local file."""

    def test_offload_keeps_local_when_remote_absent(self) -> None:
        rel = "data/precious.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"precious-bytes" * 512
        path.write_bytes(payload)

        def fake_run(cmd, *, action, **kwargs):
            if "head-object" in cmd:
                raise CloudStorageError(action, list(cmd), "Not Found", 254)
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 1)
        self.assertTrue(is_real_local(rel))
        self.assertFalse(is_ref(rel))
        self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
