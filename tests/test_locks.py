from __future__ import annotations

import fcntl
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.project import project_root
from cloud_vfs.storage.locks import lock_file_for, path_lock


class PathLockPrimitiveTests(unittest.TestCase):
    """Issue #22 — advisory file lock mutual exclusion."""

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

    def test_held_lock_blocks_second_nonblocking_acquire(self) -> None:
        rel = "data/model.npy"
        with path_lock(rel) as acquired_first:
            self.assertTrue(acquired_first)
            # A second, independent open of the same lock file must not be grabbable.
            other = open(lock_file_for(rel), "w")
            try:
                with self.assertRaises(OSError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                other.close()

    def test_lock_released_after_context(self) -> None:
        rel = "data/model.npy"
        with path_lock(rel):
            pass
        # After release the lock is freely acquirable again.
        other = open(lock_file_for(rel), "w")
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other, fcntl.LOCK_UN)
        finally:
            other.close()

    def test_distinct_paths_get_distinct_lock_files(self) -> None:
        self.assertNotEqual(lock_file_for("data/a.npy"), lock_file_for("data/b.npy"))
        self.assertEqual(lock_file_for("data/a.npy"), lock_file_for("data/a.npy"))


class EnsureLockReuseTests(unittest.TestCase):
    """Issue #22 — ensure re-checks under the lock and skips redundant fetch."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        cfg = self.root / ".cloud-vfs"
        cfg.mkdir()
        (cfg / "index").mkdir()
        (cfg / "config.env").write_text("LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=test-bucket\n")
        (cfg / "manifest.json").write_text(
            json.dumps({"version": 3, "local_archive": {"provider": "aws", "bucket": "test-bucket"}, "entries": []}) + "\n"
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

    def test_concurrent_completion_skips_fetch(self) -> None:
        """If the real file appears while we waited for the lock, do not re-download."""
        from contextlib import contextmanager

        from cloud_vfs.cli import cmd_ensure
        from cloud_vfs.storage.stub import write_inline_ref

        rel = "data/model.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})

        @contextmanager
        def fake_lock(target, *, on_wait=None):
            # Simulate another process finishing the download while we blocked.
            if on_wait is not None:
                on_wait()
            (self.root / target).write_bytes(b"\x93NUMPY fetched-by-other")
            yield False

        fetch_calls: list[str] = []

        def fake_fetch(*args, **kwargs):
            fetch_calls.append(args[0])
            return 1

        with patch("cloud_vfs.cli.path_lock", side_effect=fake_lock):
            with patch("cloud_vfs.cli.fetch_path", side_effect=fake_fetch):
                rc = cmd_ensure([rel], verify=False)

        self.assertEqual(rc, 0)
        self.assertEqual(fetch_calls, [])  # re-check under lock skipped the fetch

    def test_uncontended_fetch_still_runs(self) -> None:
        """With no concurrent writer, ensure fetches normally under the lock."""
        from cloud_vfs.cli import cmd_ensure
        from cloud_vfs.storage.paths import is_real_local
        from cloud_vfs.storage.stub import write_inline_ref

        rel = "data/model.npy"
        write_inline_ref(rel, {"blob": rel, "archive": "local_archive"})

        def fake_fetch(meta, rel_arg, archive, env, manifest, *, dest=None, dest_root=None, progress_label=None):
            assert dest is not None
            dest.write_bytes(b"payload-bytes")
            return len(b"payload-bytes")

        with patch("cloud_vfs.cli.fetch_path", side_effect=fake_fetch):
            rc = cmd_ensure([rel], verify=False)

        self.assertEqual(rc, 0)
        self.assertTrue(is_real_local(rel))


if __name__ == "__main__":
    unittest.main()
