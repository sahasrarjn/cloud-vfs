# Changelog

## 0.3.0

### Documentation

- Per-file inventory architecture (`register`, `reconcile`, `prune`)
- Large-data-only tracking policy (`inventory-policy.json`)
- Stub v2 with `blob_prefix` fallback for offloaded trees
- New docs: [INVENTORY.md](docs/INVENTORY.md), [AGENTS.md](docs/AGENTS.md)

### Scaffold

- `cloud-vfs init` writes `inventory-policy.json` and `.cloud-vfs/index/README.md`
- Gitignore patterns for ephemeral inventory shards

### CLI

- Inventory commands (`register`, `reconcile`, `prune`, `status --drift`) — see [ESP-CL reference](https://github.com/sahasrarjn/cloud-vfs/issues) if not yet in your pip build; reference implementation in consuming projects' `infra/vfs.py`.

## 0.2.0

- Multi-cloud Azure + AWS support
- Folder-level stubs and manifest catalog
- Cursor skill via `init --skill`

## 0.1.0

- Initial Azure lazy fetch + dry-run offload
