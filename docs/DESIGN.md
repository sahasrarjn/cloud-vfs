# Design principles

cloud-vfs is a **general-purpose** tool: path-keyed cloud storage with explicit materialize/offload, not a workflow tied to one product, host type, or consumer repo.

**Agents and contributors working on this repository must keep it generic.**

## Source and target (core model)

| Term | Meaning | Examples |
|------|---------|----------|
| **Source** | Where bytes are stored in cloud (configured archive / backend) | `local_archive`, `remote_staging`, `--source` |
| **Target** | Where materialized files land on disk | project root (default), `--target-root <DIR>` |

Do not name APIs or docs after a specific machine, host role, or vendor product. Use **source**, **target**, **primary**, **secondary**, **staging**, **archive**.

## CLI and docs

- Prefer flags: `--source`, `--target`, `--target-root`, `--source-archive`.
- Keep `--archive` only as a **hidden backward-compatible alias** where needed.
- Avoid subcommands named for one deployment (`ensure-remote`, `fetch-remote`, etc.). Extend `ensure` / `ingest` with flags instead.
- Help text and markdown should describe behavior, not a single customer story.
- Examples may use `/workspace` or `experiments/runs/…` as **illustrative paths**, not as required layout.

## Manifest and config

- Archive keys stay neutral: `local_archive`, `remote_staging`.
- Optional `blob_role` aliases in manifests: `primary`, `secondary`, `staging`, `archive`.
- Older manifest/env spellings may still parse in code for existing installs; do not add new public docs or CLI help for them.

## Consumer-specific layout

Downstream repos may place config under custom paths (e.g. `infra/blob-manifest.json`, `infra/remote/config.env`). cloud-vfs may **discover** optional legacy paths for compatibility, but must not require them in core docs or tests.

## What belongs outside cloud-vfs

- SSH/azcopy wrappers for a specific training platform
- Checklists for one team's incident runbooks
- Pin bumps in consumer repos

Those live in consumer documentation; cloud-vfs exposes composable commands (`ensure`, `ingest`, `preflight`, `resolve`).

## Adding features

Before merging, ask:

1. Can this be expressed with **source** + **target** (or existing archive keys)?
2. Would the API make sense for a non-ML repo with large assets?
3. Are docs and `--help` free of single-use-case branding?

See also: [SOURCE_TARGET.md](SOURCE_TARGET.md), [AGENTS.md](AGENTS.md).
