# Agent rules for cloud-vfs

Use when an coding agent reads, creates, or offloads large project data.

## Before reading cloud-only paths

If file content is JSON with `"cvfs": 1`, the path is an **inline ref** — fetch before use:

```bash
cloud-vfs ensure <path>
```

For directories with `.cloudstub` only, run `ensure` on the directory path.

Check first:

```bash
cloud-vfs resolve <path>
```

(`resolve` returns `is_ref`, `placement`: `inline` or `sidecar`.)

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

## After GPU / CPU / long local runs

```bash
cloud-vfs reconcile
```

Fix ephemeral generated indexes if needed:

```bash
cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/
```

## Never

- Hand-edit `.cloud-vfs/index/*.json`
- Offload without dry-run preview
- Delete local data before upload verify succeeds
- Commit `secrets.env` or register entire `code/` trees

## Install skill in project

```bash
cloud-vfs init --skill
```

Skill path: `.cursor/skills/cloud-vfs/SKILL.md`
