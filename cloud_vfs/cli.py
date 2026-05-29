from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from cloud_vfs import __version__
from cloud_vfs.project import fetch_cmd, manifest_path, project_root, temp_dir
from cloud_vfs.doctor import cmd_doctor
from cloud_vfs.guard import assess_delete_safety, cmd_guard
from cloud_vfs.scaffold import cmd_init
from cloud_vfs.scan import cmd_scan
from cloud_vfs.try_demo import cmd_try
from cloud_vfs.storage.env import load_cloud_env, normalize_archive
from cloud_vfs.storage.errors import CloudStorageError, CloudVfsError, PathOutsideProjectError
from cloud_vfs.storage.fetch import fetch_path, manifest_with_provider, resolve_archive, upload_path
from cloud_vfs.storage.inventory import (
    detect_drift,
    hash_paths_before_offload,
    index_offloaded_path,
    iter_inventory_rows,
    list_orphan_blobs,
    load_policy,
    mark_inventory_fetched,
    prune_inventory,
    rebuild_index_from_blob,
    register_paths,
    repair_stubs,
    verify_fetched_tree,
)
from cloud_vfs.storage.inventory import VerifyError
from cloud_vfs.storage.manifest import (
    ensure_manifest_entry,
    find_entry,
    load_manifest,
    mark_fetched,
    mark_offloaded,
    save_manifest,
)
from cloud_vfs.storage.paths import abs_path, is_real_local, normalize_rel
from cloud_vfs.storage.stub import (
    is_ref,
    migrate_legacy_file_sidecar,
    read_stub,
    remove_stub,
    resolve_meta,
    stub_placement,
    write_stub,
)


def tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and p.name != ".cloudstub":
            total += p.stat().st_size
    return total


def fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _print_error(exc: Exception) -> None:
    print(f"ERROR: {exc}", file=sys.stderr)


def _safe_fetch(rel: str, meta: dict[str, Any], archive: str, env: dict[str, str], manifest: dict[str, Any]) -> int:
    migrate_legacy_file_sidecar(rel)
    if is_ref(rel):
        tmp = temp_dir() / f"fetch-{Path(rel).name}.{uuid.uuid4().hex[:8]}"
        try:
            nbytes = fetch_path(meta, rel, archive, env, manifest, dest=tmp)
            remove_stub(rel)
            tmp.replace(abs_path(rel))
            return nbytes
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    nbytes = fetch_path(meta, rel, archive, env, manifest)
    remove_stub(rel)
    return nbytes


def cmd_ensure(paths: list[str], *, verify: bool) -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    changed = False
    for raw in paths:
        try:
            rel = normalize_rel(raw)
        except PathOutsideProjectError as exc:
            _print_error(exc)
            return 1
        entry = find_entry(manifest, rel)
        if is_real_local(rel):
            print(f"local: {rel}")
            continue
        try:
            meta = resolve_meta(rel, entry)
        except FileNotFoundError as exc:
            _print_error(exc)
            return 1
        archive = meta.get("archive", "local_archive")
        env = load_cloud_env()
        prov = meta.get("provider") or (entry or {}).get("provider")
        mcfg = manifest_with_provider(manifest, archive, prov)
        try:
            cfg = resolve_archive(env, mcfg, archive)
        except (KeyError, ValueError) as exc:
            _print_error(f"missing cloud config: {exc}")
            return 1
        print(f"fetch: {rel} ({cfg.provider}/{archive})")
        try:
            nbytes = _safe_fetch(rel, meta, archive, env, mcfg)
        except (CloudStorageError, FileNotFoundError, OSError) as exc:
            _print_error(exc)
            return 1
        if verify:
            try:
                verify_fetched_tree(rel)
            except VerifyError as exc:
                _print_error(exc)
                return 1
        mark_inventory_fetched(rel)
        if entry:
            mark_fetched(entry)
            changed = True
        print(f"OK: {rel} ({fmt_bytes(nbytes)})" + (" verified" if verify else ""))
    if changed:
        save_manifest(manifest)
    return 0


def cmd_resolve(path: str) -> int:
    try:
        rel = normalize_rel(path)
        manifest = load_manifest()
    except (FileNotFoundError, ValueError, PathOutsideProjectError) as exc:
        _print_error(exc)
        return 1
    entry = find_entry(manifest, rel)
    stub = read_stub(rel)
    local_present = is_real_local(rel)
    ref_present = is_ref(rel) or (stub is not None and not local_present)
    safety = assess_delete_safety(rel)
    out: dict[str, Any] = {
        "path": rel,
        "project_root": str(project_root()),
        "manifest": str(manifest_path()),
        "local_present": local_present,
        "cloud_only": not local_present,
        "is_ref": ref_present,
        "placement": stub_placement(rel),
        "managed_by_cloud_vfs": safety["managed_by_cloud_vfs"],
        "safe_to_delete_local": safety["safe_to_delete_local"],
        "delete_safety_reasons": safety["reasons"],
    }
    if entry:
        out["entry"] = {
            k: entry.get(k)
            for k in ("id", "archive", "provider", "status", "blob", "blob_prefix", "uploaded")
            if entry.get(k) is not None
        }
    if stub:
        out["stub"] = stub
    if not local_present:
        out["fetch_cmd"] = fetch_cmd(rel)
        archive = (stub or {}).get("archive") or (entry or {}).get("archive") or "local_archive"
        env = load_cloud_env()
        try:
            cfg = resolve_archive(env, manifest, archive)
            blob = (stub or {}).get("blob") or (entry or {}).get("blob")
            prefix = (stub or {}).get("blob_prefix") or (entry or {}).get("blob_prefix")
            out["provider"] = cfg.provider
            if cfg.provider == "aws":
                if blob:
                    out["s3_url"] = f"s3://{cfg.bucket}/{blob}"
                elif prefix:
                    out["s3_prefix_url"] = f"s3://{cfg.bucket}/{prefix.rstrip('/')}/"
            else:
                base = (manifest.get(archive) or {}).get("base_url") or cfg.base_url
                if blob:
                    out["blob_url"] = f"{base}/{blob}".replace("//", "/").replace(":/", "://")
                elif prefix:
                    out["blob_prefix_url"] = f"{base}/{prefix}".replace("//", "/").replace(":/", "://")
        except (KeyError, ValueError):
            pass
    print(json.dumps(out, indent=2))
    return 0


def cmd_status(*, as_json: bool, drift: bool) -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    policy = load_policy()
    rows = []
    for entry in manifest.get("entries", []):
        local = normalize_rel(entry.get("local", ""))
        if not local:
            continue
        present = is_real_local(local)
        size = tree_size(abs_path(local)) if present else 0
        rows.append(
            {
                "id": entry.get("id"),
                "path": local,
                "status": entry.get("status"),
                "local": present,
                "size": size,
                "size_human": fmt_bytes(size),
                "archive": entry.get("archive"),
            }
        )
    rows.sort(key=lambda r: (-r["size"], r["path"]))
    inventory_count = sum(1 for _ in iter_inventory_rows(policy))
    payload: dict[str, Any] = {
        "manifest_entries": rows,
        "inventory_rows": inventory_count,
    }
    if drift:
        payload["drift"] = detect_drift(check_blob=False)
    if as_json:
        print(json.dumps(payload if drift else rows, indent=2))
    else:
        print(f"{'path':52} {'local':5} {'size':>8}  status")
        for r in rows:
            loc = "yes" if r["local"] else "stub"
            print(f"{r['path'][:52]:52} {loc:5} {r['size_human']:>8}  {r.get('status', '-')}")
        print(f"\nInventory rows: {inventory_count}")
        if drift:
            issues = payload["drift"]
            if not issues:
                print("Drift: none")
            else:
                print(f"Drift: {len(issues)} issue(s)")
                for issue in issues:
                    print(f"  [{issue['type']}] {issue.get('path', issue.get('blob', '-'))}")
    return 0


def offload_candidates(manifest: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    partial_roots = {
        normalize_rel(e.get("local", ""))
        for e in manifest.get("entries", [])
        if e.get("status") == "partial" and e.get("local")
    }
    for entry in manifest.get("entries", []):
        rel = normalize_rel(entry.get("local", ""))
        if not rel or not is_real_local(rel):
            continue
        if rel in partial_roots:
            continue
        raw.append(rel)
    raw.sort(key=len, reverse=True)
    out: list[str] = []
    for rel in raw:
        if any(rel != kept and kept.startswith(rel.rstrip("/") + "/") for kept in out):
            continue
        out.append(rel)
    return sorted(out, key=lambda p: tree_size(abs_path(p)), reverse=True)


def _write_dir_stub_after_upload(rel: str, meta: dict[str, Any]) -> None:
    target = abs_path(rel)
    pending = temp_dir() / f"stub-{uuid.uuid4().hex[:8]}.json"
    write_stub(rel, meta)
    pending.write_text((target / ".cloudstub").read_text())
    shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    pending.replace(target / ".cloudstub")


def cmd_offload(
    paths: list[str],
    *,
    dry_run: bool,
    archive_override: str | None,
) -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    if not paths:
        paths = offload_candidates(manifest)
        if not paths:
            print("Nothing local to offload.")
            print("  cloud-vfs scan              # find large files under data/")
            print("  cloud-vfs scan --add        # add them to manifest, then retry")
            return 0
        if dry_run:
            print("Would offload (confirm, then run without --dry-run):")
    changed = False
    for raw in paths:
        try:
            rel = normalize_rel(raw)
        except PathOutsideProjectError as exc:
            _print_error(exc)
            return 1
        src = abs_path(rel)
        if not src.exists() or not is_real_local(rel):
            print(f"SKIP (not local): {rel}")
            continue
        entry = find_entry(manifest, rel)
        use_archive = normalize_archive(
            archive_override or (entry or {}).get("archive", "local_archive")
        )
        prov = (entry or {}).get("provider")
        mcfg = manifest_with_provider(manifest, use_archive, prov)
        env = load_cloud_env()
        try:
            cfg = resolve_archive(env, mcfg, use_archive)
        except (KeyError, ValueError) as exc:
            _print_error(exc)
            return 1
        size = tree_size(src)
        if dry_run:
            print(f"  would offload: {rel}  {fmt_bytes(size)}  -> {cfg.provider}/{use_archive}")
            continue
        blob_prefix = (entry.get("blob_prefix") if entry else None) or f"{rel.rstrip('/')}/"
        print(f"offload: {rel} -> {cfg.provider}/{use_archive}")
        precomputed = hash_paths_before_offload(rel)
        try:
            upload_path(rel, use_archive, env, mcfg, blob_prefix=blob_prefix)
        except (CloudStorageError, ValueError, FileNotFoundError) as exc:
            _print_error(exc)
            return 1
        entry = ensure_manifest_entry(
            manifest,
            rel,
            archive=use_archive,
            provider=cfg.provider,
            is_dir=src.is_dir(),
            blob=rel if src.is_file() else None,
            blob_prefix=blob_prefix if src.is_dir() else None,
        )
        meta: dict[str, Any] = {
            "manifest_id": entry.get("id"),
            "archive": use_archive,
            "provider": cfg.provider,
        }
        if src.is_dir():
            meta["blob_prefix"] = entry.get("blob_prefix") or rel.rstrip("/") + "/"
            mark_offloaded(entry)
            index_offloaded_path(
                rel,
                archive=use_archive,
                provider=cfg.provider,
                blob=None,
                blob_prefix=meta["blob_prefix"],
                entry=entry,
                precomputed=precomputed,
            )
            _write_dir_stub_after_upload(rel, meta)
        else:
            meta["blob"] = entry.get("blob") or rel
            mark_offloaded(entry)
            index_offloaded_path(
                rel,
                archive=use_archive,
                provider=cfg.provider,
                blob=meta["blob"],
                blob_prefix=None,
                entry=entry,
                precomputed=precomputed,
            )
            write_stub(rel, meta)
        changed = True
        print(f"OK stub: {rel} ({fmt_bytes(size)})")
    if changed:
        save_manifest(manifest)
    return 0


def cmd_register(paths: list[str]) -> int:
    if not paths:
        print("Usage: cloud-vfs register <paths...>", file=sys.stderr)
        return 1
    try:
        indexed, skipped = register_paths(paths)
    except (FileNotFoundError, ValueError, PathOutsideProjectError) as exc:
        _print_error(exc)
        return 1
    print(f"Indexed {indexed} file(s), skipped {skipped}")
    return 0


def cmd_reconcile(
    *,
    as_json: bool,
    from_blob: bool,
    fix_index: bool,
    repair_stubs_flag: bool,
    orphan_blobs: bool,
    prefix: str | None,
) -> int:
    try:
        load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    if repair_stubs_flag:
        n = repair_stubs()
        print(f"Repaired {n} stub/ref(s) from manifest and inventory.")
        return 0
    if orphan_blobs:
        orphans = list_orphan_blobs()
        if as_json:
            print(json.dumps(orphans, indent=2))
        else:
            if not orphans:
                print("No orphan blobs under cloud-vfs policy prefixes (unindexed in inventory).")
            else:
                print(
                    f"{len(orphans)} orphan blob(s) in cloud-vfs bucket "
                    "(not in inventory — may be old paths or non-vfs uploads; do not auto-delete):"
                )
                for issue in orphans:
                    print(f"  {issue.get('blob', issue.get('path', '-'))}")
                print("\nThese are only blobs visible via your cloud-vfs config bucket/prefixes.")
                print("Prod/other buckets are never scanned.")
        return 0
    if fix_index:
        if not prefix:
            _print_error("--fix-index requires --prefix")
            return 1
        try:
            added = rebuild_index_from_blob(prefix)
        except CloudVfsError as exc:
            _print_error(exc)
            return 1
        print(f"Rebuilt index: {added} row(s) under {prefix}")
        return 0
    issues = detect_drift(check_blob=from_blob)
    if as_json:
        print(json.dumps(issues, indent=2))
    else:
        if not issues:
            print("No drift detected.")
        else:
            print(f"{len(issues)} drift issue(s):")
            for issue in issues:
                detail = issue.get("path") or issue.get("blob") or "-"
                print(f"  [{issue['type']}] {detail}")
    return 1 if issues else 0


def cmd_prune() -> int:
    removed, kept = prune_inventory()
    print(f"Pruned {removed} row(s), kept {kept}")
    return 0


def cmd_materialize_stubs() -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    n = 0
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
        migrated = migrate_legacy_file_sidecar(rel)
        if migrated:
            n += 1
            print(f"inline ref: {rel} (migrated)")
            continue
        write_stub(rel, meta)
        n += 1
        placement = "inline" if abs_path(rel).suffix else "sidecar"
        print(f"{placement} ref: {rel}")
    save_manifest(manifest)
    print(f"Wrote {n} stub(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cloud-vfs",
        description="Manual cloud blob virtual filesystem",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Scaffold .cloud-vfs/ in the current project")
    p_init.add_argument("--skill", action="store_true", help="Install Cursor skill to .cursor/skills/")
    p_init.add_argument("--path", type=Path, default=Path.cwd(), help="Project root")

    p_try = sub.add_parser(
        "try",
        help="Create a sandbox demo project to learn register/offload/ensure",
    )
    p_try.add_argument(
        "--path",
        type=Path,
        default=Path("cloud-vfs-try"),
        help="Directory to create (default: ./cloud-vfs-try)",
    )
    p_try.add_argument("--force", action="store_true", help="Overwrite an existing demo tree")

    p_ensure = sub.add_parser("ensure", help="Fetch from blob if stub or missing")
    p_ensure.add_argument("paths", nargs="+")
    p_ensure.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip sha256 check against inventory after download",
    )

    p_resolve = sub.add_parser("resolve", help="JSON fetch instructions")
    p_resolve.add_argument("path")

    p_status = sub.add_parser("status", help="Local vs stub + sizes")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--drift", action="store_true", help="Include inventory drift summary")

    p_scan = sub.add_parser(
        "scan",
        help="List large local files in your repo (policy scope); optional --add to manifest",
    )
    p_scan.add_argument("--json", action="store_true")
    p_scan.add_argument(
        "--add",
        action="store_true",
        help="Add untracked paths to manifest as offload-candidate",
    )
    p_scan.add_argument("--prefix", help="Limit scan to a path prefix (e.g. data/old_run)")

    p_register = sub.add_parser("register", help="Index local large files (+ sha256)")
    p_register.add_argument("paths", nargs="+")

    p_reconcile = sub.add_parser("reconcile", help="Audit disk vs inventory vs blob")
    p_reconcile.add_argument("--json", action="store_true")
    p_reconcile.add_argument("--from-blob", action="store_true", help="Check blob existence")
    p_reconcile.add_argument("--fix-index", action="store_true", help="Rebuild index from blob listing")
    p_reconcile.add_argument("--prefix", help="Prefix for --fix-index")
    p_reconcile.add_argument(
        "--repair-stubs",
        action="store_true",
        help="Regenerate missing inline refs and .cloudstub from manifest/inventory",
    )
    p_reconcile.add_argument(
        "--orphan-blobs",
        action="store_true",
        help="List blobs in cloud-vfs bucket/prefixes not in inventory (read-only)",
    )

    p_guard = sub.add_parser(
        "guard",
        help="Block unsafe local deletes (e.g. prod bucket uploads cloud-vfs does not track)",
    )
    p_guard.add_argument("paths", nargs="+")
    p_guard.add_argument("--json", action="store_true")

    sub.add_parser("prune", help="Remove inventory rows below min size")

    p_offload = sub.add_parser("offload", help="Upload + stub (explicit paths; use --dry-run first)")
    p_offload.add_argument("paths", nargs="*")
    p_offload.add_argument("--dry-run", action="store_true")
    p_offload.add_argument(
        "--archive",
        choices=["local_archive", "remote_staging", "runpod_staging"],
        help="runpod_staging is a legacy alias for remote_staging",
    )

    sub.add_parser("materialize-stubs", help="Write .cloudstub for offloaded manifest entries")

    p_doctor = sub.add_parser("doctor", help="Check install, project config, CLI, and cloud access")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.add_argument("--probe", action="store_true", help="List bucket/container (read-only)")
    p_doctor.add_argument(
        "--roundtrip",
        action="store_true",
        help="Upload and download a small probe object, then delete it",
    )

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.path, install_skill=args.skill)
    if args.cmd == "try":
        return cmd_try(args.path, force=args.force)
    if args.cmd == "ensure":
        return cmd_ensure(args.paths, verify=not args.no_verify)
    if args.cmd == "resolve":
        return cmd_resolve(args.path)
    if args.cmd == "status":
        return cmd_status(as_json=args.json, drift=args.drift)
    if args.cmd == "scan":
        return cmd_scan(as_json=args.json, add=args.add, prefix=args.prefix)
    if args.cmd == "register":
        return cmd_register(args.paths)
    if args.cmd == "reconcile":
        return cmd_reconcile(
            as_json=args.json,
            from_blob=args.from_blob,
            fix_index=args.fix_index,
            repair_stubs_flag=args.repair_stubs,
            orphan_blobs=args.orphan_blobs,
            prefix=args.prefix,
        )
    if args.cmd == "guard":
        return cmd_guard(args.paths, as_json=args.json)
    if args.cmd == "prune":
        return cmd_prune()
    if args.cmd == "offload":
        return cmd_offload(
            args.paths,
            dry_run=args.dry_run,
            archive_override=normalize_archive(args.archive) if args.archive else None,
        )
    if args.cmd == "materialize-stubs":
        return cmd_materialize_stubs()
    if args.cmd == "doctor":
        return cmd_doctor(as_json=args.json, probe=args.probe, roundtrip=args.roundtrip)
    return 1
