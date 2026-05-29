from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs.doctor import run_checks
from cloud_vfs.project import project_root


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        cfg = self.root / ".cloud-vfs"
        cfg.mkdir()
        (cfg / "index").mkdir()
        (cfg / "config.env").write_text(
            "LOCAL_PROVIDER=aws\nAWS_LOCAL_BUCKET=test-bucket\nAWS_LOCAL_REGION=us-east-1\n"
        )
        (cfg / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "local_archive": {"provider": "aws", "bucket": "test-bucket", "region": "us-east-1"},
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

    def _status(self, results, name: str) -> str:
        for r in results:
            if r.name == name:
                return r.status
        self.fail(f"missing check: {name}")

    @patch("cloud_vfs.doctor._which", return_value="/usr/bin/aws")
    @patch("cloud_vfs.doctor._run_quiet")
    def test_doctor_aws_happy_path(self, mock_run, _which) -> None:
        mock_run.side_effect = [
            (0, "aws-cli/2.0"),
            (0, json.dumps({"Arn": "arn:aws:iam::123:user/demo"})),
        ]
        results = run_checks()
        self.assertEqual(self._status(results, "local_archive"), "ok")
        self.assertEqual(self._status(results, "aws-cli"), "ok")
        self.assertEqual(self._status(results, "credentials"), "ok")

    def test_doctor_without_project_stops_early(self) -> None:
        prev_cwd = Path.cwd()
        isolated = tempfile.mkdtemp(prefix="cloud-vfs-doctor-")
        try:
            os.environ["CLOUD_VFS_PROJECT_ROOT"] = isolated
            project_root.cache_clear()
            os.chdir(isolated)
            results = run_checks()
            names = [r.name for r in results]
            self.assertIn("project", names)
            self.assertEqual(self._status(results, "project"), "warn")
            self.assertNotIn("manifest", names)
        finally:
            os.chdir(prev_cwd)
            os.environ["CLOUD_VFS_PROJECT_ROOT"] = str(self.root)
            project_root.cache_clear()

    @patch("cloud_vfs.doctor._which", return_value=None)
    def test_doctor_missing_aws_cli(self, _which) -> None:
        results = run_checks()
        self.assertEqual(self._status(results, "aws-cli"), "fail")


if __name__ == "__main__":
    unittest.main()
