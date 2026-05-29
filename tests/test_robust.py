from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import _safe_fetch, cmd_ensure, cmd_offload, cmd_register
from cloud_vfs.project import project_root, temp_dir
from cloud_vfs.storage.backends import upload_path
from cloud_vfs.storage.config import ArchiveConfig
from cloud_vfs.storage.inventory import (
    detect_drift,
    find_row,
    hash_paths_before_offload,
    load_policy,
    prune_inventory,
    register_paths,
    sha256_file,
)
from cloud_vfs.storage.manifest import load_manifest, save_manifest
from cloud_vfs.storage.paths import is_real_local, normalize_rel
from cloud_vfs.storage.stub import is_ref, read_stub, write_inline_ref


class RobustnessTests(unittest.TestCase):
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

    def _write_file(self, rel: str, data: bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_fetch_failure_preserves_inline_ref(self) -> None:
        rel = "data/large.bin"
        self._write_file(rel, b"payload-bytes")
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        self.assertTrue(is_ref(rel))

        meta = read_stub(rel)
        assert meta is not None

        with patch("cloud_vfs.cli.fetch_path", side_effect=FileNotFoundError("blob missing")):
            with self.assertRaises(FileNotFoundError):
                _safe_fetch(rel, meta, "local_archive", {}, load_manifest())

        self.assertTrue(is_ref(rel))
        self.assertFalse(is_real_local(rel))

    def test_ensure_cmd_returns_error_when_fetch_fails(self) -> None:
        rel = "data/large.bin"
        self._write_file(rel, b"payload-bytes")
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})

        with patch("cloud_vfs.cli.fetch_path", side_effect=FileNotFoundError("blob missing")):
            rc = cmd_ensure([rel])

        self.assertEqual(rc, 1)
        self.assertTrue(is_ref(rel))

    def test_safe_fetch_replaces_inline_ref_with_bytes(self) -> None:
        rel = "data/large.bin"
        payload = b"real-binary-payload"
        self._write_file(rel, payload)
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        meta = read_stub(rel)
        assert meta is not None

        def fake_fetch(_meta, _rel, _archive, _env, _manifest, *, dest=None, dest_root=None):
            assert dest is not None
            dest.write_bytes(payload)
            return len(payload)

        with patch("cloud_vfs.cli.fetch_path", side_effect=fake_fetch):
            nbytes = _safe_fetch(rel, meta, "local_archive", {}, load_manifest())

        self.assertEqual(nbytes, len(payload))
        self.assertTrue(is_real_local(rel))
        self.assertEqual((self.root / rel).read_bytes(), payload)

    def test_empty_directory_upload_rejected(self) -> None:
        rel = "data/empty"
        path = self.root / rel
        path.mkdir(parents=True)
        cfg = ArchiveConfig(name="local_archive", provider="aws", bucket="test-bucket")
        with self.assertRaises(ValueError):
            upload_path(rel, cfg)

    def test_register_indexes_with_sha256(self) -> None:
        rel = "data/sample.bin"
        data = b"hello-inventory"
        self._write_file(rel, data)
        indexed, skipped = register_paths([rel])
        self.assertEqual(indexed, 1)
        self.assertEqual(skipped, 0)
        found = find_row(rel, load_policy())
        assert found is not None
        _, row = found
        self.assertEqual(row["sha256"], sha256_file(self.root / rel))
        self.assertEqual(row["state"], "local")

    def test_offload_hashes_before_stub(self) -> None:
        rel = "data/offload-me.bin"
        data = b"offload-payload"
        self._write_file(rel, data)
        digest = sha256_file(self.root / rel)

        with patch("cloud_vfs.cli.upload_path", return_value=rel):
            rc = cmd_offload([rel], dry_run=False, archive_override=None)

        self.assertEqual(rc, 0)
        self.assertTrue(is_ref(rel))
        found = find_row(rel, load_policy())
        assert found is not None
        _, row = found
        self.assertEqual(row["sha256"], digest)
        self.assertEqual(row["state"], "cloud-only")

    def test_hash_before_offload_captures_digest_while_local(self) -> None:
        rel = "data/hash-me.bin"
        self._write_file(rel, b"12345")
        digests = hash_paths_before_offload(rel)
        self.assertEqual(digests[rel], sha256_file(self.root / rel))

    def test_prune_removes_sub_threshold_rows(self) -> None:
        rel = "data/small.bin"
        self._write_file(rel, b"tiny")
        register_paths([rel])
        policy_path = self.root / ".cloud-vfs/inventory-policy.json"
        policy = json.loads(policy_path.read_text())
        policy["min_size_bytes"] = 100
        policy_path.write_text(json.dumps(policy) + "\n")
        removed, kept = prune_inventory()
        self.assertEqual(removed, 1)
        self.assertEqual(kept, 0)
        self.assertIsNone(find_row(rel, load_policy()))

    def test_detect_drift_orphan_local(self) -> None:
        rel = "data/unregistered.bin"
        self._write_file(rel, b"x" * 128)
        issues = detect_drift(check_blob=False)
        types = {i["type"] for i in issues}
        self.assertIn("orphan-local", types)

    def test_manifest_atomic_save(self) -> None:
        manifest = load_manifest()
        manifest["entries"] = [{"id": "demo", "local": "data/demo", "status": "synced"}]
        save_manifest(manifest)
        reloaded = load_manifest()
        self.assertEqual(len(reloaded["entries"]), 1)
        self.assertFalse((self.root / ".cloud-vfs/manifest.json.tmp").exists())

    def test_temp_dir_created_under_cloud_vfs(self) -> None:
        path = temp_dir()
        self.assertTrue(path.is_dir())
        self.assertEqual(path.name, ".tmp")


if __name__ == "__main__":
    unittest.main()
