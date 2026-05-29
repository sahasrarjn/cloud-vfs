from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cloud_vfs.project import project_root
from cloud_vfs.storage.backends import list_blob_keys
from cloud_vfs.storage.config import ArchiveConfig
from cloud_vfs.storage.inventory import _iter_local_files
from cloud_vfs.storage.io_util import atomic_write_json
from cloud_vfs.storage.manifest import find_entry, load_manifest
from cloud_vfs.storage.paths import normalize_rel

PROGRESS_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def progress_dir() -> Path:
    path = project_root() / ".cloud-vfs" / "offload-progress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def progress_file(rel: str) -> Path:
    safe = normalize_rel(rel).replace("/", "__")
    return progress_dir() / f"{safe}.json"


def load_offload_progress(rel: str) -> dict[str, Any] | None:
    path = progress_file(rel)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("rel") != normalize_rel(rel):
        return None
    return data


def save_offload_progress(data: dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    atomic_write_json(progress_file(data["rel"]), data)


def clear_offload_progress(rel: str) -> None:
    progress_file(rel).unlink(missing_ok=True)


def new_offload_progress(
    rel: str,
    *,
    archive: str,
    delete_local: bool,
    precomputed: dict[str, str],
) -> dict[str, Any]:
    rel = normalize_rel(rel)
    return {
        "version": PROGRESS_VERSION,
        "rel": rel,
        "archive": archive,
        "delete_local": delete_local,
        "uploaded": False,
        "indexed_files": [],
        "stubbed": False,
        "manifest_saved": False,
        "precomputed": precomputed,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def expected_blob_key(
    file_rel: str,
    *,
    rel: str,
    blob: str | None,
    blob_prefix: str | None,
) -> str:
    if blob and file_rel == normalize_rel(rel):
        return blob
    prefix = (blob_prefix or f"{normalize_rel(rel).rstrip('/')}/").rstrip("/")
    return f"{prefix}/{Path(file_rel).name}"


@dataclass
class VerifyOffloadResult:
    rel: str
    local_files: list[str]
    blob_keys: list[str]
    matched: list[str]
    local_only: list[str]
    blob_only: list[str]

    @property
    def safe_to_delete_local(self) -> bool:
        return bool(self.local_files) and not self.local_only and len(self.matched) == len(self.local_files)


def verify_offload(
    rel: str,
    cfg: ArchiveConfig,
    *,
    blob: str | None = None,
    blob_prefix: str | None = None,
) -> VerifyOffloadResult:
    rel = normalize_rel(rel)
    manifest = load_manifest()
    entry = find_entry(manifest, rel)
    blob = blob or (entry or {}).get("blob")
    blob_prefix = blob_prefix or (entry or {}).get("blob_prefix")

    local_files = sorted(file_rel for file_rel, _ in _iter_local_files(rel))

    if blob and not blob_prefix:
        prefix = blob.rsplit("/", 1)[0] + "/" if "/" in blob else ""
    else:
        prefix = (blob_prefix or f"{rel.rstrip('/')}/").rstrip("/") + "/"

    blob_keys = sorted(list_blob_keys(cfg, prefix.rstrip("/")))
    blob_set = set(blob_keys)

    matched: list[str] = []
    local_only: list[str] = []
    for file_rel in sorted(local_files):
        key = expected_blob_key(file_rel, rel=rel, blob=blob, blob_prefix=blob_prefix or prefix)
        if key in blob_set:
            matched.append(file_rel)
        else:
            local_only.append(file_rel)

    expected_keys = {
        expected_blob_key(file_rel, rel=rel, blob=blob, blob_prefix=blob_prefix or prefix)
        for file_rel in local_files
    }
    blob_only = sorted(key for key in blob_keys if key not in expected_keys)
    return VerifyOffloadResult(
        rel=rel,
        local_files=sorted(local_files),
        blob_keys=blob_keys,
        matched=sorted(matched),
        local_only=sorted(local_only),
        blob_only=blob_only,
    )


def format_verify_report(result: VerifyOffloadResult) -> str:
    lines = [
        f"verify: {result.rel}",
        f"  local files: {len(result.local_files)}",
        f"  blob objects: {len(result.blob_keys)}",
        f"  matched: {len(result.matched)}",
        f"  local only (not on blob): {len(result.local_only)}",
        f"  blob only (not local): {len(result.blob_only)}",
    ]
    if result.local_only:
        lines.append("  missing from blob:")
        for path in result.local_only[:20]:
            lines.append(f"    - {path}")
        if len(result.local_only) > 20:
            lines.append(f"    … and {len(result.local_only) - 20} more")
    if result.blob_only:
        lines.append("  on blob but not local:")
        for path in result.blob_only[:20]:
            lines.append(f"    - {path}")
        if len(result.blob_only) > 20:
            lines.append(f"    … and {len(result.blob_only) - 20} more")
    if result.safe_to_delete_local:
        lines.append("  safe to delete local: yes (all local files confirmed on blob)")
    else:
        lines.append("  safe to delete local: no")
    return "\n".join(lines)


class OffloadInterruptState:
    """Tracks partial offload state for SIGTERM flush."""

    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        progress: dict[str, Any],
        on_flush: Callable[[], None] | None = None,
    ) -> None:
        self.manifest = manifest
        self.progress = progress
        self.on_flush = on_flush
        self.flushed = False

    def flush(self) -> None:
        if self.flushed:
            return
        save_offload_progress(self.progress)
        if self.on_flush:
            self.on_flush()
        self.flushed = True
