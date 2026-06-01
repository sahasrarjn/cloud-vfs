# Changelog

## 0.5.7

### Path-stable offload contract ([#17](https://github.com/sahasrarjn/cloud-vfs/issues/17))

- **`resolve`** — adds `remote_present`, `content_length`, and human-readable `status_label` for stub paths
- **`ensure --dry-run`** — previews fetch size, archive, transport tool, and blob URL (no download)
- **`status <path>`** — per-path `offloaded-remote-ok` vs `offloaded-missing-remote`
- **`local-release`** — delete local bytes when remote blob already verified (idempotent re-stub)
- **Offload idempotency** — already-stubbed paths report `offloaded-remote-ok` instead of `SKIP (not local)`
- Agent docs: **Offloaded ≠ missing. Path exists; run ensure before read.**

### azcopy transport for large Azure blobs ([#19](https://github.com/sahasrarjn/cloud-vfs/issues/19))

- **Multi-GB ensure/offload** uses **azcopy v10** with blob-scoped SAS (≥ 100 MB threshold)
- **`az storage blob` CLI** retained for small objects and metadata (`show`, `list`, `generate-sas`)
- **Fetch progress** — azcopy log streamed on TTY for large downloads
- **Partial cleanup** — failed azcopy downloads remove `.part` temp files
- **Fallback** — loud warning when azcopy missing; falls back to CLI
- README recommends azcopy for large blob transfers

## 0.5.6

### Large-file offload robustness ([#15](https://github.com/sahasrarjn/cloud-vfs/issues/15))

- **Batch jobs** — multi-path `offload` persists state under `.cloud-vfs/jobs/`; re-run skips stubbed paths and continues the queue; non-zero exit when any path failed or is still pending
- **Upload resume** — before re-uploading a single file, checks blob `contentLength` vs local size and skips upload when they match
- **Retries** — transient `az`/`aws` upload failures retry with backoff (`CLOUD_VFS_UPLOAD_RETRIES`, default 3)
- **Verify output** — prints verified byte size and sha256 after single-file upload
- Docs: [ROBUSTNESS.md](docs/ROBUSTNESS.md) batch/resume section

## 0.5.5

### Source / target materialize ([#11](https://github.com/sahasrarjn/cloud-vfs/issues/11))

- **`cloud-vfs ensure --target-root`** — materialize cloud **source** into an alternate filesystem **target** (no project inventory on that host)
- **`cloud-vfs ensure --source`** — choose blob backend (`local_archive` / `remote_staging`; `--archive` kept as hidden alias)
- **`cloud-vfs preflight`** and **`ensure --check-only`** — batch exit non-zero when stubs/refs still need fetch
- **`cloud-vfs ingest --source … --target …`** — one-shot upload from an arbitrary local file to cloud + manifest + inline ref
- **Dual archive clarity** — manifest `blob_role` (`primary` / `staging` aliases); `resolve` emits `source`, `target`, and `hints`
- Docs: [SOURCE_TARGET.md](docs/SOURCE_TARGET.md), [DESIGN.md](docs/DESIGN.md) (generic source/target; no use-case-specific commands)

## 0.5.4

### Bug fixes

- **#8** — `offload`/`ensure` no longer hang silently: subprocess idle timeout (default 600s, `CLOUD_VFS_SUBPROCESS_IDLE_TIMEOUT_SEC`) aborts stuck az/aws CLI calls; heartbeats every 30s during transfers

### Enhancements

- **#8** — Resumable offload via `.cloud-vfs/offload-progress/` checkpoints (auto-resume on re-run; `--no-resume` to restart)
- **#8** — `offload --verify-only` compares local paths to blob storage for safe recovery after interrupt
- **#8** — SIGTERM flushes partial offload progress (and manifest when updated) before exit
- **#8** — `ensure` emits progress heartbeats during fetch/sync

## 0.5.3

### Bug fixes

- **#1** — Binary stub detection never reads `.npy`/`.pkl` as UTF-8 (bounded binary probe only)

### Enhancements

- **#3** — `offload` prints upload start line and a heartbeat every 30s while azcopy/aws sync runs
- **#4** — `offload --keep-local` / `--delete-local` (default) make post-upload behavior explicit
- **#5** — Inventory indexing batches shard writes (one flush per shard, not per file)
- **#6** — `ensure <directory>` expands to cloud-only files under that prefix (inline refs + inventory)

## 0.5.2

### Bug fixes

- **#1** — `offload` / `is_real_local` no longer crash on binary files (`.npy`, `.pkl`, etc.): stub detection uses size + JSON prefix probe instead of full-file `read_text()`

### Robustness (Linux-style fsck + safety)

- **`ensure`** verifies downloaded bytes against inventory sha256 by default (`--no-verify` to skip)
- **`reconcile --repair-stubs`** regenerates missing refs from manifest/inventory
- **`reconcile --orphan-blobs`** lists unindexed blobs in the **cloud-vfs-configured** bucket only
- **`cloud-vfs guard`** blocks deleting real local files not managed by cloud-vfs (prod-bucket hallucination guard)
- **`resolve`** exposes `managed_by_cloud_vfs`, `safe_to_delete_local`, `delete_safety_reasons`
- Drift: `stale-inline-ref`, `ref-inventory-mismatch`, `local-index-mismatch`; `orphan-blob` replaces `unregistered-cloud`
- [docs/ROBUSTNESS.md](docs/ROBUSTNESS.md) — two-bucket safety model

## 0.5.1

### Your-repo workflow

- **`cloud-vfs scan`** — discover large local files under inventory policy; **`scan --add`** adds them to manifest
- **`offload --dry-run`** hints to run `scan` when manifest has no local candidates
- [docs/YOUR_REPO.md](docs/YOUR_REPO.md) — setup in any folder, scan → dry-run → offload

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
