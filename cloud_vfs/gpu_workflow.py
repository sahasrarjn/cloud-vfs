from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root
from cloud_vfs.storage.env import (
    ARCHIVE_ROLE_LABELS,
    archive_context_hints,
    archive_from_entry,
    load_cloud_env,
    normalize_archive,
)
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


def resolve_archive_role(archive: str) -> dict[str, str]:
    archive = normalize_archive(archive)
    return {
        "archive": archive,
        "role": ARCHIVE_ROLE_LABELS.get(archive, archive),
    }


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
    archive_override: str | None = None,
) -> dict[str, Any]:
    rel = normalize_rel(rel)
    stub = read_stub(rel)
    if stub and (stub.get("blob") or stub.get("blob_prefix")):
        meta = dict(stub)
        if archive_override:
            meta["archive"] = normalize_archive(archive_override)
        return meta

    if manifest:
        entry = find_entry(manifest, rel)
        if entry and (entry.get("blob") or entry.get("blob_prefix")):
            meta = resolve_meta(rel, entry)
            if archive_override:
                meta["archive"] = normalize_archive(archive_override)
            return meta

    raise FileNotFoundError(
        f"No cvfs ref or manifest blob mapping for {rel}. "
        "Sync git refs, pass --manifest, or use a paths file from the Mac catalog."
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
                print(f"  {rel}  (run: cloud-vfs ensure {rel}, archive={archive})")
    return 1 if pending else 0


def cmd_ensure_remote(
    paths: list[str],
    *,
    dest_root: Path,
    archive: str,
    manifest_file: Path | None,
    paths_file: Path | None,
    config_env: Path | None,
    secrets_env: Path | None,
    project_root_override: Path | None,
) -> int:
    import os

    prev_root = os.environ.get("CLOUD_VFS_PROJECT_ROOT")
    if project_root_override:
        os.environ["CLOUD_VFS_PROJECT_ROOT"] = str(project_root_override.resolve())
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
        dest_root = dest_root.resolve()
        dest_root.mkdir(parents=True, exist_ok=True)

        for raw in rel_paths:
            try:
                rel = normalize_rel(raw)
            except PathOutsideProjectError as exc:
                _print_error(exc)
                return 1
            try:
                meta = resolve_materialize_meta(
                    rel, manifest, archive_override=archive or None
                )
            except FileNotFoundError as exc:
                _print_error(exc)
                return 1

            use_archive = normalize_archive(
                archive or meta.get("archive") or "remote_staging"
            )
            prov = meta.get("provider")
            mcfg = manifest_with_provider(manifest or {}, use_archive, prov)
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            print(f"fetch-remote: {rel} -> {dest} ({use_archive})")
            try:
                nbytes = fetch_path(
                    meta,
                    rel,
                    use_archive,
                    env,
                    mcfg,
                    dest=dest,
                    dest_root=dest_root,
                    progress_label=f"[cloud-vfs ensure-remote] {rel}",
                )
            except (CloudStorageError, FileNotFoundError, OSError) as exc:
                _print_error(exc)
                return 1
            print(f"OK: {dest} ({fmt_bytes(nbytes)})")
        return 0
    finally:
        if project_root_override:
            if prev_root is None:
                os.environ.pop("CLOUD_VFS_PROJECT_ROOT", None)
            else:
                os.environ["CLOUD_VFS_PROJECT_ROOT"] = prev_root
            project_root.cache_clear()


def index_ingested_file(
    dest_rel: str,
    *,
    archive: str,
    provider: str,
    blob: str,
    source: Path,
    entry: dict[str, Any],
) -> bool:
    policy = load_policy()
    dest_rel = normalize_rel(dest_rel)
    size = source.stat().st_size
    if not should_index(dest_rel, size, policy):
        return False
    digest = sha256_file(source)
    shard_root = shard_root_for(dest_rel, entry)
    row = _row_base(
        dest_rel,
        archive=archive,
        provider=provider,
        blob=blob,
        size=size,
        sha256=digest,
        state="cloud-only",
        policy_id=entry.get("id"),
    )
    row["uploaded_at"] = row["updated_at"]
    upsert_row(shard_root, dest_rel, row, policy)
    return True


def cmd_ingest(
    source: Path,
    dest_rel: str,
    *,
    archive: str,
    dry_run: bool,
    emit_stub: bool,
    index_inventory: bool,
) -> int:
    try:
        source = source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Source is not a file: {source}")
        dest_rel = normalize_rel(dest_rel)
        manifest = load_manifest()
        use_archive = normalize_archive(archive)
        env = load_cloud_env()
        entry = ensure_manifest_entry(
            manifest,
            dest_rel,
            archive=use_archive,
            provider="",
            is_dir=False,
            blob=dest_rel,
        )
        prov = entry.get("provider")
        mcfg = manifest_with_provider(manifest, use_archive, prov)
        from cloud_vfs.storage.fetch import resolve_archive

        cfg = resolve_archive(env, mcfg, use_archive)
        entry["provider"] = cfg.provider

        if dry_run:
            size = source.stat().st_size
            print(
                f"would ingest: {source} -> {cfg.provider}/{use_archive}:{dest_rel} "
                f"({fmt_bytes(size)})"
            )
            return 0

        print(f"ingest: {source} -> {cfg.provider}/{use_archive} as {dest_rel}")
        try:
            upload_path(
                dest_rel,
                use_archive,
                env,
                mcfg,
                progress_label=f"[cloud-vfs ingest] {dest_rel}",
                source_path=source,
            )
        except (CloudStorageError, ValueError, FileNotFoundError) as exc:
            _print_error(exc)
            return 1

        if index_inventory:
            index_ingested_file(
                dest_rel,
                archive=use_archive,
                provider=cfg.provider,
                blob=dest_rel,
                source=source,
                entry=entry,
            )

        if emit_stub:
            mark_offloaded(entry)
            meta = {
                "manifest_id": entry.get("id"),
                "archive": use_archive,
                "provider": cfg.provider,
                "blob": dest_rel,
            }
            write_stub(dest_rel, meta)
            target = project_root() / dest_rel
            if target.exists() and is_real_local(dest_rel):
                target.unlink()

        save_manifest(manifest)
        print(f"OK: ingested {dest_rel} ({fmt_bytes(source.stat().st_size)})")
        return 0
    except (FileNotFoundError, ValueError, PathOutsideProjectError, CloudVfsError) as exc:
        _print_error(exc)
        return 1
