from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cloud_vfs.project import inventory_policy_path, project_root
from cloud_vfs.storage.backends import list_blob_keys
from cloud_vfs.storage.config import ArchiveConfig, resolve_archive
from cloud_vfs.storage.env import load_cloud_env, normalize_archive
from cloud_vfs.storage.fetch import manifest_with_provider
from cloud_vfs.storage.io_util import atomic_write_json
from cloud_vfs.storage.manifest import find_entry, load_manifest
from cloud_vfs.storage.paths import STUB_NAME, abs_path, is_real_local, normalize_rel
from cloud_vfs.storage.errors import CloudVfsError
from cloud_vfs.storage.stub import STUB_TYPE_DIR, is_ref, read_stub, stub_placement, write_stub

DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "index_dir": ".cloud-vfs/index",
    "min_size_bytes": 52_428_800,
    "prefix_min_size_bytes": {},
    "include_prefixes": ["data/"],
    "exclude_prefixes": ["code/", "research/", ".cursor/", "infra/"],
    "committed_prefixes": [],
    "ephemeral_prefixes": ["data/generated/"],
}


def load_policy(path: Path | None = None) -> dict[str, Any]:
    p = path or inventory_policy_path()
    if not p.exists():
        return dict(DEFAULT_POLICY)
    return json.loads(p.read_text())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _prefix_matches(rel: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/") + "/"
    return rel == prefix.rstrip("/") or rel.startswith(prefix)


def in_scope(rel: str, policy: dict[str, Any]) -> bool:
    rel = normalize_rel(rel)
    includes = policy.get("include_prefixes") or ["data/"]
    excludes = policy.get("exclude_prefixes") or []
    if not any(_prefix_matches(rel, p) for p in includes):
        return False
    return not any(_prefix_matches(rel, p) for p in excludes)


def min_size_for(rel: str, policy: dict[str, Any]) -> int:
    rel = normalize_rel(rel)
    overrides = policy.get("prefix_min_size_bytes") or {}
    best_prefix = ""
    best_size = int(policy.get("min_size_bytes", DEFAULT_POLICY["min_size_bytes"]))
    for prefix, size in overrides.items():
        norm = prefix if prefix.endswith("/") else prefix + "/"
        if _prefix_matches(rel, norm) and len(norm) > len(best_prefix):
            best_prefix = norm
            best_size = int(size)
    return best_size


def should_index(rel: str, size: int, policy: dict[str, Any]) -> bool:
    return in_scope(rel, policy) and size >= min_size_for(rel, policy)


def shard_root_for(rel: str, entry: dict[str, Any] | None = None) -> str:
    rel = normalize_rel(rel)
    if entry and entry.get("local"):
        entry_local = normalize_rel(entry["local"])
        if Path(rel).suffix:
            if rel == entry_local or rel.startswith(entry_local.rstrip("/") + "/"):
                if Path(entry_local).suffix:
                    return str(Path(entry_local).parent)
                return entry_local
        else:
            return entry_local
    path = Path(rel)
    if path.suffix:
        parent = path.parent.as_posix()
        return parent if parent and parent != "." else rel
    return rel


def shard_path(shard_root: str, policy: dict[str, Any]) -> Path:
    index_dir = policy.get("index_dir", ".cloud-vfs/index")
    return project_root() / index_dir / f"{shard_root}.json"


def load_shard(shard_root: str, policy: dict[str, Any]) -> dict[str, Any]:
    path = shard_path(shard_root, policy)
    if not path.exists():
        return {
            "version": 1,
            "shard_root": shard_root,
            "updated_at": _now_iso(),
            "files": {},
        }
    data = json.loads(path.read_text())
    data.setdefault("files", {})
    data["shard_root"] = shard_root
    return data


def save_shard(shard: dict[str, Any], policy: dict[str, Any]) -> None:
    shard_root = shard["shard_root"]
    shard["updated_at"] = _now_iso()
    atomic_write_json(shard_path(shard_root, policy), shard)


def iter_inventory_rows(policy: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    index_root = project_root() / policy.get("index_dir", ".cloud-vfs/index")
    if not index_root.exists():
        return
    for path in sorted(index_root.rglob("*.json")):
        if path.name == "README.md":
            continue
        try:
            shard = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        shard_root = shard.get("shard_root") or path.relative_to(index_root).with_suffix("").as_posix()
        for local, row in (shard.get("files") or {}).items():
            yield shard_root, normalize_rel(local), row


def upsert_rows_batch(
    shard_root: str,
    rows: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> None:
    if not rows:
        return
    shard = load_shard(shard_root, policy)
    shard["files"].update(rows)
    save_shard(shard, policy)


def upsert_row(
    shard_root: str,
    local: str,
    row: dict[str, Any],
    policy: dict[str, Any],
) -> None:
    upsert_rows_batch(shard_root, {normalize_rel(local): row}, policy)


def remove_row(shard_root: str, local: str, policy: dict[str, Any]) -> bool:
    local = normalize_rel(local)
    shard = load_shard(shard_root, policy)
    if local not in shard["files"]:
        return False
    del shard["files"][local]
    if shard["files"]:
        save_shard(shard, policy)
    else:
        shard_path(shard_root, policy).unlink(missing_ok=True)
    return True


def find_row(local: str, policy: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    local = normalize_rel(local)
    for shard_root, row_local, row in iter_inventory_rows(policy):
        if row_local == local:
            return shard_root, row
    return None


def _row_base(
    local: str,
    *,
    archive: str,
    provider: str | None,
    blob: str | None,
    size: int,
    sha256: str | None,
    state: str,
    policy_id: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "local": local,
        "archive": archive,
        "state": state,
        "size": size,
        "updated_at": _now_iso(),
    }
    if provider:
        row["provider"] = provider
    if blob:
        row["blob"] = blob
    if sha256:
        row["sha256"] = sha256
    if policy_id:
        row["policy_id"] = policy_id
    return row


def _iter_local_files(root_rel: str) -> Iterator[tuple[str, Path]]:
    root = abs_path(root_rel)
    if root.is_file():
        if root.name != STUB_NAME:
            yield normalize_rel(root_rel), root
        return
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == STUB_NAME:
            continue
        rel = normalize_rel(path.relative_to(project_root()))
        yield rel, path


def register_paths(paths: list[str]) -> tuple[int, int]:
    policy = load_policy()
    manifest = load_manifest()
    indexed = 0
    skipped = 0
    for raw in paths:
        root_rel = normalize_rel(raw)
        if not abs_path(root_rel).exists():
            skipped += 1
            continue
        entry = find_entry(manifest, root_rel)
        for rel, path in _iter_local_files(root_rel):
            size = path.stat().st_size
            if not should_index(rel, size, policy):
                skipped += 1
                continue
            digest = sha256_file(path)
            shard_root = shard_root_for(rel, entry)
            row = _row_base(
                rel,
                archive=(entry or {}).get("archive", "local_archive"),
                provider=(entry or {}).get("provider"),
                blob=rel,
                size=size,
                sha256=digest,
                state="local",
                policy_id=(entry or {}).get("id"),
            )
            upsert_row(shard_root, rel, row, policy)
            indexed += 1
    return indexed, skipped


def index_offloaded_path(
    rel: str,
    *,
    archive: str,
    provider: str,
    blob: str | None,
    blob_prefix: str | None,
    entry: dict[str, Any] | None,
    precomputed: dict[str, str] | None = None,
    keep_local: bool = False,
) -> int:
    policy = load_policy()
    rel = normalize_rel(rel)
    indexed = 0
    src = abs_path(rel)

    if precomputed:
        file_items = list(precomputed.items())
    elif src.is_file() and not is_ref(rel):
        file_items = [(rel, sha256_file(src))]
    elif src.is_dir():
        file_items = [(file_rel, sha256_file(path)) for file_rel, path in _iter_local_files(rel)]
    else:
        return 0

    shard_batches: dict[str, dict[str, dict[str, Any]]] = {}
    for file_rel, digest in file_items:
        path = abs_path(file_rel)
        size = path.stat().st_size if path.exists() and not is_ref(file_rel) else 0
        if not size and entry:
            existing = find_row(file_rel, policy)
            if existing:
                size = int(existing[1].get("size") or 0)
        if not should_index(file_rel, size, policy):
            continue
        file_blob = blob or (f"{blob_prefix.rstrip('/')}/{Path(file_rel).name}" if blob_prefix else file_rel)
        shard_root = shard_root_for(file_rel, entry)
        row = _row_base(
            file_rel,
            archive=archive,
            provider=provider,
            blob=file_blob,
            size=size,
            sha256=digest,
            state="local" if keep_local else "cloud-only",
            policy_id=(entry or {}).get("id"),
        )
        row["uploaded_at"] = _now_iso()
        shard_batches.setdefault(shard_root, {})[file_rel] = row
        indexed += 1

    for shard_root, rows in shard_batches.items():
        upsert_rows_batch(shard_root, rows, policy)
    return indexed


def hash_paths_before_offload(rel: str) -> dict[str, str]:
    rel = normalize_rel(rel)
    policy = load_policy()
    out: dict[str, str] = {}
    for file_rel, path in _iter_local_files(rel):
        size = path.stat().st_size
        if should_index(file_rel, size, policy):
            out[file_rel] = sha256_file(path)
    return out


def prune_inventory() -> tuple[int, int]:
    policy = load_policy()
    removed = 0
    kept = 0
    for shard_root, local, row in list(iter_inventory_rows(policy)):
        size = int(row.get("size") or 0)
        if should_index(local, size, policy):
            kept += 1
            continue
        remove_row(shard_root, local, policy)
        removed += 1
    return removed, kept


class VerifyError(CloudVfsError):
    """Fetched bytes do not match inventory sha256."""


def verify_fetched_tree(rel: str, policy: dict[str, Any] | None = None) -> None:
    """Raise VerifyError if local bytes disagree with inventory sha256 after ensure."""
    policy = policy or load_policy()
    rel = normalize_rel(rel)
    mismatches: list[str] = []
    for file_rel, path in _iter_local_files(rel):
        found = find_row(file_rel, policy)
        if not found:
            continue
        _, row = found
        expected = row.get("sha256")
        if not expected:
            continue
        actual = sha256_file(path)
        if actual != expected:
            mismatches.append(f"{file_rel} (expected {expected[:12]}…, got {actual[:12]}…)")
    if mismatches:
        raise VerifyError(
            "Fetch verify failed — local sha256 does not match inventory:\n  "
            + "\n  ".join(mismatches)
        )


def list_orphan_blobs() -> list[dict[str, Any]]:
    """Blobs in the configured cloud-vfs bucket/prefix with no inventory row."""
    policy = load_policy()
    manifest = load_manifest()
    env = load_cloud_env()
    return _unregistered_cloud(policy, manifest, env)


def repair_stubs() -> int:
    """Rewrite missing stubs/inline refs from manifest + inventory (e2fsck-style repair)."""
    manifest = load_manifest()
    policy = load_policy()
    repaired = 0

    for entry in manifest.get("entries", []):
        if entry.get("status") != "offloaded-local-removed":
            continue
        rel = normalize_rel(entry.get("local", ""))
        if not rel or is_real_local(rel):
            continue
        meta = {
            "manifest_id": entry.get("id"),
            "archive": entry.get("archive", "local_archive"),
            "blob": entry.get("blob"),
            "blob_prefix": entry.get("blob_prefix"),
        }
        if read_stub(rel):
            continue
        write_stub(rel, meta)
        repaired += 1

    for _shard, local, row in iter_inventory_rows(policy):
        if row.get("state") != "cloud-only":
            continue
        if is_real_local(local) or read_stub(local):
            continue
        entry = find_entry(manifest, local)
        meta: dict[str, Any] = {
            "archive": row.get("archive", "local_archive"),
            "blob": row.get("blob"),
        }
        if entry:
            meta["manifest_id"] = entry.get("id")
            meta["blob_prefix"] = entry.get("blob_prefix")
        write_stub(local, meta)
        repaired += 1

    return repaired


def detect_drift(*, check_blob: bool = False) -> list[dict[str, Any]]:
    policy = load_policy()
    manifest = load_manifest()
    env = load_cloud_env()
    issues: list[dict[str, Any]] = []
    indexed_locals = {local for _, local, _ in iter_inventory_rows(policy)}

    for rel, path in _walk_scoped_files(policy):
        if not in_scope(rel, policy):
            continue
        size = path.stat().st_size
        if size < min_size_for(rel, policy):
            continue
        if rel in indexed_locals:
            row = find_row(rel, policy)
            if row and is_real_local(rel):
                digest = sha256_file(path)
                if row[1].get("sha256") and row[1]["sha256"] != digest:
                    issues.append({"type": "hash-mismatch", "path": rel})
            continue
        issues.append({"type": "orphan-local", "path": rel, "size": size})

    for shard_root, local, row in iter_inventory_rows(policy):
        state = row.get("state")
        stub = read_stub(local)
        entry = find_entry(manifest, local)
        if state == "cloud-only":
            if is_real_local(local):
                issues.append({"type": "local-index-mismatch", "path": local})
            elif Path(local).suffix == "" and not stub:
                issues.append({"type": "stale-stub", "path": local})
            elif stub:
                inv_blob = row.get("blob")
                stub_blob = stub.get("blob")
                if inv_blob and stub_blob and inv_blob != stub_blob:
                    issues.append(
                        {
                            "type": "ref-inventory-mismatch",
                            "path": local,
                            "inventory_blob": inv_blob,
                            "stub_blob": stub_blob,
                        }
                    )
            if check_blob:
                archive = normalize_archive(row.get("archive", "local_archive"))
                prov = row.get("provider") or (entry or {}).get("provider")
                mcfg = manifest_with_provider(manifest, archive, prov)
                try:
                    cfg = resolve_archive(env, mcfg, archive)
                except (KeyError, ValueError):
                    issues.append({"type": "ghost-index", "path": local, "reason": "missing archive config"})
                    continue
                blob = row.get("blob")
                if blob and not _blob_exists(cfg, blob):
                    issues.append({"type": "ghost-index", "path": local, "blob": blob})
        elif state == "local":
            if is_ref(local):
                issues.append({"type": "stale-inline-ref", "path": local})
            elif not is_real_local(local) and not stub:
                issues.append({"type": "ghost-local", "path": local})

    if check_blob:
        issues.extend(_unregistered_cloud(policy, manifest, env))
    return issues


def _walk_scoped_files(policy: dict[str, Any]) -> Iterator[tuple[str, Path]]:
    root = project_root()
    seen: set[str] = set()
    for prefix in policy.get("include_prefixes") or ["data/"]:
        base = root / prefix.rstrip("/")
        if not base.exists():
            continue
        if base.is_file():
            rel = normalize_rel(base.relative_to(root))
            if rel not in seen:
                seen.add(rel)
                yield rel, base
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.name == STUB_NAME:
                continue
            rel = normalize_rel(path.relative_to(root))
            if rel in seen or not in_scope(rel, policy):
                continue
            if is_ref(rel):
                continue
            seen.add(rel)
            yield rel, path


def _blob_exists(cfg: ArchiveConfig, blob: str) -> bool:
    keys = list_blob_keys(cfg, blob.rsplit("/", 1)[0] + "/" if "/" in blob else "")
    if blob in keys:
        return True
    return any(k == blob or k.endswith("/" + blob) for k in keys)


def _unregistered_cloud(
    policy: dict[str, Any],
    manifest: dict[str, Any],
    env: dict[str, str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    indexed_blobs = {row.get("blob") for _, _, row in iter_inventory_rows(policy) if row.get("blob")}
    for entry in manifest.get("entries", []):
        prefix = entry.get("blob_prefix")
        if not prefix:
            continue
        archive = normalize_archive(entry.get("archive", "local_archive"))
        prov = entry.get("provider")
        mcfg = manifest_with_provider(manifest, archive, prov)
        try:
            cfg = resolve_archive(env, mcfg, archive)
        except (KeyError, ValueError):
            continue
        for key in list_blob_keys(cfg, prefix):
            if key in indexed_blobs:
                continue
            path = normalize_rel(key)
            if not in_scope(path, policy):
                continue
            issues.append({"type": "orphan-blob", "path": path, "blob": key})
    return issues


def mark_inventory_fetched(local: str) -> None:
    policy = load_policy()
    local = normalize_rel(local)
    target = abs_path(local)
    if target.is_dir():
        mark_inventory_fetched_tree(local, policy=policy)
        return
    found = find_row(local, policy)
    if not found:
        return
    shard_root, row = found
    path = abs_path(local)
    if path.is_file() and is_real_local(local):
        row["state"] = "local"
        row["sha256"] = sha256_file(path)
        row["updated_at"] = _now_iso()
        upsert_row(shard_root, local, row, policy)


def mark_inventory_fetched_tree(rel: str, *, policy: dict[str, Any] | None = None) -> None:
    policy = policy or load_policy()
    rel = normalize_rel(rel)
    shard_batches: dict[str, dict[str, dict[str, Any]]] = {}
    for file_rel, path in _iter_local_files(rel):
        found = find_row(file_rel, policy)
        if not found or not is_real_local(file_rel):
            continue
        shard_root, row = found
        row["state"] = "local"
        row["sha256"] = sha256_file(path)
        row["updated_at"] = _now_iso()
        shard_batches.setdefault(shard_root, {})[file_rel] = row
    for shard_root, rows in shard_batches.items():
        upsert_rows_batch(shard_root, rows, policy)


def rebuild_index_from_blob(
    prefix: str,
    *,
    archive: str = "local_archive",
    provider: str | None = None,
) -> int:
    policy = load_policy()
    manifest = load_manifest()
    env = load_cloud_env()
    prefix = normalize_rel(prefix).rstrip("/") + "/"
    mcfg = manifest_with_provider(manifest, archive, provider)
    cfg = resolve_archive(env, mcfg, archive)
    entry = find_entry(manifest, prefix.rstrip("/"))
    added = 0
    for key in list_blob_keys(cfg, prefix):
        rel = normalize_rel(key)
        if not in_scope(rel, policy):
            continue
        shard_root = shard_root_for(rel, entry)
        row = _row_base(
            rel,
            archive=archive,
            provider=cfg.provider,
            blob=key,
            size=0,
            sha256=None,
            state="cloud-only",
            policy_id=(entry or {}).get("id"),
        )
        upsert_row(shard_root, rel, row, policy)
        added += 1
    return added
