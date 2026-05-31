# Source and target

> **Contributors & agents:** cloud-vfs stays **generic** — no GPU/Mac/consumer-specific commands or docs. Read [DESIGN.md](DESIGN.md) before changing CLI or behavior.

cloud-vfs separates **where bytes live in cloud storage** (source) from **where materialized files land on disk** (target).

| Concept | CLI flag | Default |
|---------|----------|---------|
| **Source** | `--source` (alias `--archive`) | Entry/stub `archive` or `blob_role` |
| **Target** | project root | Paths resolve under `CLOUD_VFS_PROJECT_ROOT` |
| **Target** | `--target-root <DIR>` | Alternate filesystem root (no inventory on that host) |

Manifest archive keys remain `local_archive` and `remote_staging`. Optional `blob_role` aliases: `primary` / `archive` → `local_archive`, `staging` / `secondary` → `remote_staging`.

## Materialize at project target (default)

```bash
cloud-vfs ensure data/embeddings.npy
cloud-vfs ensure data/foo.npy --source remote_staging
cloud-vfs preflight data/train.csv
cloud-vfs ensure --check-only data/train.csv
```

## Materialize at a custom target root

Use on any host that has credentials and cvfs refs (or a manifest), without a full `.cloud-vfs/index/`:

```bash
cloud-vfs ensure --target-root /workspace \
  --source remote_staging \
  --paths-file run-paths.txt \
  data/embeddings.npy
```

Optional: `--ref-root` (read inline refs), `--manifest`, `--config-env`, `--secrets-env`.

## Ingest (local source → cloud target)

Upload a file that is not already at the project path:

```bash
cloud-vfs ingest \
  --source /tmp/model_best.pth \
  --target research/runs/model_best.pth \
  --source-archive local_archive
```

`--archive` is a hidden alias for `--source-archive`. Use `--dry-run` first.

## Resolve

```bash
cloud-vfs resolve data/foo.npy
```

JSON includes `source.archive`, `target.project_root`, `target.custom_root`, and `hints` with ready-to-run commands.
