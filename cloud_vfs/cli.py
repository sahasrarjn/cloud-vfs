from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cloud_vfs import __version__
from cloud_vfs.project import fetch_cmd, manifest_path, project_root, temp_dir
from cloud_vfs.doctor import cmd_doctor
from cloud_vfs.guard import assess_delete_safety, cmd_guard
from cloud_vfs.scaffold import cmd_init
from cloud_vfs.scan import cmd_scan
from cloud_vfs.try_demo import cmd_try
from cloud_vfs.storage.env import (
    archive_cli_arg,
    archive_from_entry,
    load_cloud_env,
    normalize_archive,
    source_target_hints,
)
from cloud_vfs.storage.errors import CloudStorageError, CloudVfsError, PathOutsideProjectError
from cloud_vfs.storage.fetch import fetch_path, manifest_with_provider, resolve_archive, upload_path
from cloud_vfs.storage.inventory import (
    detect_drift,
    find_row,
    hash_paths_before_offload,
    index_offloaded_path,
    is_excluded,
    iter_inventory_rows,
    list_orphan_blobs,
    load_policy,
    mark_inventory_fetched,
    mark_inventory_fetched_tree,
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
from cloud_vfs.storage.backends import (
    azure_blob_url_redacted,
    blob_content_length,
    blob_matches_local_size,
    choose_azure_transport,
)
from cloud_vfs.storage.offload_job import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_STUBBED,
    clear_job_offload_progress,
    format_job_summary,
    job_has_failures,
    job_has_pending,
    load_offload_job,
    mark_job_skipped_if_pending,
    new_offload_job,
    save_offload_job,
    set_job_path_status,
)
from cloud_vfs.storage.locks import path_lock
from cloud_vfs.storage.offload_progress import (
    OffloadInterruptState,
    clear_offload_progress,
    format_verify_report,
    load_offload_progress,
    new_offload_progress,
    save_offload_progress,
    verify_offload,
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


def _needs_ensure(rel: str, policy: dict[str, Any] | None = None) -> bool:
    if is_real_local(rel):
        return False
    if is_ref(rel) or read_stub(rel):
        return True
    policy = policy or load_policy()
    found = find_row(rel, policy)
    return found is not None and found[1].get("state") == "cloud-only"


def _ensure_targets(rel: str, manifest: dict[str, Any]) -> list[str]:
    rel = normalize_rel(rel)
    target = abs_path(rel)
    entry = find_entry(manifest, rel)
    stub = read_stub(rel)

    if target.is_file():
        return [rel]

    if stub and stub.get("blob_prefix"):
        return [rel]
    if entry and entry.get("blob_prefix") and normalize_rel(entry.get("local", "")) == rel:
        return [rel]

    policy = load_policy()
    prefix = rel.rstrip("/") + "/"
    targets: set[str] = set()
    if target.is_dir():
        for file_rel, _ in _iter_local_files(rel):
            if _needs_ensure(file_rel, policy):
                targets.add(file_rel)
    for _, local, row in iter_inventory_rows(policy):
        if local.startswith(prefix) and row.get("state") == "cloud-only" and not is_real_local(local):
            targets.add(local)

    if targets:
        return sorted(targets)
    return [rel]


def _iter_local_files(root_rel: str):
    from cloud_vfs.storage.inventory import _iter_local_files as iter_files

    yield from iter_files(root_rel)


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


_active_interrupt: OffloadInterruptState | None = None


def _sigterm_handler(_signum: int, _frame: object) -> None:
    global _active_interrupt
    if _active_interrupt is not None:
        print("[cloud-vfs] SIGTERM — flushing partial offload progress …", flush=True)
        _active_interrupt.flush()
    raise SystemExit(128 + _signum)


@contextmanager
def _offload_interrupt_guard(
    manifest: dict[str, Any],
    progress: dict[str, Any],
    *,
    save_manifest_on_flush: bool = False,
) -> Iterator[OffloadInterruptState]:
    global _active_interrupt
    state = OffloadInterruptState(
        manifest=manifest,
        progress=progress,
        on_flush=(lambda: save_manifest(manifest)) if save_manifest_on_flush else None,
    )
    previous = signal.getsignal(signal.SIGTERM)
    _active_interrupt = state
    signal.signal(signal.SIGTERM, _sigterm_handler)
    try:
        yield state
    finally:
        _active_interrupt = None
        signal.signal(signal.SIGTERM, previous)


def _resolve_fetch_meta(
    rel: str,
    entry: dict[str, Any] | None,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        return resolve_meta(rel, entry)
    except FileNotFoundError:
        pass
    found = find_row(rel, load_policy())
    if not found:
        raise FileNotFoundError(f"No manifest entry, stub, or inventory row for {rel}")
    _, row = found
    meta: dict[str, Any] = {
        "archive": row.get("archive", "local_archive"),
        "provider": row.get("provider"),
        "blob": row.get("blob"),
    }
    parent = find_entry(manifest, rel)
    if parent and parent.get("blob_prefix") and not meta.get("blob"):
        prefix = parent["blob_prefix"].rstrip("/")
        meta["blob"] = f"{prefix}/{Path(rel).name}"
    if not meta.get("blob") and not meta.get("blob_prefix"):
        raise FileNotFoundError(f"No blob mapping for {rel}")
    return meta


def _safe_fetch(
    rel: str,
    meta: dict[str, Any],
    archive: str,
    env: dict[str, str],
    manifest: dict[str, Any],
    *,
    progress_label: str | None = None,
) -> int:
    migrate_legacy_file_sidecar(rel)
    if is_ref(rel):
        tmp = temp_dir() / f"fetch-{Path(rel).name}.{uuid.uuid4().hex[:8]}"
        try:
            nbytes = fetch_path(
                meta,
                rel,
                archive,
                env,
                manifest,
                dest=tmp,
                progress_label=progress_label,
            )
            remove_stub(rel)
            tmp.replace(abs_path(rel))
            return nbytes
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    nbytes = fetch_path(meta, rel, archive, env, manifest, progress_label=progress_label)
    remove_stub(rel)
    return nbytes


def _resolve_remote_fields(
    cfg: Any,
    blob: str | None,
    prefix: str | None,
) -> tuple[bool, int | None]:
    if blob:
        length = blob_content_length(cfg, blob)
        return length is not None, length
    if prefix:
        from cloud_vfs.storage.backends import list_blob_keys

        keys = list_blob_keys(cfg, prefix.rstrip("/"))
        return bool(keys), None
    return False, None


def _status_label_offloaded(
    *,
    provider: str,
    archive: str,
    rel: str,
    remote_present: bool,
    content_length: int | None,
) -> str:
    if remote_present and content_length is not None:
        size_part = fmt_bytes(content_length)
    elif remote_present:
        size_part = "remote ok"
    else:
        size_part = "remote unverified"
    return f"OFFLOADED ({size_part} on {provider}/{archive}) — {fetch_cmd(rel)}"


def _plan_ensure_fetch(
    rel: str,
    meta: dict[str, Any],
    cfg: Any,
) -> dict[str, Any]:
    blob = meta.get("blob")
    prefix = meta.get("blob_prefix")
    content_length = blob_content_length(cfg, blob) if blob else None
    transport = choose_azure_transport(content_length) if cfg.provider == "azure" else "aws-cli"
    plan: dict[str, Any] = {
        "path": rel,
        "archive": meta.get("archive", "local_archive"),
        "provider": cfg.provider,
        "transport": transport,
    }
    if blob:
        plan["blob"] = blob
        if content_length is not None:
            plan["content_length"] = content_length
            plan["content_length_human"] = fmt_bytes(content_length)
        if cfg.provider == "azure":
            plan["blob_url"] = azure_blob_url_redacted(cfg, blob)
        else:
            plan["blob_url"] = f"s3://{cfg.bucket}/{blob}"
    elif prefix:
        plan["blob_prefix"] = prefix
        plan["transport"] = "az-cli-batch" if cfg.provider == "azure" else "aws-cli"
    return plan


def cmd_ensure(
    paths: list[str],
    *,
    verify: bool,
    check_only: bool = False,
    dry_run: bool = False,
    source_archive: str | None = None,
    target_root: Path | None = None,
    paths_file: Path | None = None,
    manifest_file: Path | None = None,
    config_env: Path | None = None,
    secrets_env: Path | None = None,
    ref_root: Path | None = None,
) -> int:
    if check_only:
        from cloud_vfs.materialize import cmd_preflight

        return cmd_preflight(paths, as_json=False)

    if dry_run and target_root is not None:
        _print_error("--dry-run is not supported with --target-root")
        return 1

    if target_root is not None:
        from cloud_vfs.materialize import cmd_ensure_at_target

        return cmd_ensure_at_target(
            paths,
            target_root=target_root,
            source_archive=source_archive,
            manifest_file=manifest_file,
            paths_file=paths_file,
            config_env=config_env,
            secrets_env=secrets_env,
            ref_root=ref_root,
        )

    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    changed = False
    expanded: list[str] = []
    for raw in paths:
        try:
            rel = normalize_rel(raw)
        except PathOutsideProjectError as exc:
            _print_error(exc)
            return 1
        expanded.extend(_ensure_targets(rel, manifest))
    env = load_cloud_env()
    for rel in expanded:
        entry = find_entry(manifest, rel)
        if is_real_local(rel):
            if dry_run:
                print(f"local (skip): {rel}")
            else:
                print(f"local: {rel} (already materialized — skipping fetch, no egress)")
            continue
        try:
            meta = _resolve_fetch_meta(rel, entry, manifest)
        except FileNotFoundError as exc:
            _print_error(exc)
            return 1
        if source_archive:
            meta["archive"] = normalize_archive(source_archive)
        archive = meta.get("archive", "local_archive")
        prov = meta.get("provider") or (entry or {}).get("provider")
        mcfg = manifest_with_provider(manifest, archive, prov)
        try:
            cfg = resolve_archive(env, mcfg, archive)
        except (KeyError, ValueError) as exc:
            _print_error(f"missing cloud config: {exc}")
            return 1
        if dry_run:
            plan = _plan_ensure_fetch(rel, meta, cfg)
            print(
                f"would fetch: {rel} ({plan['content_length_human'] if plan.get('content_length_human') else 'size unknown'}) "
                f"via {plan['transport']} from {cfg.provider}/{archive}"
            )
            if plan.get("blob_url"):
                print(f"  blob: {plan['blob_url']}")
            continue
        def _note_wait(rel: str = rel) -> None:
            print(
                f"[cloud-vfs ensure] another process is fetching {rel} — waiting …",
                flush=True,
            )

        with path_lock(rel, on_wait=_note_wait):
            # Re-check under the lock: a concurrent ensure may have just finished,
            # so we can reuse its result instead of paying for a duplicate download.
            if is_real_local(rel):
                print(f"local: {rel} (materialized by concurrent ensure — skipping fetch, no egress)")
                continue
            print(f"fetch: {rel} ({cfg.provider}/{archive})")
            progress_label = f"[cloud-vfs ensure] fetching {rel} ({cfg.provider}/{archive})"
            try:
                nbytes = _safe_fetch(rel, meta, archive, env, mcfg, progress_label=progress_label)
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


def cmd_local_release(
    paths: list[str],
    *,
    archive_override: str | None,
    dry_run: bool = False,
    force_excluded: bool = False,
) -> int:
    """Delete local bytes when remote blob already exists (idempotent offload)."""
    if not paths:
        print("Usage: cloud-vfs local-release <paths...>", file=sys.stderr)
        return 1
    return cmd_offload(
        paths,
        dry_run=dry_run,
        archive_override=archive_override,
        delete_local=True,
        verify_only=False,
        no_resume=False,
        release_only=True,
        force_excluded=force_excluded,
    )


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
    source_archive = (stub or {}).get("archive") or archive_from_entry(entry)
    out["source"] = {"archive": source_archive}
    out["target"] = source_target_hints(rel, source_archive)["target"]
    out["hints"] = source_target_hints(rel, source_archive)
    if entry:
        out["entry"] = {
            k: entry.get(k)
            for k in (
                "id",
                "archive",
                "blob_role",
                "provider",
                "status",
                "blob",
                "blob_prefix",
                "uploaded",
            )
            if entry.get(k) is not None
        }
    if stub:
        out["stub"] = stub
    remote_present = False
    content_length: int | None = None
    cfg = None
    if not local_present:
        out["fetch_cmd"] = fetch_cmd(rel)
        env = load_cloud_env()
        try:
            cfg = resolve_archive(env, manifest, source_archive)
            blob = (stub or {}).get("blob") or (entry or {}).get("blob")
            prefix = (stub or {}).get("blob_prefix") or (entry or {}).get("blob_prefix")
            out["provider"] = cfg.provider
            remote_present, content_length = _resolve_remote_fields(cfg, blob, prefix)
            out["remote_present"] = remote_present
            if content_length is not None:
                out["content_length"] = content_length
                out["content_length_human"] = fmt_bytes(content_length)
            if cfg.provider == "aws":
                if blob:
                    out["s3_url"] = f"s3://{cfg.bucket}/{blob}"
                elif prefix:
                    out["s3_prefix_url"] = f"s3://{cfg.bucket}/{prefix.rstrip('/')}/"
            else:
                base = (manifest.get(source_archive) or {}).get("base_url") or cfg.base_url
                if blob:
                    out["blob_url"] = f"{base}/{blob}".replace("//", "/").replace(":/", "://")
                elif prefix:
                    out["blob_prefix_url"] = f"{base}/{prefix}".replace("//", "/").replace(":/", "://")
        except (KeyError, ValueError):
            out["remote_present"] = False
        if ref_present:
            out["status_label"] = _status_label_offloaded(
                provider=(cfg.provider if cfg else "unknown"),
                archive=source_archive,
                rel=rel,
                remote_present=remote_present,
                content_length=content_length,
            )
    print(json.dumps(out, indent=2))
    return 0


def cmd_status(*, path: str | None, as_json: bool, drift: bool) -> int:
    if path is not None:
        try:
            rel = normalize_rel(path)
        except PathOutsideProjectError as exc:
            _print_error(exc)
            return 1
        stub = read_stub(rel)
        local_present = is_real_local(rel)
        ref_present = is_ref(rel) or (stub is not None and not local_present)
        if not ref_present and local_present:
            print(f"local: {rel}")
            return 0
        manifest = load_manifest()
        entry = find_entry(manifest, rel)
        source_archive = (stub or {}).get("archive") or archive_from_entry(entry)
        env = load_cloud_env()
        remote_present = False
        content_length: int | None = None
        provider = "unknown"
        try:
            cfg = resolve_archive(env, manifest, source_archive)
            provider = cfg.provider
            blob = (stub or {}).get("blob") or (entry or {}).get("blob")
            prefix = (stub or {}).get("blob_prefix") or (entry or {}).get("blob_prefix")
            remote_present, content_length = _resolve_remote_fields(cfg, blob, prefix)
        except (KeyError, ValueError, FileNotFoundError):
            pass
        if remote_present:
            state = "offloaded-remote-ok"
        elif ref_present:
            state = "offloaded-missing-remote"
        else:
            state = "unknown"
        label = _status_label_offloaded(
            provider=provider,
            archive=source_archive,
            rel=rel,
            remote_present=remote_present,
            content_length=content_length,
        )
        payload = {
            "path": rel,
            "state": state,
            "local_present": local_present,
            "is_ref": ref_present,
            "remote_present": remote_present,
            "status_label": label,
        }
        if content_length is not None:
            payload["content_length"] = content_length
            payload["content_length_human"] = fmt_bytes(content_length)
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            print(label)
            print(f"  state: {state}")
        return 0 if remote_present or not ref_present else 1
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


def offload_candidates(manifest: dict[str, Any], policy: dict[str, Any] | None = None) -> list[str]:
    policy = policy or load_policy()
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
        if is_excluded(rel, policy):
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


def _expand_offload_paths(paths: list[str], policy: dict[str, Any]) -> list[str]:
    """Expand mixed directories to their qualifying files so offload honors
    ``min_size`` at directory granularity (issue #33).

    A directory whose files are all >= the threshold (or whitelisted via
    ``offload_always_prefixes``) keeps the efficient whole-directory fast path. A
    directory with a mix of qualifying and sub-threshold files is expanded to just
    its qualifying files, so sub-threshold files are never uploaded or removed.
    Explicit file paths are kept as-is (offloading a file by name is explicit
    intent and is honored regardless of size).
    """
    from cloud_vfs.storage.inventory import should_index

    out: list[str] = []
    for raw in paths:
        try:
            rel = normalize_rel(raw)
        except PathOutsideProjectError:
            out.append(raw)
            continue
        src = abs_path(rel)
        if not (src.is_dir() and is_real_local(rel)):
            out.append(rel)
            continue
        qualifying: list[str] = []
        total = 0
        for file_rel, path in _iter_local_files(rel):
            total += 1
            if should_index(file_rel, path.stat().st_size, policy):
                qualifying.append(file_rel)
        if len(qualifying) == total:
            out.append(rel)  # whole dir qualifies — keep the dir-stub fast path
        elif qualifying:
            out.extend(sorted(qualifying))  # mixed — offload only qualifying files
        else:
            print(
                f"[cloud-vfs offload] {rel}: no files meet the offload threshold "
                "(min_size). Offload a file by path, or add the prefix to "
                "offload_always_prefixes in inventory-policy.json.",
                file=sys.stderr,
            )
    return out


def _offload_batch_exit(
    batch_job: dict[str, Any] | None,
    path_failures: int,
    *,
    changed: bool,
    manifest: dict[str, Any],
) -> int:
    if changed:
        save_manifest(manifest)
    if batch_job:
        print(f"[cloud-vfs offload] {format_job_summary(batch_job)}")
        if path_failures or job_has_failures(batch_job) or job_has_pending(batch_job):
            return 1
    return 1 if path_failures else 0


def cmd_offload(
    paths: list[str],
    *,
    dry_run: bool,
    archive_override: str | None,
    delete_local: bool,
    verify_only: bool = False,
    no_resume: bool = False,
    release_only: bool = False,
    force_excluded: bool = False,
) -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        _print_error(exc)
        return 1
    if verify_only and not paths:
        print("Usage: cloud-vfs offload --verify-only <paths...>", file=sys.stderr)
        return 1
    policy = load_policy()
    if paths and not verify_only and not force_excluded:
        blocked = []
        for raw in paths:
            try:
                rel = normalize_rel(raw)
            except PathOutsideProjectError:
                continue
            if is_excluded(rel, policy):
                blocked.append(rel)
        if blocked:
            print(
                "Refusing to offload path(s) under inventory-policy exclude_prefixes:",
                file=sys.stderr,
            )
            for rel in blocked:
                print(f"  {rel}", file=sys.stderr)
            print(
                "Stubbing an excluded prefix (e.g. a source tree) is almost never intended.\n"
                "Pass --force-excluded to offload anyway, or edit "
                ".cloud-vfs/inventory-policy.json.",
                file=sys.stderr,
            )
            return 1
    if not paths and not verify_only:
        paths = offload_candidates(manifest, policy)
        if not paths:
            print("Nothing local to offload.")
            print("  cloud-vfs scan              # find large files under data/")
            print("  cloud-vfs scan --add        # add them to manifest, then retry")
            return 0
        if dry_run:
            print("Would offload (confirm, then run without --dry-run):")

    if not verify_only:
        paths = _expand_offload_paths(paths, policy)
        if not paths:
            return 0

    batch_job: dict[str, Any] | None = None
    if len(paths) > 1 and not dry_run and not verify_only:
        use_archive = normalize_archive(archive_override or "local_archive")
        batch_job = load_offload_job(paths)
        if batch_job and (
            batch_job.get("archive") != use_archive
            or batch_job.get("delete_local") != delete_local
        ):
            clear_job_offload_progress(paths)
            batch_job = new_offload_job(
                paths, archive=use_archive, delete_local=delete_local
            )
        elif not batch_job:
            batch_job = new_offload_job(
                paths, archive=use_archive, delete_local=delete_local
            )
        save_offload_job(batch_job)
        print(f"[cloud-vfs offload] {format_job_summary(batch_job)}")

    changed = False
    path_failures = 0
    for raw in paths:
        try:
            rel = normalize_rel(raw)
        except PathOutsideProjectError as exc:
            _print_error(exc)
            if batch_job:
                set_job_path_status(batch_job, raw, STATUS_FAILED, error=str(exc))
                path_failures += 1
                continue
            return _offload_batch_exit(batch_job, path_failures, changed=changed, manifest=manifest)
        src = abs_path(rel)
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
            if batch_job:
                set_job_path_status(batch_job, rel, STATUS_FAILED, error=str(exc))
                path_failures += 1
                continue
            return _offload_batch_exit(batch_job, path_failures, changed=changed, manifest=manifest)

        if verify_only:
            if not src.exists():
                print(f"SKIP (missing): {rel}")
                continue
            blob_prefix = (entry.get("blob_prefix") if entry else None) or f"{rel.rstrip('/')}/"
            blob = entry.get("blob") if entry and src.is_file() else None
            result = verify_offload(rel, cfg, blob=blob, blob_prefix=blob_prefix if src.is_dir() else None)
            print(format_verify_report(result))
            continue

        if not src.exists() or not is_real_local(rel):
            stub = read_stub(rel)
            if stub or is_ref(rel):
                blob = (stub or {}).get("blob") or (entry.get("blob") if entry else None)
                blob_prefix = (stub or {}).get("blob_prefix") or (
                    entry.get("blob_prefix") if entry else None
                )
                remote_ok, rlen = _resolve_remote_fields(
                    cfg,
                    blob,
                    blob_prefix,
                )
                if remote_ok:
                    size_note = fmt_bytes(rlen) if rlen is not None else "present"
                    print(
                        f"OK: {rel} offloaded-remote-ok ({size_note} on "
                        f"{cfg.provider}/{use_archive})"
                    )
                    if batch_job:
                        mark_job_skipped_if_pending(batch_job, rel)
                    continue
                print(f"WARN: {rel} stub present but remote missing or unverified")
            print(f"SKIP (not local): {rel}")
            if batch_job:
                mark_job_skipped_if_pending(batch_job, rel)
            continue
        size = tree_size(src)
        if dry_run:
            if release_only:
                blob_key = rel if src.is_file() else None
                verified = blob_key and blob_matches_local_size(cfg, blob_key, src)
                if verified:
                    print(f"  would local-release: {rel}  {fmt_bytes(size)}  (remote verified, remove local)")
                else:
                    print(f"  would local-release: {rel}  {fmt_bytes(size)}  (remote NOT verified — cannot release)")
            else:
                transport = choose_azure_transport(size) if cfg.provider == "azure" else "aws-cli"
                print(
                    f"  would offload: {rel}  {fmt_bytes(size)}  -> {cfg.provider}/{use_archive} "
                    f"via {transport}"
                )
            continue

        precomputed = hash_paths_before_offload(rel)
        if release_only:
            blob_key = rel if src.is_file() else None
            if not blob_key or not blob_matches_local_size(cfg, blob_key, src):
                _print_error(
                    f"{rel}: remote blob missing or size mismatch — "
                    "run offload first or verify with offload --verify-only"
                )
                if batch_job:
                    set_job_path_status(batch_job, rel, STATUS_FAILED, error="remote not verified")
                path_failures += 1
                continue
            print(
                f"[cloud-vfs local-release] remote verified for {rel} "
                f"({fmt_bytes(size)}), removing local bytes …"
            )
            progress = new_offload_progress(
                rel,
                archive=use_archive,
                delete_local=True,
                precomputed=precomputed,
            )
            progress["uploaded"] = True
            save_offload_progress(progress)
        else:
            progress = None if no_resume else load_offload_progress(rel)
            if progress and (
                progress.get("archive") != use_archive or progress.get("delete_local") != delete_local
            ):
                print(f"[cloud-vfs offload] stale progress for {rel}, starting fresh")
                clear_offload_progress(rel)
                progress = None

            if progress:
                indexed = len(progress.get("indexed_files") or [])
                uploaded = progress.get("uploaded", False)
                print(
                    f"[cloud-vfs offload] resuming {rel} "
                    f"(uploaded={'yes' if uploaded else 'no'}, indexed={indexed})"
                )
                if not progress.get("precomputed"):
                    progress["precomputed"] = precomputed
            else:
                progress = new_offload_progress(
                    rel,
                    archive=use_archive,
                    delete_local=delete_local,
                    precomputed=precomputed,
                )
                save_offload_progress(progress)

        blob_prefix = (entry.get("blob_prefix") if entry else None) or f"{rel.rstrip('/')}/"
        action = "remove local after upload" if delete_local else "keep local after upload"
        progress_label = (
            f"[cloud-vfs offload] uploading {rel} -> {use_archive} ({fmt_bytes(size)})"
        )

        interrupt: OffloadInterruptState | None = None
        try:
            with _offload_interrupt_guard(manifest, progress) as guard:
                interrupt = guard
                manifest_dirty = False
                if not progress.get("uploaded"):
                    blob_key = rel if src.is_file() else None
                    if (
                        not no_resume
                        and src.is_file()
                        and blob_key
                        and blob_matches_local_size(cfg, blob_key, src)
                    ):
                        print(
                            f"[cloud-vfs offload] blob size matches local "
                            f"({fmt_bytes(size)}), skipping upload for {rel}"
                        )
                        progress["uploaded"] = True
                        save_offload_progress(progress)
                    else:
                        print(
                            f"offload: {rel} -> {cfg.provider}/{use_archive} "
                            f"({fmt_bytes(size)}, {action})"
                        )
                        upload_path(
                            rel,
                            use_archive,
                            env,
                            mcfg,
                            blob_prefix=blob_prefix,
                            progress_label=progress_label,
                        )
                        progress["uploaded"] = True
                        save_offload_progress(progress)
                        if src.is_file():
                            file_rel = normalize_rel(rel)
                            digest = precomputed.get(file_rel) or precomputed.get(rel)
                            if digest:
                                print(
                                    f"[cloud-vfs offload] uploaded {fmt_bytes(size)} bytes, "
                                    f"local sha256 {digest}"
                                )
                        if delete_local:
                            print(f"[cloud-vfs offload] upload complete, writing stub for {rel} …")
                        else:
                            print(
                                f"[cloud-vfs offload] upload complete, "
                                f"updating inventory for {rel} …"
                            )
                else:
                    print(f"[cloud-vfs offload] upload already complete, skipping upload for {rel}")

                entry = ensure_manifest_entry(
                    manifest,
                    rel,
                    archive=use_archive,
                    provider=cfg.provider,
                    is_dir=src.is_dir(),
                    blob=rel if src.is_file() else None,
                    blob_prefix=blob_prefix if src.is_dir() else None,
                )
                manifest_dirty = True
                guard.on_flush = lambda: save_manifest(manifest) if manifest_dirty else None
                meta: dict[str, Any] = {
                    "manifest_id": entry.get("id"),
                    "archive": use_archive,
                    "provider": cfg.provider,
                }
                indexed_files: set[str] = set(progress.get("indexed_files") or [])

                def _on_indexed(file_rel: str) -> None:
                    if file_rel not in progress["indexed_files"]:
                        progress["indexed_files"].append(file_rel)
                    save_offload_progress(progress)

                if src.is_dir():
                    meta["blob_prefix"] = entry.get("blob_prefix") or rel.rstrip("/") + "/"
                    index_offloaded_path(
                        rel,
                        archive=use_archive,
                        provider=cfg.provider,
                        blob=None,
                        blob_prefix=meta["blob_prefix"],
                        entry=entry,
                        precomputed=precomputed,
                        keep_local=not delete_local,
                        skip_files=indexed_files,
                        on_file_indexed=_on_indexed,
                    )
                else:
                    meta["blob"] = entry.get("blob") or rel
                    index_offloaded_path(
                        rel,
                        archive=use_archive,
                        provider=cfg.provider,
                        blob=meta["blob"],
                        blob_prefix=None,
                        entry=entry,
                        precomputed=precomputed,
                        keep_local=not delete_local,
                        skip_files=indexed_files,
                        on_file_indexed=_on_indexed,
                        force_index=True,
                    )

                if delete_local and not progress.get("stubbed"):
                    mark_offloaded(entry)
                    if src.is_dir():
                        _write_dir_stub_after_upload(rel, meta)
                    else:
                        write_stub(rel, meta)
                    progress["stubbed"] = True
                    save_offload_progress(progress)
                elif not delete_local:
                    entry["status"] = "synced"

                if not progress.get("manifest_saved"):
                    progress["manifest_saved"] = True
                    save_offload_progress(progress)

            clear_offload_progress(rel)
            if delete_local:
                print(f"OK: {rel} uploaded, local removed (freed {fmt_bytes(size)})")
            else:
                print(f"OK: {rel} uploaded, local kept ({fmt_bytes(size)} on disk)")
            if batch_job:
                set_job_path_status(batch_job, rel, STATUS_STUBBED)
            changed = True
        except (CloudStorageError, ValueError, FileNotFoundError, OSError) as exc:
            _print_error(exc)
            if interrupt is not None:
                interrupt.flush()
            if batch_job:
                set_job_path_status(batch_job, rel, STATUS_FAILED, error=str(exc))
            path_failures += 1
            continue
    return _offload_batch_exit(batch_job, path_failures, changed=changed, manifest=manifest)


def cmd_cleanup_downloads(*, dry_run: bool, older_than_hours: float | None) -> int:
    """Remove stale .azDownload-*/.part/fetch-* temps left by interrupted downloads (issue #21)."""
    from cloud_vfs.storage.cleanup import cleanup_download_temps

    matched, removed, freed = cleanup_download_temps(
        older_than_hours=older_than_hours, dry_run=dry_run
    )
    if not matched:
        scope = "" if older_than_hours is None else f" older than {older_than_hours:g}h"
        print(f"No download temps{scope} under {temp_dir()}")
        return 0
    for path, size in matched:
        print(f"  {fmt_bytes(size):>9}  {path}")
    if dry_run:
        print(
            f"\nWould remove {len(matched)} temp(s), freeing {fmt_bytes(freed)} "
            "(re-run without --dry-run to delete)."
        )
    else:
        print(f"\nremoved {removed} temp(s), freed {fmt_bytes(freed)}")
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

    p_ensure = sub.add_parser(
        "ensure",
        help="Materialize cloud source into target (project root or --target-root)",
    )
    p_ensure.add_argument("paths", nargs="+")
    p_ensure.add_argument(
        "--check-only",
        action="store_true",
        help="Exit non-zero if any path is still a stub/ref (preflight, no download)",
    )
    p_ensure.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded (size, archive, transport) without fetching",
    )
    p_ensure.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip sha256 check against inventory after download (project target only)",
    )
    p_source = p_ensure.add_mutually_exclusive_group()
    p_source.add_argument(
        "--source",
        dest="source_archive",
        type=archive_cli_arg,
        help="Cloud blob backend to read from (alias: --archive)",
    )
    p_source.add_argument(
        "--archive",
        dest="source_archive",
        type=archive_cli_arg,
        help=argparse.SUPPRESS,
    )
    p_ensure.add_argument(
        "--target-root",
        type=Path,
        help="Filesystem root for materialized files (default: project root)",
    )
    p_ensure.add_argument(
        "--paths-file",
        type=Path,
        help="Newline-separated paths (with --target-root)",
    )
    p_ensure.add_argument(
        "--manifest",
        type=Path,
        help="Manifest JSON for blob mapping (with --target-root)",
    )
    p_ensure.add_argument("--config-env", type=Path, help="Override config.env (with --target-root)")
    p_ensure.add_argument("--secrets-env", type=Path, help="Override secrets.env (with --target-root)")
    p_ensure.add_argument(
        "--ref-root",
        type=Path,
        help="Project root for reading inline cvfs refs (with --target-root)",
    )

    p_resolve = sub.add_parser("resolve", help="JSON fetch instructions")
    p_resolve.add_argument("path")

    p_status = sub.add_parser("status", help="Local vs stub + sizes")
    p_status.add_argument("path", nargs="?", help="Single path: offloaded-remote-ok vs missing-remote")
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

    p_preflight = sub.add_parser(
        "preflight",
        help="Exit non-zero if paths are still cloud stubs/refs",
    )
    p_preflight.add_argument("paths", nargs="+")
    p_preflight.add_argument("--json", action="store_true")

    p_ingest = sub.add_parser(
        "ingest",
        help="Upload local source file to cloud target path (one-shot, no prior register)",
    )
    p_ingest.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Local file path (need not be under project root)",
    )
    p_ingest.add_argument(
        "--target",
        required=True,
        help="Project-relative blob key / manifest path",
    )
    p_ingest_source = p_ingest.add_mutually_exclusive_group()
    p_ingest_source.add_argument(
        "--source-archive",
        default="local_archive",
        dest="source_archive",
        type=archive_cli_arg,
        help="Cloud backend to write to",
    )
    p_ingest_source.add_argument(
        "--archive",
        dest="source_archive",
        type=archive_cli_arg,
        help=argparse.SUPPRESS,
    )
    p_ingest.add_argument("--dry-run", action="store_true")
    p_ingest.add_argument(
        "--no-stub",
        action="store_true",
        help="Upload + manifest only; do not write inline ref at --target path",
    )
    p_ingest.add_argument(
        "--no-index",
        action="store_true",
        help="Skip inventory row (manifest only)",
    )

    p_offload = sub.add_parser("offload", help="Upload + stub (explicit paths; use --dry-run first)")
    p_offload.add_argument("paths", nargs="*")
    p_offload.add_argument("--dry-run", action="store_true")
    p_offload_source = p_offload.add_mutually_exclusive_group()
    p_offload_source.add_argument(
        "--source",
        dest="source_archive",
        type=archive_cli_arg,
        help="Cloud backend to upload to",
    )
    p_offload_source.add_argument(
        "--archive",
        dest="source_archive",
        type=archive_cli_arg,
        help=argparse.SUPPRESS,
    )
    p_offload.add_argument(
        "--verify-only",
        action="store_true",
        help="Compare local paths to blob storage without modifying anything",
    )
    p_offload.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore .cloud-vfs/offload-progress/ and start from scratch",
    )
    p_offload.add_argument(
        "--force-excluded",
        action="store_true",
        help="Allow offloading explicit paths under inventory-policy exclude_prefixes",
    )
    local_group = p_offload.add_mutually_exclusive_group()
    local_group.add_argument(
        "--delete-local",
        dest="delete_local",
        action="store_true",
        help="Remove local files after confirmed upload and write stub (default)",
    )
    local_group.add_argument(
        "--keep-local",
        dest="delete_local",
        action="store_false",
        help="Upload to cloud and update inventory but keep local files",
    )
    p_offload.set_defaults(delete_local=True)

    p_release = sub.add_parser(
        "local-release",
        help="Remove local bytes when remote blob already verified (single files only)",
    )
    p_release.add_argument("paths", nargs="+")
    p_release.add_argument("--dry-run", action="store_true")
    p_release_source = p_release.add_mutually_exclusive_group()
    p_release_source.add_argument(
        "--source",
        dest="source_archive",
        type=archive_cli_arg,
        help="Cloud backend to verify against",
    )
    p_release_source.add_argument(
        "--archive",
        dest="source_archive",
        type=archive_cli_arg,
        help=argparse.SUPPRESS,
    )
    p_release.add_argument(
        "--force-excluded",
        action="store_true",
        help="Allow releasing paths under inventory-policy exclude_prefixes",
    )

    p_cleanup = sub.add_parser(
        "cleanup-downloads",
        help="Remove stale download temps (.azDownload-*/.part/fetch-*) from interrupted fetches",
    )
    p_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="List temps and reclaimable bytes without deleting",
    )
    p_cleanup.add_argument(
        "--older-than-hours",
        type=float,
        default=None,
        metavar="N",
        help="Only remove temps older than N hours (default: all incomplete temps)",
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
        source = (
            normalize_archive(args.source_archive) if getattr(args, "source_archive", None) else None
        )
        return cmd_ensure(
            args.paths,
            verify=not args.no_verify,
            check_only=args.check_only,
            dry_run=getattr(args, "dry_run", False),
            source_archive=source,
            target_root=getattr(args, "target_root", None),
            paths_file=getattr(args, "paths_file", None),
            manifest_file=getattr(args, "manifest", None),
            config_env=getattr(args, "config_env", None),
            secrets_env=getattr(args, "secrets_env", None),
            ref_root=getattr(args, "ref_root", None),
        )
    if args.cmd == "preflight":
        from cloud_vfs.materialize import cmd_preflight

        return cmd_preflight(args.paths, as_json=args.json)
    if args.cmd == "ingest":
        from cloud_vfs.materialize import cmd_ingest

        return cmd_ingest(
            args.source,
            args.target,
            source_archive=normalize_archive(args.source_archive),
            dry_run=args.dry_run,
            emit_stub=not args.no_stub,
            index_inventory=not args.no_index,
        )
    if args.cmd == "resolve":
        return cmd_resolve(args.path)
    if args.cmd == "status":
        return cmd_status(path=getattr(args, "path", None), as_json=args.json, drift=args.drift)
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
            archive_override=(
                normalize_archive(args.source_archive) if getattr(args, "source_archive", None) else None
            ),
            delete_local=args.delete_local,
            verify_only=args.verify_only,
            no_resume=args.no_resume,
            force_excluded=args.force_excluded,
        )
    if args.cmd == "local-release":
        return cmd_local_release(
            args.paths,
            archive_override=(
                normalize_archive(args.source_archive) if getattr(args, "source_archive", None) else None
            ),
            dry_run=args.dry_run,
            force_excluded=args.force_excluded,
        )
    if args.cmd == "cleanup-downloads":
        return cmd_cleanup_downloads(
            dry_run=args.dry_run,
            older_than_hours=args.older_than_hours,
        )
    if args.cmd == "materialize-stubs":
        return cmd_materialize_stubs()
    if args.cmd == "doctor":
        return cmd_doctor(as_json=args.json, probe=args.probe, roundtrip=args.roundtrip)
    return 1
