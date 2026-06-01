from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root
from cloud_vfs.storage.io_util import atomic_write_json
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
    except (json.JSONDecodeError, OSError):
        return None
    expected = sorted(normalize_rel(p) for p in paths)
    if sorted(data.get("paths") or []) != expected:
        return None
    return data


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
    entry["status"] = status
    entry["updated_at"] = _now_iso()
    if error:
        entry["error"] = error
    elif "error" in entry:
        del entry["error"]
    save_offload_job(job)


def format_job_summary(job: dict[str, Any]) -> str:
    entries = job.get("entries") or {}
    counts: dict[str, int] = {}
    for entry in entries.values():
        status = entry.get("status", STATUS_PENDING)
        counts[status] = counts.get(status, 0) + 1
    total = len(job.get("paths") or [])
    parts = [f"{counts.get(k, 0)} {k}" for k in (STATUS_STUBBED, STATUS_SKIPPED, STATUS_FAILED, STATUS_PENDING)]
    summary = ", ".join(p for p in parts if not p.startswith("0 "))
    return f"batch offload: {total} path(s) — {summary or 'no paths'}"


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
