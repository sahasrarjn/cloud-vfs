from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_vfs.project import fetch_cmd

from .env import archive_from_entry
from .paths import STUB_NAME, abs_path, normalize_rel, stub_file_for

CVFS_MARKER = 1
STUB_TYPE_BLOB = "cloud-blob-ref"
STUB_TYPE_DIR = "cloud-dir-ref"
STUB_VERSION = 2
REF_TYPES = (STUB_TYPE_BLOB, STUB_TYPE_DIR)

# Inline refs are small JSON; multi-GB .npy/.pkl must never be read as text.
MAX_REF_FILE_BYTES = 65_536


def _is_file_rel(rel: str) -> bool:
    return bool(Path(normalize_rel(rel)).suffix)


def parse_ref_text(text: str) -> dict[str, Any] | None:
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if data.get("cvfs") != CVFS_MARKER:
        return None
    if data.get("type") not in REF_TYPES:
        return None
    return data


def is_ref_path(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size > MAX_REF_FILE_BYTES:
            return False
        with path.open("rb") as handle:
            data = handle.read(MAX_REF_FILE_BYTES)
        if not data.lstrip().startswith(b"{"):
            return False
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return parse_ref_text(text) is not None
    except (OSError, ValueError):
        return False


def is_ref(rel: str) -> bool:
    return is_ref_path(abs_path(normalize_rel(rel)))


def _legacy_sidecar_paths(rel: str) -> list[Path]:
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
    out: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def read_stub(rel: str) -> dict[str, Any] | None:
    rel = normalize_rel(rel)
    inline = abs_path(rel)
    if inline.is_file() and is_ref_path(inline):
        return parse_ref_text(inline.read_text())

    for path in _legacy_sidecar_paths(rel):
        if not path.exists() or path == inline:
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ref_type = data.get("type")
        if ref_type in REF_TYPES or ref_type == STUB_TYPE_BLOB:
            if data.get("cvfs") is None and ref_type == STUB_TYPE_BLOB:
                data = {**data, "cvfs": CVFS_MARKER}
            return data
    return None


def _ref_payload(rel: str, meta: dict[str, Any], *, placement: str, ref_type: str) -> dict[str, Any]:
    rel = normalize_rel(rel)
    payload: dict[str, Any] = {
        "cvfs": CVFS_MARKER,
        "type": ref_type,
        "version": STUB_VERSION,
        "placement": placement,
        "local": rel,
        "fetch_cmd": fetch_cmd(rel),
        **meta,
    }
    payload.pop("placement", None)
    payload["placement"] = placement
    return payload


def write_stub(rel: str, meta: dict[str, Any]) -> Path:
    rel = normalize_rel(rel)
    if _is_file_rel(rel):
        return write_inline_ref(rel, meta)

    stub_path = stub_file_for(rel)
    stub_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _ref_payload(rel, meta, placement="sidecar", ref_type=STUB_TYPE_DIR)
    if meta.get("blob_prefix"):
        payload.setdefault("shard_root", rel)
        payload.setdefault("index", f".cloud-vfs/index/{rel}.json")
    stub_path.write_text(json.dumps(payload, indent=2) + "\n")
    _remove_legacy_file_sidecars(rel)
    return stub_path


def write_inline_ref(rel: str, meta: dict[str, Any]) -> Path:
    rel = normalize_rel(rel)
    ref_path = abs_path(rel)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _ref_payload(rel, meta, placement="inline", ref_type=STUB_TYPE_BLOB)
    if not payload.get("blob") and not payload.get("blob_prefix"):
        payload["blob"] = rel
    ref_path.write_text(json.dumps(payload, indent=2) + "\n")
    _remove_legacy_file_sidecars(rel)
    return ref_path


def migrate_legacy_file_sidecar(rel: str) -> Path | None:
    rel = normalize_rel(rel)
    if not _is_file_rel(rel) or is_ref(rel):
        return None
    stub = read_stub(rel)
    if not stub:
        return None
    sidecar = next((p for p in _legacy_sidecar_paths(rel) if p.exists() and p != abs_path(rel)), None)
    if sidecar is None:
        return None
    meta = {k: v for k, v in stub.items() if k not in ("cvfs", "type", "version", "placement", "local", "fetch_cmd")}
    inline = write_inline_ref(rel, meta)
    sidecar.unlink(missing_ok=True)
    return inline


def _remove_legacy_file_sidecars(rel: str) -> None:
    if not _is_file_rel(rel):
        return
    inline = abs_path(rel)
    for path in _legacy_sidecar_paths(rel):
        if path.exists() and path != inline:
            path.unlink()


def remove_stub(rel: str) -> None:
    rel = normalize_rel(rel)
    inline = abs_path(rel)
    if inline.is_file() and is_ref_path(inline):
        inline.unlink()
    for path in _legacy_sidecar_paths(rel):
        if path.exists():
            path.unlink()
    if not _is_file_rel(rel):
        sidecar = abs_path(rel) / STUB_NAME
        if sidecar.exists():
            sidecar.unlink()


def stub_placement(rel: str) -> str | None:
    stub = read_stub(rel)
    if not stub:
        return None
    return stub.get("placement") or ("inline" if _is_file_rel(rel) else "sidecar")


def resolve_meta(rel: str, entry: dict[str, Any] | None) -> dict[str, Any]:
    stub = read_stub(rel)
    if stub:
        return stub
    if not entry:
        raise FileNotFoundError(f"No manifest entry or stub for {rel}")
    meta: dict[str, Any] = {
        "manifest_id": entry.get("id"),
        "archive": archive_from_entry(entry),
    }
    if entry.get("blob"):
        meta["blob"] = entry["blob"]
    if entry.get("blob_prefix"):
        meta["blob_prefix"] = entry["blob_prefix"]
    return meta
