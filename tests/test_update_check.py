from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_vfs import update_check


class _CacheEnv(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmpdir.name)
        self._env = patch.dict(
            os.environ,
            {
                "CLOUD_VFS_CACHE_DIR": str(self.cache_dir),
                # default-enable: pretend not in CI for the gating tests that opt in
            },
            clear=False,
        )
        self._env.start()
        # Remove CI markers that may exist in the real environment.
        for var in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "CLOUD_VFS_NO_UPDATE_CHECK"):
            os.environ.pop(var, None)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _notify(self, current: str) -> str:
        buf = io.StringIO()
        # Force the "enabled" gate to pass regardless of the test runner's TTY/CI.
        with patch.object(update_check, "_is_enabled", return_value=True):
            update_check.maybe_notify_update(current, stream=buf)
        return buf.getvalue()

    def _seed_cache(self, latest: str, *, age_sec: float = 0.0) -> None:
        update_check._save_cache({"last_check": time.time() - age_sec, "latest_version": latest})


class VersionParsingTests(unittest.TestCase):
    def test_parse_basic(self) -> None:
        self.assertEqual(update_check._parse_version("0.5.11"), (0, 5, 11))

    def test_parse_with_suffix(self) -> None:
        self.assertEqual(update_check._parse_version("1.2.3rc1"), (1, 2, 3))

    def test_parse_unparseable(self) -> None:
        self.assertIsNone(update_check._parse_version("not-a-version"))

    def test_is_newer(self) -> None:
        self.assertTrue(update_check._is_newer("0.5.11", "0.5.10"))
        self.assertTrue(update_check._is_newer("0.6.0", "0.5.11"))
        self.assertFalse(update_check._is_newer("0.5.10", "0.5.10"))
        self.assertFalse(update_check._is_newer("0.5.9", "0.5.10"))

    def test_is_newer_unparseable_is_false(self) -> None:
        self.assertFalse(update_check._is_newer("garbage", "0.5.10"))
        self.assertFalse(update_check._is_newer("0.5.11", "garbage"))


class NotifyFromCacheTests(_CacheEnv):
    def test_prints_notice_when_cache_has_newer(self) -> None:
        self._seed_cache("0.5.11", age_sec=0.0)
        with patch.object(update_check, "_fetch_latest_version") as fetch:
            out = self._notify("0.5.10")
            fetch.assert_not_called()  # cache fresh → no network
        self.assertIn("0.5.11", out)
        self.assertIn("pip install -U cloud-vfs", out)

    def test_silent_when_up_to_date(self) -> None:
        self._seed_cache("0.5.11", age_sec=0.0)
        out = self._notify("0.5.11")
        self.assertEqual(out, "")

    def test_silent_when_current_is_newer_than_cache(self) -> None:
        self._seed_cache("0.5.10", age_sec=0.0)
        out = self._notify("0.6.0")
        self.assertEqual(out, "")


class ThrottleTests(_CacheEnv):
    def test_fetches_when_cache_stale(self) -> None:
        self._seed_cache("0.5.10", age_sec=10**9)  # ancient
        with patch.object(update_check, "_fetch_latest_version", return_value="0.5.11") as fetch:
            out = self._notify("0.5.10")
            fetch.assert_called_once()
        self.assertIn("0.5.11", out)
        # cache refreshed
        cache = update_check._load_cache()
        self.assertEqual(cache.get("latest_version"), "0.5.11")

    def test_no_fetch_when_cache_fresh(self) -> None:
        self._seed_cache("0.5.10", age_sec=0.0)
        with patch.object(update_check, "_fetch_latest_version") as fetch:
            self._notify("0.5.10")
            fetch.assert_not_called()

    def test_fetch_failure_advances_last_check_and_is_silent(self) -> None:
        self._seed_cache("0.5.10", age_sec=10**9)
        before = time.time()
        with patch.object(update_check, "_fetch_latest_version", return_value=None):
            out = self._notify("0.5.10")
        self.assertEqual(out, "")  # 0.5.10 not newer than cached 0.5.10
        cache = update_check._load_cache()
        self.assertGreaterEqual(cache.get("last_check", 0), before)  # throttle advanced


class GatingTests(_CacheEnv):
    def test_opt_out_env_disables(self) -> None:
        self._seed_cache("0.5.11", age_sec=0.0)
        buf = io.StringIO()
        with patch.dict(os.environ, {"CLOUD_VFS_NO_UPDATE_CHECK": "1"}):
            update_check.maybe_notify_update("0.5.10", stream=buf)
        self.assertEqual(buf.getvalue(), "")

    def test_ci_disables(self) -> None:
        self._seed_cache("0.5.11", age_sec=0.0)
        buf = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            update_check.maybe_notify_update("0.5.10", stream=buf)
        self.assertEqual(buf.getvalue(), "")

    def test_non_tty_disables(self) -> None:
        # _is_enabled should be False when the stream is not a TTY.
        buf = io.StringIO()  # StringIO has no isatty -> treated as non-tty
        self.assertFalse(update_check._is_enabled(buf))


class FailSafeTests(_CacheEnv):
    def test_never_raises_on_fetch_exception(self) -> None:
        self._seed_cache("0.5.10", age_sec=10**9)
        with patch.object(update_check, "_fetch_latest_version", side_effect=RuntimeError("boom")):
            # Must not propagate.
            out = self._notify("0.5.10")
        self.assertEqual(out, "")

    def test_never_raises_on_bad_cache_dir(self) -> None:
        with patch.dict(os.environ, {"CLOUD_VFS_CACHE_DIR": "/proc/nonexistent/cannot/write"}):
            with patch.object(update_check, "_fetch_latest_version", return_value="0.5.11"):
                # Should silently degrade, not raise.
                update_check.maybe_notify_update("0.5.10", stream=io.StringIO())


class CliIntegrationTests(unittest.TestCase):
    def test_main_returns_rc_and_calls_notifier(self) -> None:
        from cloud_vfs import cli

        with patch.object(cli, "_dispatch", return_value=0) as dispatch:
            with patch.object(cli, "maybe_notify_update") as notify:
                rc = cli.main(["status"])
        self.assertEqual(rc, 0)
        dispatch.assert_called_once()
        notify.assert_called_once()

    def test_main_notifier_failure_does_not_break_command(self) -> None:
        from cloud_vfs import cli

        with patch.object(cli, "_dispatch", return_value=3):
            with patch.object(cli, "maybe_notify_update", side_effect=RuntimeError("boom")):
                # Even if the notifier blows up, main must still return the command's rc.
                rc = cli.main(["status"])
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
