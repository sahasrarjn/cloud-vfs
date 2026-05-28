from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_vfs.project import manifest_path as default_manifest_path

from .paths import normalize_rel


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    p = path or default_manifest_path()
    return json.loads(p.read_text())


def save_manifest(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or default_manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p.write_text(json.dumps(data, indent=2) + "\n")


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
