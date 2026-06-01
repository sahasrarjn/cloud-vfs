# Use cloud-vfs in your repo

Works in **any directory** — your ML project, a monorepo subfolder, or a fresh clone. You only need write access and a cloud bucket for blobs.

## Setup (once)

```bash
cd /path/to/your-repo
cloud-vfs init --path . --skill
cp .cloud-vfs/config.env.example .cloud-vfs/config.env
# Edit bucket/account (use a TEST bucket first)
cloud-vfs doctor --roundtrip
```

`init --path .` works from the repo root. For a subfolder project, `cd` there first or pass that path.

## See what you can offload

```bash
cloud-vfs scan
```

Lists large **local** files under your policy (default: `data/`, ≥ 50 MB). Shows whether each path is already in `manifest.json`.

```bash
cloud-vfs scan --add
cloud-vfs offload --dry-run
```

- **`scan --add`** — adds untracked paths to the manifest as `offload-candidate` (still no upload).
- **`offload --dry-run`** — shows what would go to Azure/S3 and how big it is.

## Offload and fetch

```bash
cloud-vfs offload data/old_embeddings
cloud-vfs ensure data/old_embeddings
```

After offload, the path stays the same in code (`data/...`) but disk holds a tiny ref or `.cloudstub`. Agents and scripts should run `ensure` before reading binary data.

## How hard is it?

| Step | Effort |
|------|--------|
| Install CLI | One `pip install` |
| Config bucket | Edit one `config.env` file |
| Find candidates | `cloud-vfs scan` (automatic) |
| Preview | `offload --dry-run` (no changes) |
| Actually offload | You pick paths; hashes before delete |

You do **not** need to hand-edit `manifest.json` for every file if you use `scan --add`. You **do** need cloud CLI credentials (`aws` or `az`) and a bucket.

## Not in scope by default

- Files under `code/`, `.cursor/`, etc. (see `inventory-policy.json`)
- Files under 50 MB (unless you lower `min_size_bytes` or add `prefix_min_size_bytes`)

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `scan` shows nothing | Put data under `data/` or adjust `include_prefixes` / `min_size_bytes` |
| `offload --dry-run` empty | Run `scan --add` first |
| `doctor` fails | Fix `FAIL` lines before offload |
| Agent reads garbage | Path is a cloud ref — run `cloud-vfs ensure <path>` first |

See also [TRY.md](TRY.md) (sandbox) and [CLOUD_VFS.md](CLOUD_VFS.md) (full workflow).
