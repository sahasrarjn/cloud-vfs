from __future__ import annotations

import json
import sys
from typing import Any

from cloud_vfs.project import manifest_path, project_root
from cloud_vfs.storage.inventory import find_row, iter_inventory_rows, load_policy
from cloud_vfs.storage.manifest import find_entry, load_manifest
from cloud_vfs.storage.paths import is_real_local, normalize_rel
from cloud_vfs.storage.stub import is_ref, read_stub


def assess_delete_safety(rel: str) -> dict[str, Any]:
    """Whether it is safe to delete the *local* bytes at rel (agent/human guardrail)."""
    rel = normalize_rel(rel)
    policy = load_policy()
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError):
        manifest = {"entries": []}

    entry = find_entry(manifest, rel)
    inv = find_row(rel, policy)
    stub = read_stub(rel)
    real_local = is_real_local(rel)
    ref = is_ref(rel) or stub is not None

    managed = entry is not None or inv is not None
    inv_state = inv[1].get("state") if inv else None
    inv_blob = inv[1].get("blob") if inv else None

    reasons: list[str] = []
    safe = False

    if real_local:
        reasons.append("REAL_LOCAL_BYTES: file on disk is real data, not a cloud-vfs ref")
        if not managed:
            reasons.append(
                "NOT_MANAGED_BY_CLOUD_VFS: no manifest/inventory row — "
                "uploads to other buckets (e.g. prod) are invisible to this tool"
            )
        else:
            reasons.append(
                "MANAGED_BUT_LOCAL: cloud-vfs tracks this path but bytes are still local — "
                "run offload (and verify) before deleting"
            )
    elif ref or inv_state == "cloud-only":
        if managed and inv_state == "cloud-only":
            safe = True
            reasons.append(
                "CLOUD_VFS_CLOUD_ONLY: inventory marks cloud-only; local is a ref/stub — "
                "no real local bytes to delete (already offloaded via cloud-vfs)"
            )
        elif ref and not managed:
            reasons.append(
                "UNMANAGED_REF: looks like a cloud ref but not in cloud-vfs inventory — do not trust for delete"
            )
        else:
            reasons.append("STUB_WITHOUT_INVENTORY: ref present but inventory not cloud-only — run reconcile")
    else:
        reasons.append("MISSING: path not found or empty")
        if not managed:
            reasons.append("NOT_MANAGED_BY_CLOUD_VFS")

    return {
        "path": rel,
        "project_root": str(project_root()),
        "manifest": str(manifest_path()),
        "managed_by_cloud_vfs": managed,
        "manifest_entry": entry is not None,
        "inventory_row": inv is not None,
        "inventory_state": inv_state,
        "inventory_blob": inv_blob,
        "real_local": real_local,
        "is_ref": ref,
        "safe_to_delete_local": safe,
        "reasons": reasons,
        "agent_rule": (
            "Never delete local files based on prod/other-bucket claims. "
            "Only trust cloud-vfs after: register → offload --dry-run → offload → "
            "guard shows CLOUD_VFS_CLOUD_ONLY."
        ),
    }


def cmd_guard(paths: list[str], *, as_json: bool) -> int:
    if not paths:
        print("Usage: cloud-vfs guard <paths...>", file=sys.stderr)
        return 1

    results = [assess_delete_safety(p) for p in paths]
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            flag = "SAFE" if r["safe_to_delete_local"] else "BLOCK"
            print(f"[{flag}] {r['path']}")
            for reason in r["reasons"]:
                print(f"        {reason}")
        print()
        print(results[0]["agent_rule"])

    blocked = [r for r in results if not r["safe_to_delete_local"] and r["real_local"]]
    # Exit 1 if any path has real local bytes (the prod-blob hallucination case)
    if blocked:
        return 1
    return 0
