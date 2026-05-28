# cloud-vfs

Manual **Azure Blob virtual filesystem** for ML repos. Keep your laptop lean: large artifacts live in cloud storage, local disk keeps tiny `.cloudstub` JSON pointers, and you stay in full control with dry-run offload.

Built for **Cursor / Claude agents** and humans who use bash + Azure CLI.

[![PyPI version](https://img.shields.io/pypi/v/cloud-vfs)](https://pypi.org/project/cloud-vfs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Features

- **Lazy fetch** — `cloud-vfs ensure <path>` downloads from Azure when a stub or missing file is accessed
- **Manual offload** — `cloud-vfs offload --dry-run` then explicit `offload <paths>`
- **Dual blob accounts** — local archive (near you) + cloud staging (near GPU); not synced
- **Manifest catalog** — `.cloud-vfs/manifest.json` maps paths ↔ blobs ↔ status
- **Cursor skill** — `cloud-vfs init --skill` installs agent guidance

No auto-tracking, no cron, no background jobs.

## Install

### pip (recommended)

```bash
pip install git+https://github.com/sahasrarjn/cloud-vfs.git
# or after PyPI publish:
# pip install cloud-vfs
```

### install script

```bash
curl -fsSL https://raw.githubusercontent.com/sahasrarjn/cloud-vfs/main/install.sh | bash
```

### from source

```bash
git clone https://github.com/sahasrarjn/cloud-vfs.git
cd cloud-vfs
pip install -e .
```

Requires **Python 3.10+** and **[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)** (`az login`).

## Quick start

```bash
cd your-ml-project

# 1. Scaffold config + manifest (+ optional Cursor skill)
cloud-vfs init --skill

# 2. Interactive Azure setup (regions, account names, optional provision)
cloud-vfs-setup
# or: bash $(python3 -c "import cloud_vfs.project as p; print(p.package_path('scripts/setup-blob-storage.sh'))")

# 3. Edit .cloud-vfs/manifest.json — register your data paths

# 4. Inspect and dry-run offload
cloud-vfs status
cloud-vfs offload --dry-run

# 5. Offload only what you choose
cloud-vfs offload data/generated/my_old_run

# 6. Fetch back when needed
cloud-vfs ensure data/generated/my_old_run
```

## Commands

| Command | Description |
|---------|-------------|
| `cloud-vfs init [--skill]` | Create `.cloud-vfs/` in your project |
| `cloud-vfs ensure <path>` | Fetch from blob if cloud-only |
| `cloud-vfs resolve <path>` | JSON fetch instructions (for agents) |
| `cloud-vfs status` | Local vs stub + sizes |
| `cloud-vfs offload --dry-run` | Preview offload candidates |
| `cloud-vfs offload <paths>` | Upload + stub + remove local |
| `cloud-vfs materialize-stubs` | Write stubs for existing offloaded entries |

## Project layout

```
your-project/
  .cloud-vfs/
    config.env          # Azure account names (commit)
    secrets.env         # Storage keys (gitignored)
    manifest.json       # Path catalog (commit)
  data/
    my_run/.cloudstub   # Tiny pointer when offloaded
  .cursor/skills/azure-blob-storage/   # optional, from init --skill
```

Legacy compat: also reads `runpod/config.env`, `runpod/secrets.env`, and `infra/blob-manifest.json`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `CLOUD_VFS_PROJECT_ROOT` | Force project root |
| `CLOUD_VFS_CONFIG` | Path to config.env |
| `CLOUD_VFS_SECRETS` | Path to secrets.env |
| `CLOUD_VFS_MANIFEST` | Path to manifest.json |

## Agent integration

Before reading cloud-only paths:

```bash
cloud-vfs ensure data/my_dataset
```

Before offloading — always dry-run and confirm:

```bash
cloud-vfs offload --dry-run data/my_dataset
cloud-vfs offload data/my_dataset
```

Install the bundled skill with `cloud-vfs init --skill` or copy from `cloud_vfs/bundled/skills/azure-blob-storage/`.

## Why two blob accounts?

| | Local archive | Cloud staging |
|---|---------------|---------------|
| Region | Near your machine | Near GPU / cloud VM |
| Use | Long-term offload, manifest catalog | Active experiment sync |
| Synced? | **No** | **No** |

Use the same account for both by duplicating values in `config.env` if you prefer simplicity.

## Documentation

- [docs/CLOUD_VFS.md](docs/CLOUD_VFS.md) — workflow reference
- [skills/azure-blob-storage/README.md](cloud_vfs/bundled/skills/azure-blob-storage/README.md) — adoption guide

## License

MIT — see [LICENSE](LICENSE).

## Author

[Sahasra](https://github.com/sahasrarjn)
