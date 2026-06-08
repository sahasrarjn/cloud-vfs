# Per-file inventory

cloud-vfs maintains a **machine-written catalog** of large data files. Git tracks code; the manifest tracks folder policy; the inventory tracks **individual blob paths**.

## Policy file

`.cloud-vfs/inventory-policy.json`:

```json
{
  "version": 1,
  "index_dir": ".cloud-vfs/index",
  "min_size_bytes": 52428800,
  "prefix_min_size_bytes": {
    "data/model_weights/": 5242880,
    "data/embeddings/": 10485760
  },
  "offload_always_prefixes": ["data/ADME/seq_emb_dict_processed_ADME_full_"],
  "include_prefixes": ["data/"],
  "exclude_prefixes": ["code/", "experiments/", "scratch/", ".cursor/", "infra/"],
  "committed_prefixes": ["data/benchmarks/"],
  "ephemeral_prefixes": ["data/generated/"]
}
```

| Field | Purpose |
|-------|---------|
| `min_size_bytes` | Default minimum file size to index (50 MB) |
| `prefix_min_size_bytes` | Longest-prefix wins (e.g. 5 MB for weights) |
| `offload_always_prefixes` | Index/offload these trees regardless of size (bypasses `min_size_bytes`; matches literal path prefixes; `exclude_prefixes` still wins) |
| `include_prefixes` | Only index under these roots |
| `exclude_prefixes` | Never index (code, scratch experiments, …) |
| `committed_prefixes` | Shards to commit for reproducibility |
| `ephemeral_prefixes` | Shards to gitignore; rebuild from blob |

## Who writes inventory rows

| Command | When |
|---------|------|
| `register` | Large local file created; records sha256 |
| `offload` | After upload; hashes **before** delete; indexes large files only |
| `reconcile --fix-index` | Rebuild from blob listing (ephemeral trees) |
| `prune` | Removes rows below threshold (never adds) |

**Never hand-edit** index JSON.

## Shard layout

One JSON file per offload root:

```
.cloud-vfs/index/data/generated/my_run.json   → all indexed files under my_run/
.cloud-vfs/index/data/embeddings.json          → sibling files under data/embeddings/
```

Lookup scans shards by exact `local` path key.

## Offloaded trees with small files

Example: a split folder with 200 CSVs under 50 MB each.

- **Upload:** entire tree goes to blob on offload
- **Inventory:** only files ≥ `min_size_bytes` get rows (often just `train.csv`)
- **Fetch whole tree:** stub `blob_prefix` + `ensure` batch download
- **Fetch one large file:** inventory row + single-blob download

This avoids thousands of inventory rows for small split files.

## Git hygiene

```gitignore
.cloud-vfs/secrets.env
**/.cloudstub
.cloud-vfs/index/data/generated/
.cloud-vfs/index/code.json
```

Commit benchmark shards (e.g. locked embedding hashes):

```
.cloud-vfs/index/data/benchmarks/embeddings.json
```

Regenerate ephemeral indexes after clone:

```bash
cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/
```

## Maintenance

```bash
# After changing policy or accidental wide register
cloud-vfs prune

# Check drift
cloud-vfs status --drift
cloud-vfs reconcile --json
```

## Design rationale

**Track large `data/` only.** Code belongs in git. Small configs and READMEs use manifest `blob` fields without per-file inventory. This keeps indexes small (tens of rows, not thousands) while preserving explicit cloud paths for embeddings, checkpoints, and multi-GB CSVs.

## Teams

For shared repos — what to commit vs gitignore, the clone → configure → fetch checklist, committed vs ephemeral shards, and who offloads vs fetches on which machine — see [TEAM.md](TEAM.md).
