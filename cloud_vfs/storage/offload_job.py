from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root
from cloud_vfs.storage.io_util import atomic_write_json
from cloud_vfs.storage.offload_progress import clear_offload_progress
from cloud_vfs.storage.paths import normalize_rel

JOB_VERSION = 1
STATUS_PENDING = "pending"
STATUS_STUBBED = "stubbed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jobs_dir() -> Path:
    path = project_root() / ".cloud-vfs" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_fingerprint(paths: list[str]) -> str:
    normalized = sorted(normalize_rel(p) for p in paths)
    digest = hashlib.sha256("\n".join(normalized).encode()).hexdigest()
    return digest[:16]


def job_file(paths: list[str]) -> Path:
    return jobs_dir() / f"offload-{job_fingerprint(paths)}.json"


def load_offload_job(paths: list[str]) -> dict[str, Any] | None:
    path = job_file(paths)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[cloud-vfs offload] ignoring corrupt job file {path.name}: {exc}",
            file=sys.stderr,
        )
        return None
    expected = sorted(normalize_rel(p) for p in paths)
    if sorted(data.get("paths") or []) != expected:
        print(
            f"[cloud-vfs offload] ignoring stale job file {path.name} (path list changed)",
            file=sys.stderr,
        )
        return None
    if data.get("version") != JOB_VERSION:
        print(
            f"[cloud-vfs offload] ignoring unsupported job version in {path.name}",
            file=sys.stderr,
        )
        return None
    return data


def clear_job_offload_progress(paths: list[str]) -> None:
    for rel in paths:
        clear_offload_progress(normalize_rel(rel))


def save_offload_job(job: dict[str, Any]) -> None:
    job["updated_at"] = _now_iso()
    atomic_write_json(job_file(job["paths"]), job)


def new_offload_job(
    paths: list[str],
    *,
    archive: str,
    delete_local: bool,
) -> dict[str, Any]:
    normalized = sorted(normalize_rel(p) for p in paths)
    entries = {rel: {"status": STATUS_PENDING, "updated_at": _now_iso()} for rel in normalized}
    return {
        "version": JOB_VERSION,
        "paths": normalized,
        "archive": archive,
        "delete_local": delete_local,
        "entries": entries,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def set_job_path_status(job: dict[str, Any], rel: str, status: str, *, error: str | None = None) -> None:
    rel = normalize_rel(rel)
    entry = job.setdefault("entries", {}).setdefault(rel, {})
    current = entry.get("status")
    if status == STATUS_SKIPPED and current in (STATUS_STUBBED, STATUS_FAILED):
        return
    entry["status"] = status
    entry["updated_at"] = _now_iso()
    if error:
        entry["error"] = error
    elif "error" in entry:
        del entry["error"]
    save_offload_job(job)


def mark_job_skipped_if_pending(job: dict[str, Any], rel: str) -> None:
    rel = normalize_rel(rel)
    entry = (job.get("entries") or {}).get(rel) or {}
    if entry.get("status", STATUS_PENDING) == STATUS_PENDING:
        set_job_path_status(job, rel, STATUS_SKIPPED)


def format_job_summary(job: dict[str, Any]) -> str:
    entries = job.get("entries") or {}
    counts: dict[str, int] = {}
    for entry in entries.values():
        status = entry.get("status", STATUS_PENDING)
        counts[status] = counts.get(status, 0) + 1
    total = len(job.get("paths") or [])
    parts = [f"{counts.get(k, 0)} {k}" for k in (STATUS_STUBBED, STATUS_SKIPPED, STATUS_FAILED, STATUS_PENDING)]
    counts_text = ", ".join(p for p in parts if not p.startswith("0 "))
    failed = [
        rel
        for rel, entry in (job.get("entries") or {}).items()
        if (entry or {}).get("status") == STATUS_FAILED
    ]
    summary = f"batch offload: {total} path(s) — {counts_text or 'no paths'}"
    if failed:
        detail = ", ".join(
            f"{rel}: {(job['entries'][rel] or {}).get('error', 'failed')}" for rel in failed[:3]
        )
        if len(failed) > 3:
            detail += f", … and {len(failed) - 3} more"
        summary += f" ({detail})"
    return summary


def job_has_failures(job: dict[str, Any]) -> bool:
    return any(
        (entry or {}).get("status") == STATUS_FAILED
        for entry in (job.get("entries") or {}).values()
    )


def job_has_pending(job: dict[str, Any]) -> bool:
    return any(
        (entry or {}).get("status") == STATUS_PENDING
        for entry in (job.get("entries") or {}).values()
    )
