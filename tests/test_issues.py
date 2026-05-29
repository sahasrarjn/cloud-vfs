from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_ensure, cmd_offload
from cloud_vfs.project import project_root
from cloud_vfs.storage.inventory import find_row, index_offloaded_path, load_policy, upsert_rows_batch
from cloud_vfs.storage.manifest import load_manifest
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

        def fake_fetch(meta, rel, archive, env, manifest, *, dest=None, dest_root=None):
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


if __name__ == "__main__":
    unittest.main()
