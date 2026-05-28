# cloud-vfs

Manual **Azure Blob virtual filesystem** for any project with large files. Keep your machine lean: artifacts live in cloud storage, local disk keeps tiny `.cloudstub` JSON pointers, and you stay in full control with dry-run offload.

Works with **Cursor / Claude agents** or plain shell + [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli).

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Features

- **Lazy fetch** — `cloud-vfs ensure <path>` downloads when a stub or missing file is needed
- **Manual offload** — `cloud-vfs offload --dry-run` then explicit `offload <paths>`
- **Multi-cloud** — Azure Blob and AWS S3 (`LOCAL_PROVIDER` / `REMOTE_PROVIDER` or per-entry `provider`)
- **Manifest catalog** — `.cloud-vfs/manifest.json` maps paths ↔ blobs ↔ status
- **Cursor skill** — `cloud-vfs init --skill` installs agent guidance

No auto-tracking, no cron, no background jobs.

## Install

```bash
pip install git+https://github.com/sahasrarjn/cloud-vfs.git
```

Or:

```bash
curl -fsSL https://raw.githubusercontent.com/sahasrarjn/cloud-vfs/main/install.sh | bash
```

Requires **Python 3.9+** and `az login`.

## Quick start

```bash
cd your-project
cloud-vfs init --skill
cloud-vfs-setup
# edit .cloud-vfs/manifest.json
cloud-vfs status
cloud-vfs offload --dry-run
cloud-vfs offload data/large_folder    # only after you choose
cloud-vfs ensure data/large_folder     # fetch when needed
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
| `cloud-vfs materialize-stubs` | Write stubs for offloaded entries |

## Project layout

```
your-project/
  .cloud-vfs/
    config.env       # account names (commit)
    secrets.env      # keys (gitignored)
    manifest.json    # path catalog (commit)
  data/
    big/.cloudstub   # pointer when offloaded
  .cursor/skills/cloud-vfs/   # optional
```

## One or two archives (Azure and/or AWS)

Set `LOCAL_PROVIDER=azure` or `aws` in `.cloud-vfs/config.env`.

**Azure:** `AZ_LOCAL_*`, `AZ_REMOTE_*` + keys in `secrets.env`

**AWS:** `AWS_LOCAL_BUCKET`, `AWS_LOCAL_REGION` (uses `aws` CLI credentials — no keys in secrets.env)

Per manifest entry you can override with `"provider": "aws"` on the entry or archive block.

Use the same bucket/account for both archives if you only want one backend.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `CLOUD_VFS_PROJECT_ROOT` | Force project root |
| `CLOUD_VFS_CONFIG` | Path to config.env |
| `CLOUD_VFS_SECRETS` | Path to secrets.env |
| `CLOUD_VFS_MANIFEST` | Path to manifest.json |

## Agents

```bash
cloud-vfs ensure path/to/file
cloud-vfs offload --dry-run path/to/file   # confirm with user first
cloud-vfs offload path/to/file
```

## Documentation

- [docs/CLOUD_VFS.md](docs/CLOUD_VFS.md)

## License

MIT — see [LICENSE](LICENSE).
