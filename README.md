# cloud-vfs

Manual **cloud blob virtual filesystem** for repos with large artifacts. Keep primary disks small: data lives in Azure Blob or S3, local paths keep tiny **inline refs** (same path) or `.cloudstub` directory pointers, and a **machine-maintained per-file inventory** tracks explicit cloud paths.

**Design:** generic **source** (cloud archive) and **target** (filesystem) — see [docs/DESIGN.md](docs/DESIGN.md).

Works with **Cursor / Claude agents** or plain shell + [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) / [AWS CLI](https://aws.amazon.com/cli/).

[![PyPI](https://img.shields.io/pypi/v/cloud-vfs.svg)](https://pypi.org/project/cloud-vfs/)
[![Python versions](https://img.shields.io/pypi/pyversions/cloud-vfs.svg)](https://pypi.org/project/cloud-vfs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Why cloud-vfs (not DVC / Git LFS)

| cloud-vfs | DVC / Git LFS |
|-----------|----------------|
| Lean repo; data stays out of git | Data lineage tied to git commits |
| Agent-safe dry-run offload | Heavier toolchain |
| Dual archive (primary + optional secondary backend) | Single-remote patterns |
| **Large `data/` only** inventory | Tracks everything you add |

Best for: **disk hygiene + lazy fetch + explicit offload** when projects store large files under `data/` (or policy-defined prefixes).

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
pip install cloud-vfs
```

Or from GitHub:

```bash
pip install git+https://github.com/sahasrarjn/cloud-vfs.git
curl -fsSL https://raw.githubusercontent.com/sahasrarjn/cloud-vfs/main/install.sh | bash
```

Requires **Python 3.9+**, `az` and/or `aws` CLI, cloud credentials, and **[azcopy v10](https://learn.microsoft.com/azure/storage/common/storage-use-azcopy-v10)** (recommended for multi-GB blob transfers).

## Try it in 5 minutes

```bash
pip install cloud-vfs
cloud-vfs try
cd cloud-vfs-try
cp .cloud-vfs/config.env.example .cloud-vfs/config.env   # set a TEST bucket
cloud-vfs doctor --roundtrip
./scripts/create-sample.sh
cloud-vfs offload --dry-run data/sample && cloud-vfs offload data/sample
cloud-vfs ensure data/sample
```

Full walkthrough: [docs/TRY.md](docs/TRY.md). Same demo lives in [examples/minimal-demo/](examples/minimal-demo/) if you cloned this repo.

## Quick start (your project)

Point at **any repo or folder** (must be writable; run from repo root or pass `--path`):

```bash
cd /path/to/your-ml-repo
cloud-vfs init --path . --skill
cp .cloud-vfs/config.env.example .cloud-vfs/config.env   # set bucket (see config.env.example)
cloud-vfs doctor --roundtrip

cloud-vfs scan                    # what large files can you offload?
cloud-vfs scan --add              # add them to manifest (no upload yet)
cloud-vfs offload --dry-run       # preview: sizes + cloud target
cloud-vfs offload data/your_run   # upload + stub (you choose paths)
cloud-vfs ensure data/your_run    # fetch back when needed
```

Optional: `cloud-vfs register <path>` indexes sha256 without upload; `cloud-vfs status --drift` audits inventory.

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
| `cloud-vfs guard <paths>` | Block unsafe local deletes (not managed by cloud-vfs) |
| `cloud-vfs doctor [--probe] [--roundtrip]` | Verify install, config, CLI, and cloud access |
| `cloud-vfs ensure [--source A] [--target-root DIR] [--check-only]` | Materialize cloud source → project or custom target |
| `cloud-vfs preflight <paths>` | Exit non-zero if stubs/refs need `ensure` |
| `cloud-vfs ingest --source FILE --target REL` | One-shot upload from arbitrary local file |
| `cloud-vfs try [--path DIR]` | Create sandbox demo project (default `./cloud-vfs-try`) |
| `cloud-vfs init [--path DIR] [--skill]` | Scaffold `.cloud-vfs/` in any folder |
| `cloud-vfs scan [--add] [--prefix P]` | Find large local files; optionally add to manifest |
| `cloud-vfs register <paths>` | Index local files (+ sha256); respects min size |
| `cloud-vfs ensure <path>` | Fetch from cloud if inline ref / stub / cloud-only |
| `cloud-vfs resolve <path>` | JSON: blob URL + inventory row (for agents) |
| `cloud-vfs status [--drift]` | Manifest paths + inventory counts |
| `cloud-vfs reconcile [--from-blob] [--fix-index]` | Drift audit; rebuild index from blob |
| `cloud-vfs prune` | Remove inventory rows below min size |
| `cloud-vfs cleanup-downloads [--dry-run]` | Remove stale download temps from interrupted fetches |
| `cloud-vfs offload --dry-run` | Preview offload candidates |
| `cloud-vfs offload <paths>` | Upload + index (large files) + inline ref or dir stub |
| `cloud-vfs materialize-stubs` | Write inline/sidecar refs; migrate legacy file sidecars |

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
    big.npy                   # inline JSON ref when single file offloaded
    big/.cloudstub            # directory pointer when tree offloaded
  .cursor/skills/cloud-vfs/   # optional
```

## Tracking scope (defaults)

| Rule | Default |
|------|---------|
| `include_prefixes` | `data/` only |
| `min_size_bytes` | 50 MB (52_428_800) |
| `prefix_min_size_bytes` | e.g. `data/model_weights/` → 5 MB |
| `exclude_prefixes` | `code/`, `experiments/`, `scratch/`, … |
| Offloaded split trees | dir stub `blob_prefix` for small members; index only large files |
| Offloaded single files | inline ref at original path (`"cvfs": 1`) |

See [docs/INVENTORY.md](docs/INVENTORY.md).

## One or two archives (Azure and/or AWS)

Set `LOCAL_PROVIDER=azure` or `aws` in `.cloud-vfs/config.env`.

**Azure:** `AZ_LOCAL_*`, `AZ_REMOTE_*` + keys in `secrets.env`

**AWS:** `AWS_LOCAL_BUCKET`, `AWS_LOCAL_REGION` (uses `aws` CLI credentials)

Manifest archive keys: `local_archive` (primary), `remote_staging` (secondary). See [docs/SOURCE_TARGET.md](docs/SOURCE_TARGET.md).

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
- [docs/ROBUSTNESS.md](docs/ROBUSTNESS.md) — verify, guard, orphan blobs, prod vs cloud-vfs bucket
- [docs/YOUR_REPO.md](docs/YOUR_REPO.md) — scan and offload in your existing repo
- [docs/TEAM.md](docs/TEAM.md) — multi-contributor playbook: commit vs blob vs inventory, clone workflow
- [docs/TRY.md](docs/TRY.md) — 5-minute try guide
- [examples/minimal-demo/](examples/minimal-demo/) — demo sources (also bundled in `cloud-vfs try`)
- [docs/PUBLISHING.md](docs/PUBLISHING.md) — PyPI release process

## License

MIT — see [LICENSE](LICENSE).
