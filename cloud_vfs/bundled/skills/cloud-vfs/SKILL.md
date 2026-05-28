---
name: cloud-vfs
description: >-
  Operate cloud-vfs: Azure Blob-backed paths with manual lazy fetch and dry-run
  offload. Use when configuring .cloud-vfs/, fetching cloud-only files, or
  offloading large project data.
---

# cloud-vfs

Install: `pip install git+https://github.com/sahasrarjn/cloud-vfs.git`

Keep large project files in Azure Blob. Local disk holds tiny `.cloudstub` pointers until you `cloud-vfs ensure` a path.

## First-time setup — ask the user

1. Azure region(s) and storage account name(s)
2. One account or two (local archive + optional remote staging)
3. Which project paths belong in the manifest

Then:

```bash
cloud-vfs init --skill
cloud-vfs-setup
# edit .cloud-vfs/manifest.json
```

## Config

| File | Commit? |
|------|---------|
| `.cloud-vfs/config.env` | Yes |
| `.cloud-vfs/secrets.env` | **Never** |
| `.cloud-vfs/manifest.json` | Yes |

## Commands

| Task | Command |
|------|---------|
| Fetch | `cloud-vfs ensure <path>` |
| Inspect | `cloud-vfs resolve <path>` |
| Inventory | `cloud-vfs status` |
| Preview offload | `cloud-vfs offload --dry-run` |
| Offload | `cloud-vfs offload <path>...` |

Archive values: `local_archive` or `remote_staging`. Provider: `azure` or `aws` (config or per-entry `"provider": "aws"`).

**AWS config:** `LOCAL_PROVIDER=aws`, `AWS_LOCAL_BUCKET`, `AWS_LOCAL_REGION` — uses `aws` CLI credentials.

## Agent rules

1. Before reading a cloud-only path: `cloud-vfs ensure <path>`
2. Before offloading: **always** `cloud-vfs offload --dry-run` and get user confirmation
3. Never offload without explicit approval after dry-run

## Manifest entry example

```json
{
  "id": "my-dataset",
  "local": "data/my_dataset",
  "blob_prefix": "data/my_dataset/",
  "archive": "local_archive",
  "status": "offloaded-local-removed"
}
```

Docs: https://github.com/sahasrarjn/cloud-vfs
