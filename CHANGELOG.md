# Changelog

## 0.5.0

### Adoption

- **`cloud-vfs doctor`** — checks Python, install, project scaffold, provider config, CLI tools, credentials; `--probe` and `--roundtrip` for bucket smoke tests
- **PyPI** — install with `pip install cloud-vfs`; GitHub Actions publish on release ([docs/PUBLISHING.md](docs/PUBLISHING.md))
- **`cloud-vfs try`** — scaffolds bundled sandbox demo (default `./cloud-vfs-try`)
- **Example project** — [examples/minimal-demo/](examples/minimal-demo/) and [docs/TRY.md](docs/TRY.md)

## 0.4.1

### Robustness

- **Safe `ensure`:** fetch to temp first; inline refs are removed only after a successful download
- **Safe `offload`:** sha256 captured before upload; stub written after verified upload (inline ref overwrites file; dir sidecar persisted via temp before tree removal)
- **Inventory commands:** `register`, `reconcile`, `prune`, and `status --drift` implemented in CLI
- **Atomic writes** for manifest and inventory shards
- **Clear errors** for cloud CLI failures, missing manifest, and paths outside project root
- **Empty directory upload** rejected with an explicit error

### Tests

- Regression tests for fetch failure ref preservation, offload hashing, register/prune/drift

## 0.4.0

### Hybrid inline stubs

- **Single files** offloaded to cloud now keep a tiny JSON ref **at the original path** (agent-readable without sidecar lookup)
- **Directory trees** still use `.cloudstub` sidecar (unchanged)
- Inline refs use `"cvfs": 1`, `"placement": "inline"`, schema version 2
- `resolve` reports `is_ref` and `placement`
- Legacy `*.cloudstub` file sidecars migrate to inline on `ensure` / `materialize-stubs`
- Scaffold gitignore drops `**/*.cloudstub` (dirs still ignore `**/.cloudstub`)

### Documentation

- Design spec: [2026-05-29-hybrid-inline-stubs-design.md](docs/superpowers/specs/2026-05-29-hybrid-inline-stubs-design.md)
- Updated [CLOUD_VFS.md](docs/CLOUD_VFS.md), Cursor skill, README

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

- Inventory commands (`register`, `reconcile`, `prune`, `status --drift`) — shipped in 0.4.1+

## 0.2.0

- Multi-cloud Azure + AWS support
- Folder-level stubs and manifest catalog
- Cursor skill via `init --skill`

## 0.1.0

- Initial Azure lazy fetch + dry-run offload
