from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from cloud_vfs.project import temp_dir

# Scratch artifacts that fetch/azcopy leave under .cloud-vfs/.tmp during a download:
#   fetch-<name>.<hex>   cloud-vfs `ensure` scratch destination (renamed into place on success)
#   *.part               in-progress atomic-rename target for a single blob
#   .azDownload-*        azcopy's own per-job temp files (e.g. .azDownload-<uuid>-azcopy-<name>)
# An interrupted or killed download (Ctrl-C, SIGKILL, OOM) can leave any of these behind,
# and each orphan is a full-size copy that re-bills blob egress if a retry re-downloads.
TEMP_GLOBS = ("fetch-*", "*.part", ".azDownload-*")


def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _iter_temp_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    seen: set[Path] = set()
    for pattern in TEMP_GLOBS:
        for path in root.rglob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            yield path


def find_download_temps(
    *,
    older_than_hours: float | None = None,
    now: float | None = None,
) -> list[tuple[Path, int]]:
    """Return (path, size) for stale download temps under the project temp dir.

    older_than_hours=None means "all incomplete temps" (the default for cleanup).
    """
    current = now if now is not None else time.time()
    cutoff = None if older_than_hours is None else current - older_than_hours * 3600.0
    out: list[tuple[Path, int]] = []
    for path in _iter_temp_files(temp_dir()):
        try:
            stat = path.stat()
        except OSError:
            continue
        if cutoff is not None and stat.st_mtime > cutoff:
            continue
        out.append((path, stat.st_size))
    return sorted(out, key=lambda item: item[0].as_posix())


def cleanup_download_temps(
    *,
    older_than_hours: float | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> tuple[list[tuple[Path, int]], int, int]:
    """Remove stale download temps. Returns (matched, removed_count, freed_bytes).

    With dry_run=True nothing is deleted but freed_bytes reflects what would be reclaimed.
    """
    matched = find_download_temps(older_than_hours=older_than_hours, now=now)
    removed = 0
    freed = 0
    for path, size in matched:
        if dry_run:
            freed += size
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
    return matched, removed, freed
