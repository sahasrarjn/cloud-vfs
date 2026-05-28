---
name: azure-blob-storage
description: >-
  Set up and operate cloud-vfs: dual Azure Blob storage for ML repos with manual
  lazy fetch and dry-run offload. Use when onboarding blob storage, configuring
  .cloud-vfs/, or explaining fetch/offload workflow to adopters.
---

# Azure Blob storage (cloud-vfs)

Install: `pip install git+https://github.com/sahasrarjn/cloud-vfs.git`

**Goal:** Keep git + laptop small. Large artifacts live in Azure Blob. Manual control only — dry-run, then explicit offload.

## Before first use — ask the user

1. **Local archive** — region, resource group, storage account, container (near their machine)
2. **Cloud staging** — region, resource group, storage account, container (near GPU/cloud)
3. **Single vs dual blob** — dual recommended; same account for both is OK

Then:

```bash
cloud-vfs init --skill
cloud-vfs-setup
# edit .cloud-vfs/manifest.json
```

## Config (never commit secrets)

| File | Committed? |
|------|------------|
| `.cloud-vfs/config.env` | Yes |
| `.cloud-vfs/secrets.env` | **No** |
| `.cloud-vfs/manifest.json` | Yes |

Legacy compat: `runpod/config.env`, `infra/blob-manifest.json`.

## Commands

| Task | Command |
|------|---------|
| Fetch on demand | `cloud-vfs ensure <path>` |
| JSON instructions | `cloud-vfs resolve <path>` |
| Inventory | `cloud-vfs status` |
| Preview offload | `cloud-vfs offload --dry-run` |
| Offload chosen paths | `cloud-vfs offload <path>...` |

## Agent rules

1. Before reading cloud-only paths: `cloud-vfs ensure <path>`
2. Before offloading: **always** `cloud-vfs offload --dry-run` and confirm with user
3. Never offload without explicit user approval after dry-run

## Manifest entry

```json
{
  "id": "my-run",
  "local": "data/generated/my_run",
  "blob_prefix": "data/generated/my_run/",
  "archive": "local_archive",
  "status": "offloaded-local-removed",
  "uploaded": "2026-05-28"
}
```

Statuses: `required-local` | `synced` | `offload-candidate` | `offloaded-local-removed`

## Lifecycle

```
START  → cloud-only?  cloud-vfs ensure <path>
RUN    → use local paths
END    → cloud-vfs offload --dry-run → user confirms → cloud-vfs offload <paths>
```

Docs: https://github.com/sahasrarjn/cloud-vfs
