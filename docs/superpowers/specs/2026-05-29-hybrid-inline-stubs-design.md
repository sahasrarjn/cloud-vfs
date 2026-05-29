# Hybrid inline stubs — design spec

**Status:** Approved  
**Date:** 2026-05-29  
**Scope:** cloud-vfs 0.4.0

## Summary

Replace sidecar `.cloudstub` files for **single offloaded files** with a tiny **same-path ref** at the original path. Keep **directory sidecar stubs** unchanged. Central per-file inventory (`.cloud-vfs/index/`) remains the source of truth; inline refs are a denormalized agent-facing cache.

## Problem

After offload today, a single file like `data/embeddings.npy` is deleted and replaced by `data/embeddings.npy.cloudstub`. Agents must know to call `cloud-vfs ensure` or discover the sidecar — reading the original path fails with "file not found."

## Goal

When an agent (or human) opens an offloaded file path, they immediately see cloud location metadata at that path and can fetch on demand.

## Non-goals (v0.4)

- Transparent auto-fetch (FUSE, Python import hooks, numpy shims)
- Replacing directories with same-path refs (Unix cannot replace a dir with a file)
- Removing central inventory or manifest layers
- Changing blob upload/download backends

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Policy (human/agent, git)                                  │
│  .cloud-vfs/manifest.json, inventory-policy.json          │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│  Inventory — source of truth (tools only)                   │
│  .cloud-vfs/index/<shard>.json                              │
│  sha256, etag, state, blob path                             │
└─────────────────────────────────────────────────────────────┘
                              │ materialize / sync
                              ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│  Single file (inline)    │  │  Directory tree (sidecar)    │
│  data/foo.npy            │  │  data/run/.cloudstub         │
│  = JSON ref at same path │  │  = dir stub (unchanged)      │
└──────────────────────────┘  └──────────────────────────────┘
```

## Ref formats

### Inline file ref (new default for files)

Written at `data/embeddings.npy` after offload:

```json
{
  "cvfs": 1,
  "type": "cloud-blob-ref",
  "version": 2,
  "placement": "inline",
  "local": "data/embeddings.npy",
  "blob": "data/embeddings.npy",
  "archive": "local_archive",
  "fetch_cmd": "cloud-vfs ensure data/embeddings.npy"
}
```

Required keys: `cvfs`, `type`, `version`, `placement`, `local`, `fetch_cmd`, plus `blob` or `blob_prefix`.

Optional keys (may be omitted to keep ref small; inventory has full record): `archive`, `manifest_id`, `provider`.

### Directory sidecar ref (unchanged)

Written at `data/generated/my_run/.cloudstub`:

```json
{
  "cvfs": 1,
  "type": "cloud-dir-ref",
  "version": 2,
  "placement": "sidecar",
  "local": "data/generated/my_run",
  "archive": "local_archive",
  "shard_root": "data/generated/my_run",
  "index": ".cloud-vfs/index/data/generated/my_run.json",
  "blob_prefix": "data/generated/my_run/",
  "manifest_id": "my-run",
  "fetch_cmd": "cloud-vfs ensure data/generated/my_run"
}
```

### Detection

A path is a **ref** (not real local data) when:

1. Path exists and is a regular file, and
2. First byte is `{`, and
3. Parsed JSON has `"cvfs": 1` and `"type"` in `("cloud-blob-ref", "cloud-dir-ref")`

No binary magic prefix in v0.4 — JSON-only keeps refs human/agent-readable.

### Legacy sidecar files

Continue to read `*.cloudstub` sidecars for single files (v1 migration). `materialize-stubs` and `ensure` upgrade legacy sidecars to inline refs on next touch.

## Behavior changes

### `offload <path>`

| Path kind | Before | After |
|-----------|--------|-------|
| File | delete file → write `path.cloudstub` | delete file → write ref **at path** |
| Directory | delete tree → write `path/.cloudstub` | unchanged |

Upload, hash-before-delete, and inventory indexing unchanged.

### `ensure <path>`

1. If path is inline ref → fetch blob → **delete ref, write real bytes at path**
2. If path is dir with sidecar → fetch tree → remove sidecar, restore files
3. If path is real local → no-op
4. Legacy `path.cloudstub` for files → fetch → write inline ref removed, real file at path; delete legacy sidecar

### `resolve <path>`

Return `placement: inline|sidecar`, `is_ref: true|false`, plus existing blob URLs.

### `is_real_local(path)`

| Case | Result |
|------|--------|
| File exists, not a ref | `true` |
| File exists, is inline ref | `false` |
| File missing, legacy sidecar exists | `false` |
| Dir with only `.cloudstub`, no other files | `false` |
| Dir with real files (ignoring `.cloudstub`) | `true` |

### `status --drift`

Add drift type `stale-inline-ref`: inventory says `local` but path is still an inline ref (or vice versa).

Existing `stale-stub` covers sidecar dirs.

## Module changes

| Module | Change |
|--------|--------|
| `storage/stub.py` | `is_ref()`, `write_inline_ref()`, `read_stub()` checks same path first, legacy fallback |
| `storage/paths.py` | `is_real_local()` uses ref detection; `stub_file_for()` only for dirs + legacy |
| `cli.py` | `offload`, `ensure`, `resolve`, `materialize-stubs` use hybrid placement |
| `scaffold.py` | Gitignore: optional note that inline refs under `data/` may be committed (policy choice) |
| `bundled/skills/cloud-vfs/SKILL.md` | Agent rule: if file content is cvfs ref, run `ensure` before binary reads |
| `docs/CLOUD_VFS.md` | Document hybrid model, inline schema, migration |

## Agent skill update

Add to agent rules:

1. **Before reading a path:** if `read` shows JSON with `"cvfs": 1`, run `cloud-vfs ensure <path>` first.
2. **Do not** parse inline refs as data (numpy, pandas, etc.).
3. Directory paths: if `.cloudstub` present inside dir, run `ensure` on the directory path.

## Git hygiene

| Artifact | Recommendation |
|----------|----------------|
| Inline refs under `committed_prefixes` | Commit (tiny, documents cloud location for reproducibility) |
| Inline refs under `ephemeral_prefixes` | Gitignore pattern `data/generated/**/*.npy` etc. is impractical — rely on prefix gitignore of generated trees or commit refs only |
| Sidecar `.cloudstub` | Keep gitignored |

**Decision:** Inline refs inherit the same commit/gitignore policy as their shard in `inventory-policy.json`. Refs are small (~300 B); committing refs for benchmark paths is desirable.

Update scaffold gitignore: remove blanket ignore of `**/*.cloudstub` only for file sidecars if migrating; keep `**/.cloudstub` for directories.

## Migration

1. **`cloud-vfs materialize-stubs`** — for manifest entries already offloaded:
   - Files with legacy `*.cloudstub` → move to inline ref at path, delete sidecar
   - Dirs → ensure sidecar exists (no change)
2. **One-time detection** in `read_stub`: check inline path, then legacy sidecar locations (current candidates list).
3. **Version bump** package to 0.4.0 in `pyproject.toml` / `__init__.py`.

## Error handling

| Situation | Behavior |
|-----------|----------|
| Inline ref corrupt JSON | `ensure` errors; `reconcile` flags `stale-inline-ref` |
| Real file overwritten by ref write | Prevented: offload only after hash + upload verify |
| User hand-edits ref | `reconcile` compares inventory; inventory wins on `--fix-stubs` (new flag, optional v0.4) |
| Partial fetch failure | Ref remains; temp file discarded |

## Testing

| Test | Method |
|------|--------|
| File roundtrip | offload file → assert inline ref at path → ensure → assert real bytes + sha256 match |
| Dir roundtrip | existing behavior unchanged |
| Legacy sidecar read | place `foo.npy.cloudstub` → ensure migrates to inline |
| `is_real_local` | matrix: real file, inline ref, missing, dir+sidecar |
| Agent detection | ref JSON parseable, `cvfs` key present |

Add `tests/test_stub_hybrid.py` (first unit tests in repo).

## Rollout

1. Implement stub/path changes
2. Update CLI + docs + skill
3. Bump 0.4.0, CHANGELOG entry
4. Run AWS roundtrip script extended for inline file case

## Open questions (defaults chosen)

| Question | Default |
|----------|---------|
| Include sha256 in inline ref? | No — inventory only (keep ref tiny) |
| Auto-migrate on any `read_stub`? | Migrate legacy sidecar → inline on `ensure` / `materialize-stubs` only |
| `reconcile --fix-stubs` in v0.4? | Defer unless trivial; inventory + materialize-stubs sufficient |

## Success criteria

- Agent opening `data/large.npy` sees cvfs JSON without calling `resolve`
- `cloud-vfs ensure data/large.npy` restores real file in place
- Directory offloads behave identically to 0.3.x
- Legacy file sidecars migrate cleanly
