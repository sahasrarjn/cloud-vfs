# GPU / hybrid workflow (issue #11)

Use when ML runs happen on ephemeral GPU hosts (RunPod, rescue `/workspace`) while catalog and long-term archive live on a Mac.

## Two blob backends

| Archive key | Role | Typical host |
|-------------|------|----------------|
| `local_archive` | Mac archive (long-term catalog) | Workstation `cloud-vfs ensure` |
| `remote_staging` | GPU staging (near compute) | Pod `cloud-vfs ensure-remote` |

Manifest entries may set `"blob_role": "staging"` (alias for `remote_staging`) or `"archive"` / `"mac_archive"` for `local_archive`.

```bash
cloud-vfs resolve data/gpu/train.csv
# archive, archive_role, context_hints.mac / .gpu
```

Override backend per command:

```bash
cloud-vfs ensure data/foo.npy --archive remote_staging
cloud-vfs offload research/out.pth --archive local_archive
```

## Remote materialize (no Mac inventory on pod)

On the GPU host: install `cloud-vfs`, set credentials (env or `--config-env` / `--secrets-env`), clone or sync git so **inline `cvfs` refs** exist.

```bash
export CLOUD_VFS_PROJECT_ROOT=/workspace/MyProject   # optional

cloud-vfs ensure-remote \
  --dest-root /workspace \
  --archive remote_staging \
  --paths-file /workspace/run-paths.txt \
  data/embeddings.npy data/gpu/train.csv
```

- Reads blob keys from inline refs and/or `--manifest` (e.g. `infra/blob-manifest.json`).
- Does **not** require `.cloud-vfs/index/` on the pod.
- Writes files under `--dest-root` preserving project-relative paths.

## Preflight before long GPU jobs

```bash
cloud-vfs preflight data/train.csv data/labels.csv
# or
cloud-vfs ensure --check-only data/train.csv
```

Exit code **1** lists paths still stubs/refs with suggested `ensure` commands.

## Training artifact round-trip (checkpoint after SCP)

When a large file lands on the Mac outside the project tree (e.g. SCP `model_best.pth`):

```bash
cloud-vfs ingest /tmp/model_best.pth \
  --as research/2026-05-29-v2-finetune/runs/model_best.pth \
  --archive local_archive
```

- Uploads from `--source` without a prior `register` at that path.
- Updates manifest + inventory (unless `--no-index`) and writes an inline ref at `--as` (unless `--no-stub`).
- Use `cloud-vfs ingest --dry-run` first.

## ESP consumer

After a cloud-vfs release with these commands, ESP bumps the pin via `./infra/bump-cloud-vfs.sh` and replaces ad-hoc `azcopy` bootstrap where `ensure-remote` + `blob_role` cover the path.
