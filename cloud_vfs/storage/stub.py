from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_vfs.project import fetch_cmd

from .paths import STUB_NAME, abs_path, normalize_rel, stub_file_for

STUB_TYPE = "cloud-blob-ref"
STUB_VERSION = 1


def write_stub(rel: str, meta: dict[str, Any]) -> Path:
    rel = normalize_rel(rel)
    stub_path = stub_file_for(rel)
    stub_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": STUB_TYPE,
        "version": STUB_VERSION,
        "local": rel,
        "fetch_cmd": fetch_cmd(rel),
        **meta,
    }
    stub_path.write_text(json.dumps(payload, indent=2) + "\n")
    if not Path(rel).suffix:
        legacy = abs_path(rel).parent / f"{Path(rel).name}{STUB_NAME}"
        if legacy.exists() and legacy != stub_path:
            legacy.unlink()
    return stub_path


def read_stub(rel: str) -> dict[str, Any] | None:
    rel = normalize_rel(rel)
    p = Path(rel)
    candidates = [
        stub_file_for(rel),
        abs_path(rel) / STUB_NAME,
        Path(f"{abs_path(rel)}{STUB_NAME}"),
    ]
    if not p.suffix:
        candidates.append(abs_path(rel).parent / f"{p.name}{STUB_NAME}")
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("type") == STUB_TYPE:
            return data
    return None


def remove_stub(rel: str) -> None:
    rel = normalize_rel(rel)
    for path in (stub_file_for(rel), abs_path(rel) / STUB_NAME):
        if path.exists():
            path.unlink()


def resolve_meta(rel: str, entry: dict[str, Any] | None) -> dict[str, Any]:
    stub = read_stub(rel)
    if stub:
        return stub
    if not entry:
        raise FileNotFoundError(f"No manifest entry or stub for {rel}")
    meta: dict[str, Any] = {
        "manifest_id": entry.get("id"),
        "archive": entry.get("archive", "local_archive"),
    }
    if entry.get("blob"):
        meta["blob"] = entry["blob"]
    if entry.get("blob_prefix"):
        meta["blob_prefix"] = entry["blob_prefix"]
    return meta
