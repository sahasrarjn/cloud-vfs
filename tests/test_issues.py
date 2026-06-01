from __future__ import annotations

import json
import os
import subprocess
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

    def test_upload_streams_progress_on_tty_for_large_files(self) -> None:
        """Issue #13 — large uploads stream native CLI progress when stdout is a TTY."""
        from cloud_vfs.storage.backends import PROGRESS_MIN_BYTES, upload_path
        from cloud_vfs.storage.config import ArchiveConfig

        rel = "data/large.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

        cfg = ArchiveConfig(
            name="local_archive",
            provider="azure",
            bucket="test-container",
            account="acct",
            key="key",
            profile=None,
            region=None,
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd, *, action, **kwargs):
            captured_cmds.append(list(cmd))
            if "upload" in action:
                self.assertTrue(kwargs.get("stream_output"))
            return subprocess.CompletedProcess(cmd, 0, stdout="Finished")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with patch("cloud_vfs.storage.backends._should_show_upload_progress", return_value=True):
                upload_path(rel, cfg, source_path=path)

        upload_cmd = next(cmd for cmd in captured_cmds if "upload" in " ".join(cmd))
        self.assertNotIn("--no-progress", upload_cmd)

    def test_upload_enables_progress_for_directories_on_tty(self) -> None:
        """Issue #13 — directory batch uploads always show progress on a TTY."""
        from cloud_vfs.storage.backends import upload_path
        from cloud_vfs.storage.config import ArchiveConfig

        rel = "data/batch"
        dir_path = self.root / rel
        dir_path.mkdir(parents=True)
        (dir_path / "a.csv").write_text("a")

        cfg = ArchiveConfig(
            name="local_archive",
            provider="azure",
            bucket="test-container",
            account="acct",
            key="key",
        )
        captured: list[tuple[list[str], dict]] = []

        def fake_run(cmd, *, action, **kwargs):
            captured.append((list(cmd), kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with patch("cloud_vfs.storage.backends.sys.stdout.isatty", return_value=True):
                with patch("cloud_vfs.storage.backends._is_ci", return_value=False):
                    upload_path(rel, cfg, source_path=dir_path)

        upload_cmd, kwargs = next(item for item in captured if "upload-batch" in " ".join(item[0]))
        self.assertTrue(kwargs.get("stream_output"))
        self.assertNotIn("--no-progress", upload_cmd)

    def test_aws_upload_progress_flags(self) -> None:
        """Issue #13 — AWS cp uses explicit progress flags matching Azure behavior."""
        from cloud_vfs.storage.backends import upload_path
        from cloud_vfs.storage.config import ArchiveConfig

        rel = "data/aws.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

        cfg = ArchiveConfig(
            name="local_archive",
            provider="aws",
            bucket="test-bucket",
            region="us-east-1",
        )
        captured: list[list[str]] = []

        def fake_run(cmd, *, action, **kwargs):
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with patch("cloud_vfs.storage.backends._should_show_upload_progress", return_value=True):
                upload_path(rel, cfg, source_path=path)
        cp_cmd = next(cmd for cmd in captured if "cp" in cmd)
        self.assertIn("--progress-multiline", cp_cmd)

        captured.clear()
        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with patch("cloud_vfs.storage.backends._should_show_upload_progress", return_value=False):
                upload_path(rel, cfg, source_path=path)
        cp_cmd = next(cmd for cmd in captured if "cp" in cmd)
        self.assertIn("--no-progress", cp_cmd)

    def test_should_show_upload_progress_disables_in_ci(self) -> None:
        """Review — suppress verbose path output in CI even when stdout is a TTY."""
        from cloud_vfs.storage.backends import _should_show_upload_progress

        dir_path = self.root / "data" / "ci-dir"
        dir_path.mkdir(parents=True)

        with patch("cloud_vfs.storage.backends.sys.stdout.isatty", return_value=True):
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=False):
                self.assertFalse(_should_show_upload_progress(dir_path))

    def test_upload_suppresses_progress_for_small_files(self) -> None:
        """Issue #13 — small single-file uploads keep --no-progress even on a TTY."""
        from cloud_vfs.storage.backends import upload_path
        from cloud_vfs.storage.config import ArchiveConfig

        rel = "data/small.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"small")

        cfg = ArchiveConfig(
            name="local_archive",
            provider="azure",
            bucket="test-container",
            account="acct",
            key="key",
            profile=None,
            region=None,
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd, *, action, **kwargs):
            captured_cmds.append(list(cmd))
            self.assertFalse(kwargs.get("stream_output"))
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        with patch("cloud_vfs.storage.backends._run", side_effect=fake_run):
            with patch("cloud_vfs.storage.backends.sys.stdout.isatty", return_value=True):
                with patch("cloud_vfs.storage.backends._is_ci", return_value=False):
                    upload_path(rel, cfg, source_path=path)

        upload_cmd = next(cmd for cmd in captured_cmds if "upload" in " ".join(cmd))
        self.assertIn("--no-progress", upload_cmd)

    def test_run_monitored_streams_subprocess_output(self) -> None:
        """Issue #13 — stream_output prints subprocess lines as they arrive."""
        from cloud_vfs.storage.backends import _run_monitored

        cmd = [sys.executable, "-c", "print('line-one'); print('line-two')"]
        with patch("builtins.print") as mock_print:
            _run_monitored(cmd, action="test stream", stream_output=True)
        output = "".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("line-one", output)
        self.assertIn("line-two", output)

    def test_offload_batch_continues_after_failure(self) -> None:
        """Issue #15 — batch offload does not stop at first path failure."""
        paths = ["data/ok.bin", "data/fail.bin", "data/also.bin"]
        for rel in paths:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(rel.encode())

        calls: list[str] = []

        def fake_upload(rel, *args, **kwargs):
            calls.append(rel)
            if rel == "data/fail.bin":
                from cloud_vfs.storage.errors import CloudStorageError

                raise CloudStorageError("upload", [], "simulated failure", 1)
            return rel

        with patch("cloud_vfs.cli.upload_path", side_effect=fake_upload):
            rc = cmd_offload(
                paths,
                dry_run=False,
                archive_override=None,
                delete_local=True,
            )

        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["data/ok.bin", "data/fail.bin", "data/also.bin"])
        self.assertTrue(is_ref("data/ok.bin"))
        self.assertTrue(is_ref("data/also.bin"))
        self.assertTrue(is_real_local("data/fail.bin"))

    def test_offload_skips_upload_when_blob_size_matches(self) -> None:
        """Issue #15 — resume detects complete blob by content length."""
        from cloud_vfs.storage.backends import blob_matches_local_size
        from cloud_vfs.storage.config import ArchiveConfig

        rel = "data/checkpoint.pth"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 4096)

        cfg = ArchiveConfig(
            name="local_archive",
            provider="azure",
            bucket="test-container",
            account="acct",
            key="key",
        )
        upload_calls: list[str] = []

        with patch("cloud_vfs.storage.backends.blob_content_length", return_value=4096):
            self.assertTrue(blob_matches_local_size(cfg, rel, path))
            with patch("cloud_vfs.cli.upload_path", side_effect=lambda r, *a, **k: upload_calls.append(r) or r):
                rc = cmd_offload([rel], dry_run=False, archive_override=None, delete_local=True)

        self.assertEqual(rc, 0)
        self.assertEqual(upload_calls, [])
        self.assertTrue(is_ref(rel))

    def test_offload_binary_pth_dry_run_no_crash(self) -> None:
        """Issue #15 / #7 — large .pth binary must not crash is_ref_path during offload."""
        rel = "research/model_best.pth"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(1024 * 1024))

        rc = cmd_offload([rel], dry_run=True, archive_override=None, delete_local=True)
        self.assertEqual(rc, 0)
        self.assertTrue(is_real_local(rel))

    def test_run_monitored_streams_carriage_return_progress(self) -> None:
        """Issue #13 — chunk streaming forwards \\r-based CLI progress updates."""
        from cloud_vfs.storage.backends import _run_monitored

        cmd = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('10%\\r20%\\r100%\\n'); sys.stdout.flush()",
        ]
        with patch("builtins.print") as mock_print:
            _run_monitored(cmd, action="test cr progress", stream_output=True)
        output = "".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("10%", output)
        self.assertIn("100%", output)


if __name__ == "__main__":
    unittest.main()
