from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.cli import cmd_ensure, cmd_guard
from cloud_vfs.guard import assess_delete_safety
from cloud_vfs.project import project_root
from cloud_vfs.storage.inventory import (
    detect_drift,
    find_row,
    load_policy,
    register_paths,
    upsert_row,
    verify_fetched_tree,
    VerifyError,
)
from cloud_vfs.storage.stub import write_inline_ref


class GuardVerifyTests(unittest.TestCase):
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

    def test_guard_blocks_unmanaged_real_local(self) -> None:
        rel = "data/prod-only.bin"
        (self.root / "data").mkdir(exist_ok=True)
        (self.root / rel).write_bytes(b"important" * 100)
        assessment = assess_delete_safety(rel)
        self.assertFalse(assessment["managed_by_cloud_vfs"])
        self.assertTrue(assessment["real_local"])
        self.assertFalse(assessment["safe_to_delete_local"])
        self.assertEqual(cmd_guard([rel], as_json=False), 1)

    def test_guard_allows_cloud_only_managed(self) -> None:
        rel = "data/offloaded.bin"
        policy = load_policy()
        shard = "data"
        upsert_row(
            shard,
            rel,
            {
                "local": rel,
                "archive": "local_archive",
                "state": "cloud-only",
                "size": 100,
                "blob": rel,
                "sha256": "abc",
            },
            policy,
        )
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        assessment = assess_delete_safety(rel)
        self.assertTrue(assessment["managed_by_cloud_vfs"])
        self.assertFalse(assessment["real_local"])
        self.assertTrue(assessment["safe_to_delete_local"])
        self.assertEqual(cmd_guard([rel], as_json=False), 0)

    def test_verify_fetched_tree_detects_mismatch(self) -> None:
        rel = "data/check.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original-bytes")
        register_paths([rel])
        path.write_bytes(b"tampered-bytes")
        with self.assertRaises(VerifyError):
            verify_fetched_tree(rel)

    def test_ensure_verify_fails_on_mismatch(self) -> None:
        rel = "data/check2.bin"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload")
        register_paths([rel])
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})

        def bad_fetch(_meta, _rel, _archive, _env, _manifest, *, dest=None, dest_root=None):
            assert dest is not None
            dest.write_bytes(b"wrong-payload")
            return len(b"wrong-payload")

        with patch("cloud_vfs.cli.fetch_path", side_effect=bad_fetch):
            rc = cmd_ensure([rel], verify=True)

        self.assertEqual(rc, 1)

    def test_stale_inline_ref_drift(self) -> None:
        rel = "data/stale-ref.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        policy = load_policy()
        upsert_row(
            "data",
            rel,
            {
                "local": rel,
                "archive": "local_archive",
                "state": "local",
                "size": 10,
                "sha256": "deadbeef",
            },
            policy,
        )
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})
        types = {i["type"] for i in detect_drift(check_blob=False)}
        self.assertIn("stale-inline-ref", types)


if __name__ == "__main__":
    unittest.main()
