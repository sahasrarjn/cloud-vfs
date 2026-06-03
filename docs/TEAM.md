# Multi-contributor playbook

How a **team** shares one repo with cloud-vfs: what goes in git vs blob vs the
per-machine inventory, how a new contributor gets productive, and who owns
`offload` vs `ensure` on shared machines.

cloud-vfs is intentionally **not** experiment tracking or data lineage. If you need
run-to-artifact provenance, dataset versioning, or governance, use **MLflow / DVC**
alongside it (see [FAQ](#faq) below). This doc is **team workflow + git hygiene** only.

## Where each artifact lives

| Artifact | git | blob | inventory row | on-disk marker |
|----------|:---:|:----:|:-------------:|----------------|
| Source code, configs | ✅ | — | — | the file itself |
| `manifest.json`, `inventory-policy.json` (policy) | ✅ | — | — | the file itself |
| Large `data/` file, **offloaded** single file | — | ✅ | ✅ | **inline ref** at original path (`"cvfs": 1`) |
| Large `data/` **tree**, offloaded | — | ✅ | ✅ (large members) | `.cloudstub` in the dir |
| Committed benchmark shard (e.g. locked embedding hashes) | ✅ (`index/…`) | ✅ | ✅ | inventory shard JSON |
| Ephemeral generated tree (`data/generated/…`) | ❌ (gitignored) | ✅ | ✅ (rebuilt) | inventory shard JSON |
| `secrets.env` (cloud keys) | ❌ | — | — | gitignored |
| `.cloud-vfs/.tmp/`, `.cloud-vfs/locks/`, `.cloud-vfs/jobs/` | ❌ | — | — | runtime scratch, gitignored |

Rule of thumb: **git holds policy + small code; blob holds bytes; inventory is the
catalog that maps the two.** Whether an inventory shard is committed or regenerated is
the one real per-team decision — see [committed vs ephemeral](#committed-vs-ephemeral-inventory).

## New contributor checklist (clone → first fetch)

```bash
# 1. Clone — you get code, policy, committed inventory shards, and refs/stubs (no big bytes)
git clone <repo> && cd <repo>
pip install cloud-vfs

# 2. Configure this machine's cloud access (config.env is committed as an *.example)
cp .cloud-vfs/config.env.example .cloud-vfs/config.env   # set bucket/account
# Azure: put keys in .cloud-vfs/secrets.env (gitignored). AWS: uses `aws` CLI creds.

# 3. Verify install + config + bucket access before touching data
cloud-vfs doctor --roundtrip

# 4. Rebuild any ephemeral inventory shards that aren't committed (see policy)
cloud-vfs reconcile --from-blob --fix-index --prefix data/generated/

# 5. Fetch only the paths this task needs (egress costs money — fetch what you use)
cloud-vfs ensure data/benchmarks/embeddings.npy
cloud-vfs status --drift            # sanity-check disk ↔ inventory ↔ blob
```

After this, code paths (`data/...`) resolve normally: real bytes where you fetched,
tiny refs/stubs elsewhere. Scripts and agents must run `cloud-vfs ensure <path>` before
reading a path that may still be a cloud ref.

When you **produce** new large outputs:

```bash
cloud-vfs register data/new_outputs/big.npy   # index + sha256, no upload
cloud-vfs offload --dry-run data/new_outputs  # preview what would go to blob
cloud-vfs offload data/new_outputs            # upload + ref/stub (frees disk)
```

Always `offload --dry-run` first, and only delete local bytes through `offload` /
`local-release` — never by hand (see [ROBUSTNESS.md](ROBUSTNESS.md) `guard`).

## Committed vs ephemeral inventory

The inventory (`.cloud-vfs/index/<shard>.json`) is **per-file catalog**, written only by
tools (`offload`, `register`, `reconcile --fix-index`) — **never hand-edited**. Two
policies, set in `inventory-policy.json`:

```json
{
  "committed_prefixes": ["data/benchmarks/"],
  "ephemeral_prefixes": ["data/generated/"]
}
```

| Kind | Commit the shard? | Why | Rebuild after clone |
|------|:-----------------:|-----|---------------------|
| **Committed** (`data/benchmarks/`) | ✅ | Reproducibility — everyone resolves the *same* locked bytes/hashes | not needed (in git) |
| **Ephemeral** (`data/generated/`) | ❌ gitignore | Regenerated per run; committing it churns git and races between devs | `reconcile --from-blob --fix-index --prefix data/generated/` |

### Example `.gitignore`

```gitignore
# secrets + runtime scratch — never commit
.cloud-vfs/secrets.env
.cloud-vfs/.tmp
.cloud-vfs/locks
.cloud-vfs/jobs

# on-disk pointers for offloaded trees
**/.cloudstub

# ephemeral inventory — rebuild from blob, don't commit
.cloud-vfs/index/data/generated/
.cloud-vfs/index/code.json

# COMMIT these (do NOT ignore): benchmark shards for reproducibility
# .cloud-vfs/index/data/benchmarks/embeddings.json
```

`cloud-vfs init` writes a starter `.gitignore` with the secrets/scratch/ephemeral lines.
Add `committed_prefixes` shards to git explicitly.

## Concrete repo layout

```
team-ml-repo/
  .cloud-vfs/
    config.env              # bucket/account — committed (no secrets)
    secrets.env             # Azure keys — gitignored
    manifest.json           # folder policy — committed
    inventory-policy.json   # min size, include/exclude, committed/ephemeral — committed
    index/
      data/
        benchmarks/
          embeddings.json   # ✅ COMMIT — locked benchmark hashes
        generated/          # ❌ gitignored — rebuilt with reconcile --from-blob
  data/
    benchmarks/
      embeddings.npy        # inline ref after offload (same path, tiny JSON)
    generated/
      run_2026_05/.cloudstub  # dir pointer for an offloaded tree
  src/                      # normal git
```

## Who runs what on shared machines

| Machine | Typical role | Runs |
|---------|--------------|------|
| **Laptop / Mac** | author code, small fetches | `ensure` small paths, `offload --dry-run`, `register`. Avoid pulling multi-GB blobs (public-internet egress $ — see [ROBUSTNESS.md](ROBUSTNESS.md)). |
| **Training / GPU node** | produce + offload big artifacts | `ensure` large inputs (in-region, egress-free), `offload` outputs, `reconcile` after runs |
| **Either, same path concurrently** | — | `ensure` serializes per path (one downloader; others wait then skip — issue #22). For `offload`, agree **one writer per path** until shared offload locking exists. |

When in doubt: **fetch on compute, not on the laptop**; **offload from the node that
produced the bytes**; and run `cloud-vfs status --drift` / `reconcile` after pulls to
catch index drift early.

## FAQ

**Does cloud-vfs do experiment tracking / lineage / dataset versioning?**
No — and it won't. It is a manual, path-keyed blob layer: explicit `offload`/`ensure`,
per-file inventory, drift audit. For run → metric → artifact lineage, dataset versions,
or model registries, use **MLflow** or **DVC** alongside cloud-vfs.

**Two people offload the same path — what happens?**
Path-keyed, so you get **one** blob key (last write wins / overwrite). cloud-vfs does not
content-dedup. Coordinate one writer per path; use `offload --verify-only` to compare
local vs blob before trusting a partial upload.

**Someone renamed a `data/` path — old blob still there?**
Yes; it becomes an **orphan blob**. `cloud-vfs reconcile --orphan-blobs` lists unindexed
blobs in the cloud-vfs bucket (read-only; never auto-deleted).

**Clone shows tiny JSON where I expected a `.npy`.**
That's an inline ref for an offloaded file. Run `cloud-vfs ensure <path>` to materialize it.

See also: [INVENTORY.md](INVENTORY.md) (catalog + policy), [YOUR_REPO.md](YOUR_REPO.md)
(single-dev setup), [ROBUSTNESS.md](ROBUSTNESS.md) (safety, egress, concurrency),
[AGENTS.md](AGENTS.md) (agent rules).
