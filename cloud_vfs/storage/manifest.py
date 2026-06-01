from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_vfs.project import manifest_path as default_manifest_path

from .io_util import atomic_write_json
from .paths import normalize_rel


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    p = path or default_manifest_path()
    if not p.exists():
        raise FileNotFoundError(f"Manifest not found: {p} (run cloud-vfs init)")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid manifest JSON at {p}: {exc}") from exc


def save_manifest(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or default_manifest_path()
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    atomic_write_json(p, data)


def find_entry(manifest: dict[str, Any], rel: str) -> dict[str, Any] | None:
    rel = normalize_rel(rel)
    best: dict[str, Any] | None = None
    best_len = -1
    for entry in manifest.get("entries", []):
        local = normalize_rel(entry.get("local", ""))
        if not local:
            continue
        if rel == local or rel.startswith(local.rstrip("/") + "/"):
            if len(local) > best_len:
                best = entry
                best_len = len(local)
    return best


def mark_fetched(entry: dict[str, Any]) -> None:
    if entry.get("status") == "offloaded-local-removed":
        entry["status"] = "synced"


def mark_offloaded(entry: dict[str, Any]) -> None:
    entry["status"] = "offloaded-local-removed"
    entry["uploaded"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_manifest_entry(
    manifest: dict[str, Any],
    rel: str,
    *,
    archive: str,
    provider: str,
    is_dir: bool,
    blob: str | None = None,
    blob_prefix: str | None = None,
) -> dict[str, Any]:
    entry = find_entry(manifest, rel)
    if entry:
        return entry
    entry = {
        "id": rel.replace("/", "-").strip("-"),
        "local": rel,
        "archive": archive,
        "provider": provider,
        "status": "synced",
    }
    if is_dir:
        entry["blob_prefix"] = blob_prefix or f"{rel.rstrip('/')}/"
    else:
        entry["blob"] = blob or rel
    manifest.setdefault("entries", []).append(entry)
    return entry
