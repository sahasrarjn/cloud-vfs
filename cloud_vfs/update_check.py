"""Non-blocking 'a newer version is available' notice.

Notify-only: this never installs anything or modifies the environment. It is
called once at the end of a CLI run, prints at most one line to stderr, and is
wrapped so that any failure (network, filesystem, parsing) is swallowed — it can
never slow down, block, or break a command.

Disable entirely with ``CLOUD_VFS_NO_UPDATE_CHECK=1``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

PACKAGE = "cloud-vfs"
_PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
_DEFAULT_INTERVAL_SEC = 24 * 60 * 60  # once per day
_DEFAULT_TIMEOUT_SEC = 1.5
_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _is_ci() -> bool:
    if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
        return True
    return bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("GITLAB_CI"))


def _opt_out() -> bool:
    val = os.environ.get("CLOUD_VFS_NO_UPDATE_CHECK", "").strip().lower()
    return val not in ("", "0", "false", "no")


def _is_enabled(stream: TextIO) -> bool:
    if _opt_out():
        return False
    if _is_ci():
        return False
    # Only nudge interactive users; stay silent when output is piped/captured.
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    return True


def _cache_path() -> Path:
    base = os.environ.get("CLOUD_VFS_CACHE_DIR")
    if base:
        root = Path(base)
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        root = Path(xdg) if xdg else Path.home() / ".cache"
        root = root / "cloud-vfs"
    return root / "update-check.json"


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so concurrent CLI runs never read a half-written cache.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _parse_version(value: str) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.match(value)
    if not match:
        return None
    parts = [int(p) if p is not None else 0 for p in match.groups()]
    return (parts[0], parts[1], parts[2])


def _is_newer(latest: str, current: str) -> bool:
    latest_v = _parse_version(latest)
    current_v = _parse_version(current)
    if latest_v is None or current_v is None:
        return False
    return latest_v > current_v


def _fetch_latest_version(timeout: float) -> str | None:
    from urllib.request import Request, urlopen

    req = Request(_PYPI_URL, headers={"User-Agent": f"{PACKAGE}-update-check"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed HTTPS PyPI URL
        payload = json.loads(resp.read().decode("utf-8"))
    version = (payload.get("info") or {}).get("version")
    return version if isinstance(version, str) and version else None


def _fetch_latest_within(deadline_sec: float) -> str | None:
    """Run the PyPI fetch but cap *total* wall time, including DNS resolution.

    ``urlopen(timeout=...)`` only bounds connect/read, not ``getaddrinfo``, so a
    broken resolver could otherwise delay process exit. We run the fetch in a
    daemon thread and abandon it if it overruns the deadline — the command's
    exit is never blocked for longer than ``deadline_sec``.
    """
    result: dict[str, str | None] = {"v": None}

    def _run() -> None:
        try:
            result["v"] = _fetch_latest_version(deadline_sec)
        except Exception:
            result["v"] = None

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline_sec)
    return result["v"]


def _due_for_check(cache: dict[str, Any]) -> bool:
    interval = _env_float("CLOUD_VFS_UPDATE_CHECK_INTERVAL", _DEFAULT_INTERVAL_SEC)
    last = cache.get("last_check")
    if not isinstance(last, (int, float)):
        return True
    return (time.time() - last) >= interval


def _format_notice(current: str, latest: str) -> str:
    return f"cloud-vfs {current} → {latest} available — run: pip install -U {PACKAGE}"


def maybe_notify_update(current_version: str, *, stream: TextIO | None = None) -> None:
    """Print an update notice to ``stream`` (stderr) if a newer version exists.

    Best-effort and exception-proof: any failure is swallowed.
    """
    stream = stream if stream is not None else sys.stderr
    try:
        if not _is_enabled(stream):
            return
        cache = _load_cache()
        latest = cache.get("latest_version")
        if _due_for_check(cache):
            fetched = _fetch_latest_within(
                _env_float("CLOUD_VFS_UPDATE_TIMEOUT", _DEFAULT_TIMEOUT_SEC)
            )
            if fetched:
                latest = fetched
            # Advance last_check even on failure so a persistent network problem
            # does not re-hit PyPI on every invocation.
            try:
                _save_cache({"last_check": time.time(), "latest_version": latest})
            except OSError:
                pass
        if isinstance(latest, str) and _is_newer(latest, current_version):
            print(_format_notice(current_version, latest), file=stream)
    except Exception:
        # Never let an update check affect the command's outcome.
        return
