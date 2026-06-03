from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cloud_vfs.project import project_root, temp_dir
from cloud_vfs.storage.cleanup import (
    cleanup_download_temps,
    find_download_temps,
)


class CleanupDownloadsTests(unittest.TestCase):
    """Issue #21 — temp hygiene: find and remove stale .azDownload-*/.part/fetch-* temps."""

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

    def _make_temp(self, name: str, size: int, *, age_hours: float = 0.0) -> Path:
        path = temp_dir() / name
        path.write_bytes(b"x" * size)
        if age_hours:
            old = time.time() - age_hours * 3600.0
            os.utime(path, (old, old))
        return path

    def test_finds_all_temp_flavors(self) -> None:
        self._make_temp(".azDownload-abc123-azcopy-model_best.pth.1", 1024)
        self._make_temp("fetch-model.npy.deadbeef", 2048)
        self._make_temp("checkpoint.pth.part", 512)
        # a non-temp file must be ignored
        (temp_dir() / "keepme.json").write_text("{}")

        found = {p.name for p, _ in find_download_temps()}
        self.assertEqual(
            found,
            {
                ".azDownload-abc123-azcopy-model_best.pth.1",
                "fetch-model.npy.deadbeef",
                "checkpoint.pth.part",
            },
        )

    def test_dry_run_reports_but_keeps_files(self) -> None:
        a = self._make_temp(".azDownload-x-azcopy-a.1", 1000)
        b = self._make_temp("b.part", 2000)
        matched, removed, freed = cleanup_download_temps(dry_run=True)
        self.assertEqual(len(matched), 2)
        self.assertEqual(removed, 0)
        self.assertEqual(freed, 3000)
        self.assertTrue(a.exists() and b.exists())

    def test_removes_and_reports_freed_bytes(self) -> None:
        a = self._make_temp("fetch-a.bin.1", 4096)
        b = self._make_temp("a.part", 4096)
        matched, removed, freed = cleanup_download_temps()
        self.assertEqual(len(matched), 2)
        self.assertEqual(removed, 2)
        self.assertEqual(freed, 8192)
        self.assertFalse(a.exists() or b.exists())

    def test_older_than_hours_filters_recent(self) -> None:
        self._make_temp("recent.part", 100, age_hours=0.0)
        old = self._make_temp("old.part", 100, age_hours=48.0)
        matched, removed, _ = cleanup_download_temps(older_than_hours=24.0)
        self.assertEqual([p.name for p, _ in matched], ["old.part"])
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())

    def test_no_temp_dir_is_safe(self) -> None:
        # temp_dir() creates the dir; even empty it must not error.
        matched, removed, freed = cleanup_download_temps()
        self.assertEqual((matched, removed, freed), ([], 0, 0))


class CleanupDownloadsCliTests(unittest.TestCase):
    """Issue #21 — `cloud-vfs cleanup-downloads` command surface."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        cfg = self.root / ".cloud-vfs"
        cfg.mkdir()
        (cfg / "config.env").write_text("LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=test-bucket\n")
        (cfg / "manifest.json").write_text(
            json.dumps({"version": 3, "local_archive": {"provider": "aws", "bucket": "test-bucket"}, "entries": []}) + "\n"
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

    def test_cli_dry_run_lists_without_deleting(self) -> None:
        from cloud_vfs.cli import cmd_cleanup_downloads

        temp = temp_dir() / ".azDownload-z-azcopy-big.pth.1"
        temp.write_bytes(b"x" * (3 * 1024 * 1024))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_cleanup_downloads(dry_run=True, older_than_hours=None)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn(".azDownload-z-azcopy-big.pth.1", out)
        self.assertIn("3.0MB", out)
        self.assertTrue(temp.exists())

    def test_cli_removes_and_reports(self) -> None:
        from cloud_vfs.cli import cmd_cleanup_downloads

        temp = temp_dir() / "fetch-x.npy.1"
        temp.write_bytes(b"x" * 2048)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_cleanup_downloads(dry_run=False, older_than_hours=None)
        self.assertEqual(rc, 0)
        self.assertFalse(temp.exists())
        self.assertIn("removed 1", buf.getvalue())

    def test_cli_nothing_to_do(self) -> None:
        from cloud_vfs.cli import cmd_cleanup_downloads

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_cleanup_downloads(dry_run=False, older_than_hours=None)
        self.assertEqual(rc, 0)
        self.assertIn("No download temps", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
