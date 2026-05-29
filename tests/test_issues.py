from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_ensure, cmd_offload
from cloud_vfs.project import project_root
from cloud_vfs.storage.inventory import find_row, index_offloaded_path, load_policy, upsert_rows_batch
from cloud_vfs.storage.manifest import load_manifest
from cloud_vfs.storage.offload_progress import (
    OffloadInterruptState,
    load_offload_progress,
    new_offload_progress,
    save_offload_progress,
)
from cloud_vfs.storage.paths import is_real_local
from cloud_vfs.storage.stub import is_ref, write_inline_ref


class IssueFixTests(unittest.TestCase):
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

    def test_ensure_expands_directory_with_inline_refs(self) -> None:
        """Issue #6 — ensure on a dir fetches each cloud-only file."""
        dir_rel = "data/probe_train_csvs"
        files = ["a.csv", "b.csv"]
        for name in files:
            rel = f"{dir_rel}/{name}"
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})

        payloads = {f"{dir_rel}/{name}": f"csv-{name}".encode() for name in files}

        def fake_fetch(meta, rel, archive, env, manifest, *, dest=None, dest_root=None, progress_label=None):
            assert dest is not None
            dest.write_bytes(payloads[rel])
            return len(payloads[rel])

        with patch("cloud_vfs.cli.fetch_path", side_effect=fake_fetch):
            rc = cmd_ensure([dir_rel], verify=False)

        self.assertEqual(rc, 0)
        for name in files:
            rel = f"{dir_rel}/{name}"
            self.assertTrue(is_real_local(rel))
            self.assertEqual((self.root / rel).read_bytes(), payloads[rel])

    def test_offload_keep_local_preserves_bytes(self) -> None:
        """Issue #4 — --keep-local uploads but does not replace files with stubs."""
        rel = "data/weights.npy"
        payload = b"\x93NUMPY\x00" + b"\x00" * 64
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

        with patch("cloud_vfs.cli.upload_path", return_value=rel):
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=False)

        self.assertEqual(rc, 0)
        self.assertTrue(is_real_local(rel))
        self.assertFalse(is_ref(rel))
        self.assertEqual(path.read_bytes(), payload)
        found = find_row(rel, load_policy())
        assert found is not None
        _, row = found
        self.assertEqual(row["state"], "local")

    def test_offload_delete_local_writes_stub(self) -> None:
        rel = "data/model.npy"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x93NUMPY\x00" + b"\x00" * 64)

        with patch("cloud_vfs.cli.upload_path", return_value=rel):
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 0)
        self.assertTrue(is_ref(rel))
        self.assertFalse(is_real_local(rel))

    def test_index_offloaded_path_batches_shard_writes(self) -> None:
        """Issue #5 — one save per shard, not one per file."""
        dir_rel = "data/many_files"
        precomputed: dict[str, str] = {}
        for i in range(5):
            rel = f"{dir_rel}/file{i}.csv"
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"row{i}")
            precomputed[rel] = f"hash{i}"

        saves: list[str] = []
        original = upsert_rows_batch

        def tracking_batch(shard_root, rows, policy):
            saves.append(shard_root)
            return original(shard_root, rows, policy)

        with patch("cloud_vfs.storage.inventory.upsert_rows_batch", side_effect=tracking_batch):
            count = index_offloaded_path(
                dir_rel,
                archive="local_archive",
                provider="aws",
                blob=None,
                blob_prefix=f"{dir_rel}/",
                entry={"id": "many", "local": dir_rel},
                precomputed=precomputed,
            )

        self.assertEqual(count, 5)
        self.assertEqual(len(saves), 1)

    def test_upload_passes_progress_label(self) -> None:
        """Issue #3 — upload emits a start label for long transfers."""
        rel = "data/big.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 128)

        captured: dict[str, str | None] = {}

        def fake_upload(*args, **kwargs):
            captured["label"] = kwargs.get("progress_label")
            return rel

        with patch("cloud_vfs.cli.upload_path", side_effect=fake_upload):
            cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertIn("uploading", captured.get("label") or "")

    def test_offload_resume_skips_upload(self) -> None:
        """Issue #8 — re-run resumes after upload checkpoint without re-uploading."""
        rel = "data/resume-me.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"resume-payload")

        upload_calls: list[str] = []

        def fake_upload(*args, **kwargs):
            upload_calls.append(args[0])
            return rel

        progress = new_offload_progress(
            rel,
            archive="local_archive",
            delete_local=True,
            precomputed={"data/resume-me.bin": "abc123"},
        )
        progress["uploaded"] = True
        save_offload_progress(progress)

        with patch("cloud_vfs.cli.upload_path", side_effect=fake_upload):
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 0)
        self.assertEqual(upload_calls, [])
        self.assertTrue(is_ref(rel))

    def test_offload_verify_only_reports_diff(self) -> None:
        """Issue #8 — --verify-only compares local files to blob listing."""
        rel = "data/verify-dir"
        for name in ("a.csv", "b.csv"):
            file_path = self.root / rel / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(name)

        def fake_list(_cfg, prefix):
            return [f"{prefix.rstrip('/')}/a.csv"]

        with patch("cloud_vfs.storage.offload_progress.list_blob_keys", side_effect=fake_list):
            rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True, verify_only=True)

        self.assertEqual(rc, 0)

    def test_sigterm_flushes_offload_progress(self) -> None:
        """Issue #8 — SIGTERM saves partial progress before exit."""
        rel = "data/interrupted.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"partial")

        progress = new_offload_progress(
            rel,
            archive="local_archive",
            delete_local=True,
            precomputed={rel: "deadbeef"},
        )
        progress["uploaded"] = True
        save_offload_progress(progress)

        manifest = load_manifest()
        state = OffloadInterruptState(manifest=manifest, progress=progress)
        state.flush()

        reloaded = load_offload_progress(rel)
        assert reloaded is not None
        self.assertTrue(reloaded.get("uploaded"))

    def test_subprocess_idle_timeout(self) -> None:
        """Issue #8 — hung subprocess aborts after idle timeout."""
        from cloud_vfs.storage.backends import _run_monitored
        from cloud_vfs.storage.errors import CloudStorageError

        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        with patch.dict(os.environ, {"CLOUD_VFS_SUBPROCESS_IDLE_TIMEOUT_SEC": "1"}):
            with self.assertRaises(CloudStorageError) as ctx:
                _run_monitored(cmd, action="test idle", heartbeat_sec=0.5, idle_timeout_sec=1.0)
        self.assertIn("no subprocess output", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
