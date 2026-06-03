from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from cloud_vfs.project import project_root

try:  # POSIX (macOS, Linux) — the only platforms cloud-vfs targets
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows fallback
    _HAVE_FCNTL = False


def _locks_dir() -> Path:
    path = project_root() / ".cloud-vfs" / "locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lock_file_for(rel: str) -> Path:
    """Stable lock-file path for a project-relative target path."""
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
    return _locks_dir() / f"{digest}.lock"


@contextmanager
def path_lock(rel: str, *, on_wait: Callable[[], None] | None = None) -> Iterator[bool]:
    """Serialize work on ``rel`` across processes via an advisory file lock.

    Yields ``True`` when the lock was acquired immediately, ``False`` when another
    holder was in-flight and we had to wait for it. Callers should re-check local
    state on ``False`` — the other process may have just finished the download, so
    a second fetch (and its egress) can be skipped.

    Best-effort no-op (always yields ``True``) when ``fcntl`` is unavailable.
    """
    if not _HAVE_FCNTL:
        yield True
        return

    lock_path = lock_file_for(rel)
    handle = open(lock_path, "w")
    waited = False
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            waited = True
            if on_wait is not None:
                on_wait()
            fcntl.flock(handle, fcntl.LOCK_EX)  # block until the holder releases
        yield not waited
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
