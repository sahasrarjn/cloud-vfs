from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root
from cloud_vfs.storage.inventory import load_policy, should_index
from cloud_vfs.storage.env import load_cloud_env
from cloud_vfs.storage.manifest import ensure_manifest_entry, find_entry, load_manifest, save_manifest
from cloud_vfs.storage.paths import STUB_NAME, abs_path, is_real_local, normalize_rel


def _fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and p.name != STUB_NAME:
            total += p.stat().st_size
    return total


def discover_large_local(policy: dict[str, Any]) -> list[dict[str, Any]]:
    root = project_root()
    rows: list[dict[str, Any]] = []
    skip_parts = {".cloud-vfs", ".git", ".cursor", "node_modules", "__pycache__", ".venv", "venv"}

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_parts for part in path.parts):
            continue
        if path.name == STUB_NAME or path.name.endswith(".cloudstub"):
            continue
        rel = normalize_rel(path.relative_to(root))
        if not is_real_local(rel):
            continue
        size = path.stat().st_size
        if not should_index(rel, size, policy):
            continue
        rows.append({"path": rel, "size": size, "kind": "file"})

    # Suggest directory roots: data/<segment>/ when multiple large files share prefix
    by_root: dict[str, int] = defaultdict(int)
    for row in rows:
        parts = Path(row["path"]).parts
        if len(parts) >= 2 and parts[0] == "data":
            group = "/".join(parts[:2]) + ("/" if len(parts) > 2 else "")
            by_root[group.rstrip("/")] += row["size"]
        else:
            parent = str(Path(row["path"]).parent)
            if parent and parent != ".":
                by_root[parent] += row["size"]

    dir_rows: list[dict[str, Any]] = []
    seen_files = {r["path"] for r in rows}
    for group, total in sorted(by_root.items(), key=lambda x: -x[1]):
        src = abs_path(group)
        if not src.exists():
            continue
        if src.is_file():
            continue
        if group in seen_files:
            continue
        if _tree_size(src) < total * 0.5:
            continue
        dir_rows.append({"path": group, "size": _tree_size(src), "kind": "dir"})

    combined = rows + dir_rows
    combined.sort(key=lambda r: -r["size"])
    return combined


def _manifest_label(manifest: dict[str, Any], rel: str) -> str:
    entry = find_entry(manifest, rel)
    if not entry:
        return "not in manifest"
    status = entry.get("status", "?")
    if is_real_local(rel):
        return f"manifest:{status}, local"
    return f"manifest:{status}, cloud-only"


def cmd_scan(*, as_json: bool, add: bool, prefix: str | None) -> int:
    try:
        manifest = load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Run: cloud-vfs init --path .", file=sys.stderr)
        return 1

    policy = load_policy()
    min_mb = int(policy.get("min_size_bytes", 52_428_800)) / (1024 * 1024)
    includes = ", ".join(policy.get("include_prefixes") or ["data/"])

    discovered = discover_large_local(policy)
    if prefix:
        norm = prefix.rstrip("/") + "/"
        discovered = [r for r in discovered if r["path"] == prefix.rstrip("/") or r["path"].startswith(norm)]

    payload: dict[str, Any] = {
        "project_root": str(project_root()),
        "policy": {"include_prefixes": includes, "min_size_mb": round(min_mb, 1)},
        "candidates": [],
    }

    env = load_cloud_env()
    block = manifest.get("local_archive") or {}
    provider = str(block.get("provider") or env.get("LOCAL_PROVIDER") or "azure")

    added = 0
    for row in discovered:
        rel = row["path"]
        label = _manifest_label(manifest, rel)
        item = {
            "path": rel,
            "size": row["size"],
            "size_human": _fmt_bytes(row["size"]),
            "kind": row["kind"],
            "manifest": label,
            "in_manifest": find_entry(manifest, rel) is not None,
            "local": is_real_local(rel),
        }
        payload["candidates"].append(item)

        if add and not item["in_manifest"] and item["local"]:
            src = abs_path(rel)
            ensure_manifest_entry(
                manifest,
                rel,
                archive="local_archive",
                provider=provider,
                is_dir=src.is_dir(),
            )
            entry = find_entry(manifest, rel)
            if entry:
                entry["status"] = "offload-candidate"
            added += 1

    if add and added:
        save_manifest(manifest)

    if as_json:
        payload["added_to_manifest"] = added
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Project: {project_root()}")
    print(f"Policy: {includes}  (>= {min_mb:.0f} MB unless prefix override)\n")

    if not discovered:
        print("No large local files in scope.")
        print("  • Put artifacts under data/ (or edit inventory-policy.json)")
        print("  • Or lower min_size_bytes for testing")
        return 0

    print(f"{'size':>10}  {'kind':4}  {'manifest':28}  path")
    for item in payload["candidates"]:
        print(
            f"{item['size_human']:>10}  {item['kind']:4}  {item['manifest']:28}  {item['path']}"
        )

    untracked = [c for c in payload["candidates"] if not c["in_manifest"] and c["local"]]
    local_manifest = [c for c in payload["candidates"] if c["in_manifest"] and c["local"]]

    print()
    if untracked:
        print(f"{len(untracked)} path(s) not in manifest yet.")
        print("  cloud-vfs scan --add          # add as offload-candidate")
    if local_manifest or untracked:
        print("  cloud-vfs offload --dry-run   # preview upload + stubs")
        print("  cloud-vfs offload <path>      # after you confirm")
    if add:
        print(f"\nAdded {added} manifest entr{'y' if added == 1 else 'ies'}.")
    return 0
