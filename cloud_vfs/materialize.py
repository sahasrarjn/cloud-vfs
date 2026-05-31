from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root
from cloud_vfs.storage.env import archive_from_entry, load_cloud_env, normalize_archive
from cloud_vfs.storage.errors import CloudStorageError, CloudVfsError, PathOutsideProjectError
from cloud_vfs.storage.fetch import fetch_path, manifest_with_provider, upload_path
from cloud_vfs.storage.inventory import (
    find_row,
    load_policy,
    sha256_file,
    shard_root_for,
    should_index,
    upsert_row,
    _row_base,
)
from cloud_vfs.storage.manifest import (
    ensure_manifest_entry,
    find_entry,
    load_manifest,
    mark_offloaded,
    save_manifest,
)
from cloud_vfs.storage.paths import is_real_local, normalize_rel
from cloud_vfs.storage.stub import is_ref, read_stub, resolve_meta, write_stub


def fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _print_error(exc: Exception) -> None:
    print(f"ERROR: {exc}", file=sys.stderr)


def load_items_from_paths_file(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def resolve_materialize_meta(
    rel: str,
    manifest: dict[str, Any] | None,
    *,
    source_archive: str | None = None,
) -> dict[str, Any]:
    rel = normalize_rel(rel)
    stub = read_stub(rel)
    if stub and (stub.get("blob") or stub.get("blob_prefix")):
        meta = dict(stub)
        if source_archive:
            meta["archive"] = normalize_archive(source_archive)
        return meta

    if manifest:
        entry = find_entry(manifest, rel)
        if entry and (entry.get("blob") or entry.get("blob_prefix")):
            meta = resolve_meta(rel, entry)
            if source_archive:
                meta["archive"] = normalize_archive(source_archive)
            return meta

    raise FileNotFoundError(
        f"No cvfs ref or manifest blob mapping for {rel}. "
        "Provide inline refs, --manifest, or --paths-file."
    )


def paths_needing_materialize(paths: list[str]) -> list[str]:
    policy = load_policy()
    pending: list[str] = []
    for raw in paths:
        rel = normalize_rel(raw)
        if is_ref(rel) or read_stub(rel):
            pending.append(rel)
            continue
        if not is_real_local(rel):
            found = find_row(rel, policy)
            if found and found[1].get("state") == "cloud-only":
                pending.append(rel)
    return pending


def cmd_preflight(paths: list[str], *, as_json: bool) -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError):
        manifest = None

    expanded: list[str] = []
    for raw in paths:
        try:
            rel = normalize_rel(raw)
        except PathOutsideProjectError as exc:
            _print_error(exc)
            return 1
        if manifest:
            import cloud_vfs.cli as cli_mod

            expanded.extend(cli_mod._ensure_targets(rel, manifest))
        else:
            expanded.append(rel)

    pending = paths_needing_materialize(expanded)
    if as_json:
        print(json.dumps({"ok": not pending, "pending": pending}, indent=2))
    else:
        if not pending:
            print(f"OK: {len(expanded)} path(s) materialized")
        else:
            print(f"PREFLIGHT FAILED: {len(pending)} path(s) need materialization:")
            for rel in pending:
                stub = read_stub(rel)
                archive = (stub or {}).get("archive", "local_archive")
                print(
                    f"  {rel}  (run: cloud-vfs ensure {rel}"
                    + (f" --source {archive}" if archive != "local_archive" else "")
                    + ")"
                )
    return 1 if pending else 0


def cmd_ensure_at_target(
    paths: list[str],
    *,
    target_root: Path,
    source_archive: str | None,
    manifest_file: Path | None,
    paths_file: Path | None,
    config_env: Path | None,
    secrets_env: Path | None,
    ref_root: Path | None,
) -> int:
    """Fetch cloud source blobs into paths under target_root (no project inventory required)."""
    prev_root = os.environ.get("CLOUD_VFS_PROJECT_ROOT")
    if ref_root:
        os.environ["CLOUD_VFS_PROJECT_ROOT"] = str(ref_root.resolve())
        project_root.cache_clear()

    try:
        rel_paths = list(paths)
        if paths_file:
            rel_paths.extend(load_items_from_paths_file(paths_file))

        manifest: dict[str, Any] | None = None
        if manifest_file:
            manifest = load_manifest(manifest_file)
        else:
            try:
                manifest = load_manifest()
            except (FileNotFoundError, ValueError):
                manifest = None

        if not rel_paths:
            _print_error(
                FileNotFoundError("No paths: pass path arguments and/or --paths-file")
            )
            return 1

        env = load_cloud_env(config=config_env, secrets=secrets_env)
        target_root = target_root.resolve()
        target_root.mkdir(parents=True, exist_ok=True)

        for raw in rel_paths:
            try:
                rel = normalize_rel(raw)
            except PathOutsideProjectError as exc:
                _print_error(exc)
                return 1
            try:
                meta = resolve_materialize_meta(
                    rel, manifest, source_archive=source_archive
                )
            except FileNotFoundError as exc:
                _print_error(exc)
                return 1

            use_source = normalize_archive(
                source_archive or meta.get("archive") or "local_archive"
            )
            prov = meta.get("provider")
            mcfg = manifest_with_provider(manifest or {}, use_source, prov)
            dest = target_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            print(f"fetch: source={use_source} target={dest}")
            try:
                nbytes = fetch_path(
                    meta,
                    rel,
                    use_source,
                    env,
                    mcfg,
                    dest=dest,
                    dest_root=target_root,
                    progress_label=f"[cloud-vfs ensure] {rel} -> {target_root}",
                )
            except (CloudStorageError, FileNotFoundError, OSError) as exc:
                _print_error(exc)
                return 1
            print(f"OK: {dest} ({fmt_bytes(nbytes)})")
        return 0
    finally:
        if ref_root:
            if prev_root is None:
                os.environ.pop("CLOUD_VFS_PROJECT_ROOT", None)
            else:
                os.environ["CLOUD_VFS_PROJECT_ROOT"] = prev_root
            project_root.cache_clear()


def index_ingested_file(
    target_rel: str,
    *,
    archive: str,
    provider: str,
    blob: str,
    source_path: Path,
    entry: dict[str, Any],
) -> bool:
    policy = load_policy()
    target_rel = normalize_rel(target_rel)
    size = source_path.stat().st_size
    if not should_index(target_rel, size, policy):
        return False
    digest = sha256_file(source_path)
    shard_root = shard_root_for(target_rel, entry)
    row = _row_base(
        target_rel,
        archive=archive,
        provider=provider,
        blob=blob,
        size=size,
        sha256=digest,
        state="cloud-only",
        policy_id=entry.get("id"),
    )
    row["uploaded_at"] = row["updated_at"]
    upsert_row(shard_root, target_rel, row, policy)
    return True


def cmd_ingest(
    source_path: Path,
    target_rel: str,
    *,
    source_archive: str,
    dry_run: bool,
    emit_stub: bool,
    index_inventory: bool,
) -> int:
    try:
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Source is not a file: {source_path}")
        target_rel = normalize_rel(target_rel)
        manifest = load_manifest()
        use_source = normalize_archive(source_archive)
        env = load_cloud_env()
        entry = ensure_manifest_entry(
            manifest,
            target_rel,
            archive=use_source,
            provider="",
            is_dir=False,
            blob=target_rel,
        )
        prov = entry.get("provider")
        mcfg = manifest_with_provider(manifest, use_source, prov)
        from cloud_vfs.storage.fetch import resolve_archive

        cfg = resolve_archive(env, mcfg, use_source)
        entry["provider"] = cfg.provider

        if dry_run:
            size = source_path.stat().st_size
            print(
                f"would ingest: source={source_path} -> "
                f"target={cfg.provider}/{use_source}:{target_rel} ({fmt_bytes(size)})"
            )
            return 0

        print(
            f"ingest: source={source_path} -> target={cfg.provider}/{use_source}:{target_rel}"
        )
        try:
            upload_path(
                target_rel,
                use_source,
                env,
                mcfg,
                progress_label=f"[cloud-vfs ingest] {target_rel}",
                source_path=source_path,
            )
        except (CloudStorageError, ValueError, FileNotFoundError) as exc:
            _print_error(exc)
            return 1

        if index_inventory:
            index_ingested_file(
                target_rel,
                archive=use_source,
                provider=cfg.provider,
                blob=target_rel,
                source_path=source_path,
                entry=entry,
            )

        if emit_stub:
            mark_offloaded(entry)
            meta = {
                "manifest_id": entry.get("id"),
                "archive": use_source,
                "provider": cfg.provider,
                "blob": target_rel,
            }
            write_stub(target_rel, meta)
            target = project_root() / target_rel
            if target.exists() and is_real_local(target_rel):
                target.unlink()

        save_manifest(manifest)
        print(f"OK: ingested target={target_rel} ({fmt_bytes(source_path.stat().st_size)})")
        return 0
    except (FileNotFoundError, ValueError, PathOutsideProjectError, CloudVfsError) as exc:
        _print_error(exc)
        return 1
