from __future__ import annotations

from pathlib import Path
from typing import Any

from cloud_vfs.storage.env import normalize_archive

from .backends import fetch_path as _fetch_path
from .backends import upload_path as _upload_path
from .config import ArchiveConfig, manifest_with_provider, resolve_archive


def fetch_path(
    meta: dict[str, Any],
    rel: str,
    archive: str,
    env: dict[str, str],
    manifest: dict[str, Any],
    *,
    dest: Path | None = None,
    dest_root: Path | None = None,
) -> int:
    cfg = resolve_archive(env, manifest, normalize_archive(archive))
    meta = {**meta, "archive": cfg.name, "provider": cfg.provider}
    return _fetch_path(meta, rel, cfg, dest=dest, dest_root=dest_root)


def upload_path(
    rel: str,
    archive: str,
    env: dict[str, str],
    manifest: dict[str, Any],
    *,
    blob_prefix: str | None = None,
    progress_label: str | None = None,
) -> str:
    cfg = resolve_archive(env, manifest, normalize_archive(archive))
    return _upload_path(rel, cfg, blob_prefix=blob_prefix, progress_label=progress_label)


__all__ = ["fetch_path", "upload_path", "resolve_archive", "manifest_with_provider", "ArchiveConfig"]
