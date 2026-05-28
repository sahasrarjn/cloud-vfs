# cloud-vfs

Manual **cloud blob virtual filesystem** for ML and research repos. Keep your laptop small: large artifacts live in Azure Blob or S3, local disk keeps tiny `.cloudstub` pointers, and a **machine-maintained per-file inventory** tracks explicit cloud paths.

Works with **Cursor / Claude agents** or plain shell + [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) / [AWS CLI](https://aws.amazon.com/cli/).

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Why cloud-vfs (not DVC / Git LFS)

| cloud-vfs | DVC / Git LFS |
|-----------|----------------|
| Lean repo; data stays out of git | Data lineage tied to git commits |
| Agent-safe dry-run offload | Heavier toolchain |
| Dual archive (local + cloud staging) | Single-remote patterns |
| **Large `data/` only** inventory | Tracks everything you add |

Best for: **laptop hygiene + lazy fetch + explicit offload** on research repos with big embeddings, datasets, and checkpoints.

## Features

- **Per-file inventory** — `.cloud-vfs/index/<shard>.json` with `local`, `blob`, `sha256`, `etag`, `state`
- **Lazy fetch** — `cloud-vfs ensure <path>` (single file or whole tree)
- **Manual offload** — hash before delete; `--dry-run` first
- **Drift audit** — `cloud-vfs reconcile` compares disk ↔ inventory ↔ blob
- **Large-data scope** — default ≥ 50 MB under `data/`; prefix overrides for weights, etc.
- **Multi-cloud** — Azure Blob and AWS S3
- **Cursor skill** — `cloud-vfs init --skill`

No auto-tracking, no cron, no background jobs.

## Install

```bash
pip install git+https://github.com/sahasrarjn/cloud-vfs.git
```

Or:

```bash
curl -fsSL https://raw.githubusercontent.com/sahasrarjn/cloud-vfs/main/install.sh | bash
```

Requires **Python 3.9+**, `az` and/or `aws` CLI, and cloud credentials.

## Quick start

```bash
cd your-project
cloud-vfs init --skill
cloud-vfs-setup                    # optional interactive wizard
# edit .cloud-vfs/manifest.json
# edit .cloud-vfs/inventory-policy.json  (optional; defaults are sensible)

cloud-vfs register data/big/embeddings.npy   # index local large files
cloud-vfs status --drift
cloud-vfs offload --dry-run
cloud-vfs offload data/old_run               # only after you choose
cloud-vfs ensure data/old_run                # fetch when needed
cloud-vfs prune                              # drop sub-threshold inventory rows
```

## Two layers

| Layer | File | Who edits |
|-------|------|-----------|
| **Policy** | `.cloud-vfs/manifest.json` | Human / agent |
| **Policy** | `.cloud-vfs/inventory-policy.json` | Human / agent |
| **Inventory** | `.cloud-vfs/index/<root>.json` | **Tools only** |

Inventory rows are created by **`offload`**, **`register`**, and **`reconcile --fix-index`** — never hand-edited.

## Commands

| Command | Description |
|---------|-------------|
| `cloud-vfs init [--skill]` | Scaffold `.cloud-vfs/` in your project |
| `cloud-vfs register <paths>` | Index local files (+ sha256); respects min size |
| `cloud-vfs ensure <path>` | Fetch from cloud if stub / cloud-only |
| `cloud-vfs resolve <path>` | JSON: blob URL + inventory row (for agents) |
| `cloud-vfs status [--drift]` | Manifest paths + inventory counts |
| `cloud-vfs reconcile [--from-blob] [--fix-index]` | Drift audit; rebuild index from blob |
| `cloud-vfs prune` | Remove inventory rows below min size |
| `cloud-vfs offload --dry-run` | Preview offload candidates |
| `cloud-vfs offload <paths>` | Upload + index (large files) + stub |
| `cloud-vfs materialize-stubs` | Upgrade legacy stubs to v2 |

## Project layout

```
your-project/
  .cloud-vfs/
    config.env              # account names (commit)
    secrets.env             # keys (gitignored)
    manifest.json           # folder-level policy (commit)
    inventory-policy.json   # min size, include/exclude (commit)
    index/                  # per-file inventory shards
      data/
        ADME.json             # commit benchmark shards
        generated/            # often gitignored — regenerate from blob
  data/
    big/.cloudstub            # v2 directory pointer when offloaded
  .cursor/skills/cloud-vfs/   # optional
```

## Tracking scope (defaults)

| Rule | Default |
|------|---------|
| `include_prefixes` | `data/` only |
| `min_size_bytes` | 50 MB (52_428_800) |
| `prefix_min_size_bytes` | e.g. `data/model_weights/` → 5 MB |
| `exclude_prefixes` | `code/`, `research/`, … |
| Offloaded split trees | stub `blob_prefix` for small members; index only large files |

See [docs/INVENTORY.md](docs/INVENTORY.md).

## One or two archives (Azure and/or AWS)

Set `LOCAL_PROVIDER=azure` or `aws` in `.cloud-vfs/config.env`.

**Azure:** `AZ_LOCAL_*`, `AZ_REMOTE_*` + keys in `secrets.env`

**AWS:** `AWS_LOCAL_BUCKET`, `AWS_LOCAL_REGION` (uses `aws` CLI credentials)

Manifest archive keys: `local_archive`, `remote_staging` (`runpod_staging` is a legacy alias).

## Agents

```bash
cloud-vfs ensure path/to/file          # before reading cloud-only paths
cloud-vfs register path/to/new.npy     # after creating outputs ≥ min size
cloud-vfs reconcile                    # after compute runs
cloud-vfs offload --dry-run path       # always dry-run + confirm with user
cloud-vfs offload path
```

Never hand-edit `.cloud-vfs/index/*.json`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `CLOUD_VFS_PROJECT_ROOT` | Force project root |
| `CLOUD_VFS_CONFIG` | Path to `config.env` |
| `CLOUD_VFS_SECRETS` | Path to `secrets.env` |
| `CLOUD_VFS_MANIFEST` | Path to `manifest.json` |

## Documentation

- [docs/CLOUD_VFS.md](docs/CLOUD_VFS.md) — workflow, stubs, drift
- [docs/INVENTORY.md](docs/INVENTORY.md) — policy, shards, git hygiene
- [docs/AGENTS.md](docs/AGENTS.md) — rules for coding agents

## License

MIT — see [LICENSE](LICENSE).
