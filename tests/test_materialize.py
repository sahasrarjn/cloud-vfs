from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_ensure, cmd_resolve
from cloud_vfs.materialize import cmd_ensure_at_target, cmd_ingest, cmd_preflight, resolve_materialize_meta
from cloud_vfs.project import project_root
from cloud_vfs.storage.env import archive_from_entry, normalize_archive
from cloud_vfs.storage.stub import is_ref, write_inline_ref


class MaterializeTests(unittest.TestCase):
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
                            "id": "staging-csv",
                            "local": "data/staging/train.csv",
                            "blob_role": "staging",
                            "blob": "data/staging/train.csv",
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

    def test_blob_role_resolves_to_secondary_archive(self) -> None:
        entry = {"blob_role": "staging", "archive": "local_archive"}
        self.assertEqual(archive_from_entry(entry), "remote_staging")
        self.assertEqual(normalize_archive("secondary"), "remote_staging")

    def test_preflight_fails_on_inline_ref(self) -> None:
        rel = "data/embeddings.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        rc = cmd_preflight([rel], as_json=False)
        self.assertEqual(rc, 1)

    def test_resolve_emits_source_and_target(self) -> None:
        rel = "data/embeddings.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_resolve(rel)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("source", payload)
        self.assertIn("target", payload)
        self.assertIn("custom_root", payload["target"])

    def test_resolve_meta_from_manifest_blob_role(self) -> None:
        from cloud_vfs.storage.manifest import load_manifest

        manifest = load_manifest()
        meta = resolve_materialize_meta("data/staging/train.csv", manifest)
        self.assertEqual(meta["archive"], "remote_staging")

    def test_ensure_at_target_writes_under_target_root(self) -> None:
        rel = "data/embeddings.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        target_root = self.root / "workspace"
        payload = b"\x93NUMPY\x00" + b"x" * 32

        def fake_fetch(meta, r, archive, env, manifest, *, dest=None, dest_root=None, progress_label=None):
            assert dest is not None
            dest.write_bytes(payload)
            return len(payload)

        with patch("cloud_vfs.materialize.fetch_path", side_effect=fake_fetch):
            rc = cmd_ensure_at_target(
                [rel],
                target_root=target_root,
                source_archive="local_archive",
                manifest_file=None,
                paths_file=None,
                config_env=None,
                secrets_env=None,
                ref_root=None,
            )

        self.assertEqual(rc, 0)
        out = target_root / rel
        self.assertEqual(out.read_bytes(), payload)

    def test_ensure_target_root_via_cli(self) -> None:
        rel = "data/embeddings.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        target_root = self.root / "out"

        def fake_at_target(*args, **kwargs):
            return 0

        with patch("cloud_vfs.materialize.cmd_ensure_at_target", side_effect=fake_at_target) as called:
            rc = cmd_ensure(
                [rel],
                verify=False,
                target_root=target_root,
                source_archive="remote_staging",
            )
        self.assertEqual(rc, 0)
        called.assert_called_once()

    def test_ingest_uploads_source_to_target(self) -> None:
        source = self.root / "tmp" / "model_best.pth"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"checkpoint-bytes")
        target_rel = "research/runs/model_best.pth"

        with patch("cloud_vfs.materialize.upload_path", return_value=target_rel) as upload:
            rc = cmd_ingest(
                source,
                target_rel,
                source_archive="local_archive",
                dry_run=False,
                emit_stub=True,
                index_inventory=True,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(upload.call_args.kwargs.get("source_path").resolve(), source.resolve())
        self.assertTrue(is_ref(target_rel))


if __name__ == "__main__":
    unittest.main()
