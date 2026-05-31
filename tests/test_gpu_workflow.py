from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.gpu_workflow import (
    cmd_ensure_remote,
    cmd_ingest,
    cmd_preflight,
    resolve_materialize_meta,
)
from cloud_vfs.project import project_root
from cloud_vfs.storage.env import archive_from_entry, normalize_archive
from cloud_vfs.storage.stub import is_ref, write_inline_ref


class GpuWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        cfg = self.root / ".cloud-vfs"
        cfg.mkdir()
        (cfg / "index").mkdir()
        (cfg / "config.env").write_text(
            "LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=archive-bucket\n"
            "REMOTE_PROVIDER=aws\nAWS_REMOTE_BUCKET=staging-bucket\n"
        )
        (cfg / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "local_archive": {"provider": "aws", "bucket": "archive-bucket"},
                    "remote_staging": {"provider": "aws", "bucket": "staging-bucket"},
                    "entries": [
                        {
                            "id": "emb",
                            "local": "data/embeddings.npy",
                            "archive": "local_archive",
                            "blob": "data/embeddings.npy",
                            "status": "offloaded-local-removed",
                        },
                        {
                            "id": "gpu-csv",
                            "local": "data/gpu/train.csv",
                            "blob_role": "staging",
                            "blob": "data/gpu/train.csv",
                            "status": "offloaded-local-removed",
                        },
                    ],
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

    def test_blob_role_resolves_to_staging(self) -> None:
        entry = {"blob_role": "staging", "archive": "local_archive"}
        self.assertEqual(archive_from_entry(entry), "remote_staging")
        self.assertEqual(normalize_archive("gpu_staging"), "remote_staging")

    def test_preflight_fails_on_inline_ref(self) -> None:
        rel = "data/embeddings.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        rc = cmd_preflight([rel], as_json=False)
        self.assertEqual(rc, 1)

    def test_preflight_ok_when_materialized(self) -> None:
        rel = "data/local.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"materialized")
        rc = cmd_preflight([rel], as_json=False)
        self.assertEqual(rc, 0)

    def test_resolve_meta_from_manifest_blob_role(self) -> None:
        from cloud_vfs.storage.manifest import load_manifest

        manifest = load_manifest()
        meta = resolve_materialize_meta("data/gpu/train.csv", manifest)
        self.assertEqual(meta["archive"], "remote_staging")
        self.assertEqual(meta["blob"], "data/gpu/train.csv")

    def test_ensure_remote_writes_under_dest_root(self) -> None:
        rel = "data/embeddings.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        dest_root = self.root / "workspace"
        payload = b"\x93NUMPY\x00" + b"x" * 32

        def fake_fetch(meta, r, archive, env, manifest, *, dest=None, dest_root=None, progress_label=None):
            assert dest is not None
            dest.write_bytes(payload)
            return len(payload)

        with patch("cloud_vfs.gpu_workflow.fetch_path", side_effect=fake_fetch):
            rc = cmd_ensure_remote(
                [rel],
                dest_root=dest_root,
                archive="local_archive",
                manifest_file=None,
                paths_file=None,
                config_env=None,
                secrets_env=None,
                project_root_override=None,
            )

        self.assertEqual(rc, 0)
        out = dest_root / rel
        self.assertTrue(out.is_file())
        self.assertEqual(out.read_bytes(), payload)

    def test_ingest_uploads_from_external_source(self) -> None:
        source = self.root / "tmp" / "model_best.pth"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"checkpoint-bytes")
        dest_rel = "research/runs/model_best.pth"

        with patch("cloud_vfs.gpu_workflow.upload_path", return_value=dest_rel) as upload:
            rc = cmd_ingest(
                source,
                dest_rel,
                archive="local_archive",
                dry_run=False,
                emit_stub=True,
                index_inventory=True,
            )

        self.assertEqual(rc, 0)
        upload.assert_called_once()
        self.assertEqual(
            upload.call_args.kwargs.get("source_path").resolve(),
            source.resolve(),
        )
        self.assertTrue(is_ref(dest_rel))


if __name__ == "__main__":
    unittest.main()
