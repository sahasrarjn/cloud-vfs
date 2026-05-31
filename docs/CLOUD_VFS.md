# cloud-vfs workflow

Large files live in cloud storage. The machine keeps **inline refs** (single files at the original path) and **`.cloudstub`** directory pointers, plus a **per-file inventory** under `.cloud-vfs/index/`.

## Architecture

```
POLICY (human/agent, git)          INVENTORY (tools only)
─────────────────────────          ───────────────────────
.cloud-vfs/manifest.json           .cloud-vfs/index/data/run.json
.cloud-vfs/inventory-policy.json     local ↔ blob ↔ sha256 ↔ etag ↔ state
  archive routing
  required-local paths
  folder-level status
```

## Lifecycle

```
Create large file   →  register <path>           →  index as local (+ sha256)
Need file           →  ensure <path>             →  fetch file or tree
Audit               →  reconcile                 →  drift report
Fix ephemeral index →  reconcile --from-blob --fix-index
Clean stale rows    →  prune
Choose offload      →  offload --dry-run
Offload             →  offload <paths>           →  hash, upload, index, stub
Agent lookup        →  resolve <path>            →  JSON with blob_url
```

## Commands

```bash
# Index local large files (no upload)
cloud-vfs register data/embeddings.npy
cloud-vfs register data/generated/new_run

# Fetch (verifies sha256 vs inventory by default)
cloud-vfs ensure data/generated/old_run
cloud-vfs ensure data/embeddings.npy
cloud-vfs ensure --check-only data/train.csv
cloud-vfs ensure data/foo.npy --source remote_staging

# Custom target root (no project inventory on host) — see SOURCE_TARGET.md
cloud-vfs ensure --target-root /workspace --source remote_staging data/foo.npy

# Local source file → cloud target path
cloud-vfs ingest --source /tmp/model_best.pth --target research/runs/model_best.pth

# Safe delete check (blocks prod-bucket hallucinations)
cloud-vfs guard data/embeddings.npy

# Inspect (no download)
cloud-vfs resolve data/generated/old_run

# Status + drift
cloud-vfs status --drift

# Reconcile
cloud-vfs reconcile
cloud-vfs reconcile --from-blob
cloud-vfs reconcile --repair-stubs
cloud-vfs reconcile --orphan-blobs
cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/

# Offload
cloud-vfs offload --dry-run
cloud-vfs offload data/generated/old_run

# Maintenance
cloud-vfs prune
cloud-vfs materialize-stubs
```

## Inventory shard

Path: `.cloud-vfs/index/<shard_root>.json`

```json
{
  "version": 1,
  "shard_root": "data/generated/my_run",
  "updated_at": "2026-05-28T12:00:00Z",
  "files": {
    "data/generated/my_run/train.csv": {
      "local": "data/generated/my_run/train.csv",
      "blob": "data/generated/my_run/train.csv",
      "archive": "local_archive",
      "state": "cloud-only",
      "size": 119495247,
      "sha256": "a1b2c3…",
      "etag": "0x8D…",
      "uploaded_at": "2026-05-28T12:00:00Z",
      "policy_id": "my-run"
    }
  }
}
```

**States:** `local`, `synced`, `cloud-only`, `pending-upload`, `orphan-local`, `orphan-cloud`

Only files meeting `inventory-policy.json` thresholds get rows. Small members of offloaded trees fetch via **`blob_prefix`** on the dir stub.

## Hybrid refs (v0.4)

| Artifact | Local after offload | Agent reads |
|----------|---------------------|-------------|
| Single file | JSON ref **at path** (`data/foo.npy`) | `"cvfs": 1` → run `ensure` |
| Directory tree | `data/run/.cloudstub` | sidecar JSON → run `ensure` |

Inventory (`.cloud-vfs/index/`) remains source of truth; inline refs are a denormalized agent cache.

## Inline file ref

Written at `data/embeddings.npy` after offload:

```json
{
  "cvfs": 1,
  "type": "cloud-blob-ref",
  "version": 2,
  "placement": "inline",
  "local": "data/embeddings.npy",
  "blob": "data/embeddings.npy",
  "archive": "local_archive",
  "fetch_cmd": "cloud-vfs ensure data/embeddings.npy"
}
```

## Directory sidecar (`.cloudstub`)

Directory offloaded:

```json
{
  "cvfs": 1,
  "type": "cloud-dir-ref",
  "version": 2,
  "placement": "sidecar",
  "local": "data/generated/my_run",
  "archive": "local_archive",
  "shard_root": "data/generated/my_run",
  "index": ".cloud-vfs/index/data/generated/my_run.json",
  "blob_prefix": "data/generated/my_run/",
  "manifest_id": "my-run",
  "fetch_cmd": "cloud-vfs ensure data/generated/my_run"
}
```

Single file offloaded:

```json
{
  "cvfs": 1,
  "type": "cloud-blob-ref",
  "version": 2,
  "placement": "inline",
  "local": "data/embeddings.npy",
  "blob": "data/embeddings.npy",
  "archive": "local_archive",
  "fetch_cmd": "cloud-vfs ensure data/embeddings.npy"
}
```

## Drift types (`reconcile`)

| Issue | Meaning |
|-------|---------|
| `orphan-local` | On disk, not in inventory (and above min size) |
| `ghost-index` | Indexed cloud-only, blob missing |
| `hash-mismatch` | Local sha256 ≠ inventory |
| `orphan-blob` | On cloud-vfs bucket under policy prefix, not in inventory |
| `stale-stub` | Inventory says cloud-only but dir sidecar missing |
| `stale-inline-ref` | Inventory `local` but path is still an inline ref |
| `ref-inventory-mismatch` | Stub `blob` ≠ inventory `blob` |
| `local-index-mismatch` | Inventory `cloud-only` but real local bytes present |

Legacy `*.cloudstub` file sidecars migrate to inline refs via `materialize-stubs` or `ensure`.

## Trade-offs

**Pros:** Explicit cloud path per large file; sha256 before delete; partial fetch; agent-safe dry-run.

**Cons:** Cold reads hit network; manual offload approval; inventory must be pruned occasionally.

## See also

- [INVENTORY.md](INVENTORY.md) — policy knobs and git hygiene
- [AGENTS.md](AGENTS.md) — agent rules
- [ROBUSTNESS.md](ROBUSTNESS.md) — two-bucket safety, guard, verify
