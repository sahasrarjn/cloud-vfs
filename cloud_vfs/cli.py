from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from cloud_vfs import __version__
from cloud_vfs.project import fetch_cmd, manifest_path, package_path, project_root
from cloud_vfs.scaffold import cmd_init
from cloud_vfs.storage.env import load_cloud_env, normalize_archive
from cloud_vfs.storage.fetch import fetch_path, manifest_with_provider, resolve_archive, upload_path
from cloud_vfs.storage.manifest import (
    find_entry,
    load_manifest,
    mark_fetched,
    mark_offloaded,
    save_manifest,
)
from cloud_vfs.storage.paths import abs_path, is_real_local, normalize_rel
from cloud_vfs.storage.stub import read_stub, remove_stub, resolve_meta, write_stub


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


def cmd_ensure(paths: list[str]) -> int:
    manifest = load_manifest()
    changed = False
    for raw in paths:
        rel = normalize_rel(raw)
        entry = find_entry(manifest, rel)
        if is_real_local(rel):
            print(f"local: {rel}")
            continue
        try:
            meta = resolve_meta(rel, entry)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        archive = meta.get("archive", "local_archive")
        env = load_cloud_env()
        prov = meta.get("provider") or (entry or {}).get("provider")
        mcfg = manifest_with_provider(manifest, archive, prov)
        try:
            cfg = resolve_archive(env, mcfg, archive)
        except (KeyError, ValueError) as exc:
            print(f"ERROR: missing cloud config: {exc}", file=sys.stderr)
            return 1
        print(f"fetch: {rel} ({cfg.provider}/{archive})")
        remove_stub(rel)
        nbytes = fetch_path(meta, rel, archive, env, mcfg)
        if entry:
            mark_fetched(entry)
            changed = True
        print(f"OK: {rel} ({fmt_bytes(nbytes)})")
    if changed:
        save_manifest(manifest)
    return 0


def cmd_resolve(path: str) -> int:
    rel = normalize_rel(path)
    manifest = load_manifest()
    entry = find_entry(manifest, rel)
    stub = read_stub(rel)
    local_present = is_real_local(rel)
    out: dict[str, Any] = {
        "path": rel,
        "project_root": str(project_root()),
        "manifest": str(manifest_path()),
        "local_present": local_present,
        "cloud_only": not local_present,
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


def cmd_status(as_json: bool) -> int:
    manifest = load_manifest()
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
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"{'path':52} {'local':5} {'size':>8}  status")
        for r in rows:
            loc = "yes" if r["local"] else "stub"
            print(f"{r['path'][:52]:52} {loc:5} {r['size_human']:>8}  {r.get('status', '-')}")
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


def cmd_offload(
    paths: list[str],
    *,
    dry_run: bool,
    archive_override: str | None,
) -> int:
    manifest = load_manifest()
    if not paths:
        paths = offload_candidates(manifest)
        if not paths:
            print("Nothing local to offload.")
            return 0
        if dry_run:
            print("Local paths (pass explicit paths to offload, or run without --dry-run):")
    for raw in paths:
        rel = normalize_rel(raw)
        if not abs_path(rel).exists() or not is_real_local(rel):
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
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        size = tree_size(abs_path(rel))
        if dry_run:
            print(f"  would offload: {rel}  {fmt_bytes(size)}  -> {cfg.provider}/{use_archive}")
            continue
        blob_prefix = (entry.get("blob_prefix") if entry else None) or f"{rel.rstrip('/')}/"
        print(f"offload: {rel} -> {cfg.provider}/{use_archive}")
        upload_path(rel, use_archive, env, mcfg, blob_prefix=blob_prefix)
        meta: dict[str, Any] = {
            "manifest_id": entry.get("id") if entry else None,
            "archive": use_archive,
            "provider": cfg.provider,
        }
        if entry:
            if entry.get("blob"):
                meta["blob"] = entry["blob"]
            meta["blob_prefix"] = entry.get("blob_prefix") or rel.rstrip("/") + "/"
            mark_offloaded(entry)
        else:
            meta["blob_prefix"] = rel.rstrip("/") + "/"
        if abs_path(rel).is_dir():
            shutil.rmtree(abs_path(rel))
        else:
            abs_path(rel).unlink()
        write_stub(rel, meta)
        print(f"OK stub: {rel} ({fmt_bytes(size)})")
    if not dry_run and paths:
        save_manifest(manifest)
    return 0


def cmd_materialize_stubs() -> int:
    manifest = load_manifest()
    n = 0
    for entry in manifest.get("entries", []):
        if entry.get("status") != "offloaded-local-removed":
            continue
        rel = normalize_rel(entry.get("local", ""))
        if not rel or is_real_local(rel):
            continue
        write_stub(
            rel,
            {
                "manifest_id": entry.get("id"),
                "archive": entry.get("archive", "local_archive"),
                "blob": entry.get("blob"),
                "blob_prefix": entry.get("blob_prefix"),
            },
        )
        n += 1
        print(f"stub: {rel}")
    save_manifest(manifest)
    print(f"Wrote {n} stub(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cloud-vfs",
        description="Manual Azure blob virtual filesystem",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Scaffold .cloud-vfs/ in the current project")
    p_init.add_argument("--skill", action="store_true", help="Install Cursor skill to .cursor/skills/")
    p_init.add_argument("--path", type=Path, default=Path.cwd(), help="Project root")

    p_ensure = sub.add_parser("ensure", help="Fetch from blob if stub or missing")
    p_ensure.add_argument("paths", nargs="+")

    p_resolve = sub.add_parser("resolve", help="JSON fetch instructions")
    p_resolve.add_argument("path")

    p_status = sub.add_parser("status", help="Local vs stub + sizes")
    p_status.add_argument("--json", action="store_true")

    p_offload = sub.add_parser("offload", help="Upload + stub (explicit paths; use --dry-run first)")
    p_offload.add_argument("paths", nargs="*")
    p_offload.add_argument("--dry-run", action="store_true")
    p_offload.add_argument(
        "--archive",
        choices=["local_archive", "remote_staging", "runpod_staging"],
        help="runpod_staging is a legacy alias for remote_staging",
    )

    sub.add_parser("materialize-stubs", help="Write .cloudstub for offloaded manifest entries")

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.path, install_skill=args.skill)
    if args.cmd == "ensure":
        return cmd_ensure(args.paths)
    if args.cmd == "resolve":
        return cmd_resolve(args.path)
    if args.cmd == "status":
        return cmd_status(args.json)
    if args.cmd == "offload":
        return cmd_offload(
            args.paths,
            dry_run=args.dry_run,
            archive_override=normalize_archive(args.archive) if args.archive else None,
        )
    if args.cmd == "materialize-stubs":
        return cmd_materialize_stubs()
    return 1
