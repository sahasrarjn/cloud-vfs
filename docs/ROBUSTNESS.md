# Robustness and safety

cloud-vfs is **not** a full filesystem. It is a **manual, path-keyed** layer over one configured bucket/archive. Robustness comes from checks, repair commands, and **never trusting uploads outside this tool**.

## Linux-style guarantees (what we implement)

| Idea | Command / behavior |
|------|-------------------|
| **fsck** | `reconcile`, `reconcile --from-blob`, `reconcile --repair-stubs` |
| **fsync / verify read** | `ensure` verifies sha256 vs inventory (use `--no-verify` to skip) |
| **lost+found** | `reconcile --orphan-blobs` lists unindexed blobs in **cloud-vfs bucket only** |
| **safe unlink** | `guard <path>` blocks deleting real local bytes unless cloud-vfs says cloud-only |

## Two buckets (cloud-vfs vs prod)

**Scenario:** `fileA` exists locally and was uploaded to a **prod** bucket by another pipeline. cloud-vfs has **no** manifest row, **no** inventory row, and **never** talks to prod.

| If an agent says… | Reality |
|-------------------|---------|
| “It’s in the blob, safe to delete locally” | **Unsafe** unless `cloud-vfs guard fileA` passes |
| `guard` on real local `fileA` | **BLOCK** — `NOT_MANAGED_BY_CLOUD_VFS` |
| `resolve fileA` | `managed_by_cloud_vfs: false`, `safe_to_delete_local: false` |

cloud-vfs **only** scans/archives configured in `.cloud-vfs/config.env` (`local_archive` / `remote_staging`). It cannot see prod.

**Safe local delete** only when:

1. Path was offloaded **through cloud-vfs** (`offload` after dry-run), and  
2. Local path is already a ref/stub (no real bytes), and  
3. Inventory state is `cloud-only`.

```bash
cloud-vfs guard data/fileA    # must not BLOCK with REAL_LOCAL_BYTES
```

Agents must run **`guard` before any delete** of large `data/` paths.

## Re-offload and moves

| Case | Cloud copies |
|------|----------------|
| Same path: offload → ensure → offload | **One** blob key; overwrite |
| Rename without `scan`/manifest update | **Orphan** blob at old key; use `reconcile --orphan-blobs` |
| Same bytes, two paths | **Two** blobs (path-keyed, not content-deduped) |

## Large-file offload (batch + resume)

| Scenario | Behavior |
|----------|----------|
| Multi-path `offload` | Job file in `.cloud-vfs/jobs/offload-<id>.json` tracks each path; failures do not stop the queue |
| Re-run same batch | Paths already stubbed → `SKIP (not local)`; interrupted path resumes via `.cloud-vfs/offload-progress/` |
| Interrupted single-file upload | If blob **size** matches local file, upload is skipped; otherwise full re-upload. Size match alone does not prove content integrity — use `offload --verify-only` before trusting partial uploads. |
| Upload retries | `CLOUD_VFS_UPLOAD_RETRIES` (default 3) with exponential backoff on CLI failures |

```bash
cloud-vfs offload path1 path2 path3   # partial failure → exit 1 + summary
cloud-vfs offload path1 path2 path3   # re-run: skips done, continues rest
```

Binary checkpoints (`.pth`, `.npy`, etc.) require **≥ 0.5.2** (bounded stub probe; never `read_text()` on large files).

## Commands

```bash
# After fetch — default verifies sha256
cloud-vfs ensure data/run
cloud-vfs ensure data/run --no-verify

# Audit
cloud-vfs reconcile --drift
cloud-vfs reconcile --from-blob
cloud-vfs reconcile --repair-stubs
cloud-vfs reconcile --orphan-blobs

# Before agents/humans delete local data
cloud-vfs guard data/large.npy
```

## Drift types

| Type | Meaning |
|------|---------|
| `orphan-local` | Large local file, not in inventory |
| `ghost-index` | Inventory cloud-only, blob missing |
| `hash-mismatch` | Local sha256 ≠ inventory |
| `orphan-blob` | In cloud-vfs bucket, not in inventory |
| `stale-stub` | Cloud-only dir, no `.cloudstub` |
| `stale-inline-ref` | Inventory `local` but path is still a ref |
| `ref-inventory-mismatch` | Stub blob path ≠ inventory blob |
| `local-index-mismatch` | Inventory cloud-only but real local bytes |

## Agent rules (summary)

1. **Never** delete local files because “prod blob has it.”  
2. **Always** `guard` + user confirm before delete.  
3. **Only** cloud-vfs `offload` + inventory `cloud-only` counts as “backed up.”  
4. Use `resolve` / `guard` JSON fields: `managed_by_cloud_vfs`, `safe_to_delete_local`.

See [AGENTS.md](AGENTS.md).
