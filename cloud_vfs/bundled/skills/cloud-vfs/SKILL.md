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

**Generic model:** **source** = cloud archive (`--source`), **target** = where files materialize (project root or `--target-root`). Do not add GPU/Mac/product-specific APIs — see repo [docs/DESIGN.md](https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/DESIGN.md).

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
| Setup any repo | `cloud-vfs init --path .` |
| Verify setup | `cloud-vfs doctor` / `doctor --roundtrip` |
| Before deleting local files | `cloud-vfs guard <path>` (required) |
| Fetch + verify | `cloud-vfs ensure <path>` |
| Preview fetch | `cloud-vfs ensure --dry-run <path>` |
| Preflight stubs | `cloud-vfs preflight <paths>` |
| Materialize at other root | `cloud-vfs ensure --target-root <DIR> [--source ARCHIVE] <paths>` |
| Ingest external file | `cloud-vfs ingest --source <file> --target <rel>` |
| Find offload candidates | `cloud-vfs scan` / `scan --add` |
| Index new local files | `cloud-vfs register <path>` |
| Inspect blob path | `cloud-vfs resolve <path>` |
| Status + drift | `cloud-vfs status --drift` |
| Audit | `cloud-vfs reconcile` |
| Drop sub-threshold rows | `cloud-vfs prune` |
| Rebuild ephemeral index | `cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/` |
| Preview offload | `cloud-vfs offload --dry-run` |
| Offload | `cloud-vfs offload <path>...` |
| Release local (remote ok) | `cloud-vfs local-release <path>...` |

## Agent rules

**Offloaded ≠ missing.** The path still exists at its original location — it holds a stub/ref with fetch instructions. Run `cloud-vfs ensure <path>` before reading binary data.

1. **Discover:** `cloud-vfs resolve <path>` — check `is_ref`, `remote_present`, `content_length`, `fetch_cmd`
2. **Need bytes?** `cloud-vfs ensure <path>` (or `ensure --dry-run` to preview transport)
3. **Free disk after task?** `cloud-vfs offload <path>` or `local-release <path>` when remote already verified
4. **Inline refs:** If reading a path returns JSON with `"cvfs": 1`, run `ensure` before treating it as binary (numpy, pandas, etc.)
5. **Directory stubs:** If a directory contains only `.cloudstub`, run `ensure` on the directory path
6. Before reading other cloud-only paths: `ensure <path>`
7. After creating outputs ≥ min size: `register <path>`
8. Before offloading: **always** `offload --dry-run` and get user confirmation
9. After compute runs: `reconcile`
10. **Never** hand-edit inventory JSON

### Large blob transport (Azure)

For blobs ≥ 100 MB, cloud-vfs uses **azcopy** (parallel I/O). Install azcopy v10 and keep `az` CLI for metadata. AWS paths continue to use `aws s3 cp/sync`.

## Inventory row (per large file)

Each row: `local`, `blob`, `archive`, `sha256`, `etag`, `state`. Offload hashes **before** delete.

## Git

- Commit benchmark inventory shards listed in `committed_prefixes`
- Gitignore `ephemeral_prefixes` (e.g. `data/generated/`) — rebuild with `reconcile --from-blob --fix-index`

Docs: https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/CLOUD_VFS.md
