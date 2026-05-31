# Agent rules for cloud-vfs

Use when a coding agent reads, creates, or offloads large project data.

## Design (read first)

cloud-vfs is **generic**: **source** (cloud archive) and **target** (filesystem path / root). Do not add GPU-, Mac-, or product-specific commands, flags, or documentation to this repo. See [DESIGN.md](DESIGN.md).

## Before reading cloud-only paths

If file content is JSON with `"cvfs": 1`, the path is an **inline ref** — fetch before use:

```bash
cloud-vfs ensure <path>
```

For directories with `.cloudstub` only, run `ensure` on the directory path.

Check first:

```bash
cloud-vfs resolve <path>
cloud-vfs preflight <paths...>    # or: cloud-vfs ensure --check-only <paths...>
```

(`resolve` returns `is_ref`, `placement`, `source`, `target`, and `hints`.)

## After creating large outputs

When a task writes files under `data/` ≥ policy min size (default 50 MB):

```bash
cloud-vfs register <path>
```

## Before offloading

**Always** dry-run and get user confirmation:

```bash
cloud-vfs offload --dry-run <paths>
# user confirms
cloud-vfs offload <paths>
```

## After long runs on any host

```bash
cloud-vfs reconcile
```

Fix ephemeral generated indexes if needed:

```bash
cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/
```

## Before deleting local files

**Mandatory** — prod/other buckets are invisible to cloud-vfs:

```bash
cloud-vfs guard <path>
```

- Exit **non-zero** if real local bytes exist (`REAL_LOCAL_BYTES` / `NOT_MANAGED_BY_CLOUD_VFS`).
- Only trust deletion of local bytes after a successful **cloud-vfs** offload and inventory `cloud-only`.
- `resolve` includes `managed_by_cloud_vfs` and `safe_to_delete_local` — do not delete when `managed_by_cloud_vfs` is false.

## Source / target (issue #11+)

| Task | Command |
|------|---------|
| Preflight stubs | `cloud-vfs preflight <paths>` or `ensure --check-only` |
| Materialize at project | `cloud-vfs ensure <path> [--source ARCHIVE]` |
| Materialize elsewhere | `cloud-vfs ensure --target-root <DIR> [--source ARCHIVE] <paths>` |
| Upload external file | `cloud-vfs ingest --source <file> --target <project-rel>` |

## Never

- Hand-edit `.cloud-vfs/index/*.json`
- Add use-case-specific subcommands or docs (keep behavior in flags; see [DESIGN.md](DESIGN.md))
- Offload without dry-run preview
- Delete local data before upload verify succeeds
- Delete because "file is in blob" without `guard` + cloud-vfs offload proof
- Assume uploads to prod/staging buckets outside `.cloud-vfs/config.env` are tracked
- Commit `secrets.env` or register entire `code/` trees

## Install skill in project

```bash
cloud-vfs init --skill
```

Skill path: `.cursor/skills/cloud-vfs/SKILL.md`
