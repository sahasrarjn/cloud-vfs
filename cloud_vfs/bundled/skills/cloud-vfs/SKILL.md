---
name: cloud-vfs
description: >-
  Operate cloud-vfs: cloud blob paths with per-file inventory, lazy fetch,
  reconcile drift, and dry-run offload. Use when configuring .cloud-vfs/, fetching
  cloud-only files, registering large outputs, or offloading project data.
---

# cloud-vfs

Install: `pip install cloud-vfs` (or `pip install git+https://github.com/sahasrarjn/cloud-vfs.git`)

Large files live in cloud storage. Local disk holds **inline refs** (single files at the original path) or **`.cloudstub`** directory pointers, plus a **per-file inventory** under `.cloud-vfs/index/`.

## Two layers

| Layer | File | Who edits |
|-------|------|-----------|
| Policy | `.cloud-vfs/manifest.json`, `inventory-policy.json` | Human/agent |
| Inventory | `.cloud-vfs/index/<root>.json` | **Tools only** |

## Tracking scope

Large **`data/` artifacts only** (default ≥ 50 MB). Code excluded — see `inventory-policy.json`.

| Task | Command |
|------|---------|
| Learn in sandbox | `cloud-vfs try` then `cd cloud-vfs-try` |
| Verify setup | `cloud-vfs doctor` / `doctor --roundtrip` |
| Index new local files | `cloud-vfs register <path>` |
| Fetch (file or tree) | `cloud-vfs ensure <path>` |
| Inspect blob path | `cloud-vfs resolve <path>` |
| Status + drift | `cloud-vfs status --drift` |
| Audit | `cloud-vfs reconcile` |
| Drop sub-threshold rows | `cloud-vfs prune` |
| Rebuild ephemeral index | `cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/` |
| Preview offload | `cloud-vfs offload --dry-run` |
| Offload | `cloud-vfs offload <path>...` |

## Agent rules

1. **Inline refs:** If reading a path returns JSON with `"cvfs": 1`, run `cloud-vfs ensure <path>` before treating it as binary data (numpy, pandas, etc.)
2. **Directory stubs:** If a directory contains only `.cloudstub`, run `ensure` on the directory path
3. Before reading other cloud-only paths: `ensure <path>`
4. After creating outputs ≥ min size: `register <path>`
5. Before offloading: **always** `offload --dry-run` and get user confirmation
6. After compute runs: `reconcile`
7. **Never** hand-edit inventory JSON

## Inventory row (per large file)

Each row: `local`, `blob`, `archive`, `sha256`, `etag`, `state`. Offload hashes **before** delete.

## Git

- Commit benchmark inventory shards listed in `committed_prefixes`
- Gitignore `ephemeral_prefixes` (e.g. `data/generated/`) — rebuild with `reconcile --from-blob --fix-index`

Docs: https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/CLOUD_VFS.md
