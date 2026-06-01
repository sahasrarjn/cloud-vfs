# Draft — r/mlops

Subreddit: https://reddit.com/r/mlops  
Check [sub rules](https://www.reddit.com/r/mlops/about/rules) before posting.

---

## Title options (pick one)

1. **Artifact storage without DVC: lazy fetch from S3/Azure, explicit offload, drift audit**
2. **I built a manual blob VFS for large `data/` dirs — looking for MLOps feedback**
3. **Lightweight alternative to DVC/Git LFS when you only want to track large `data/` (not everything in git)**

---

## Post body

Hi r/mlops — I've been working on **[cloud-vfs](https://github.com/sahasrarjn/cloud-vfs)**, a small CLI for repos where large artifacts live under `data/` (or similar) and you want **disk hygiene + lazy fetch** without pulling the full DVC/Git LFS toolchain into every project.

**The problem I'm solving:** ML repos accumulate huge run outputs, checkpoints, and datasets. Git LFS bloats the repo model; DVC is powerful but heavier than I wanted for “keep my laptop disk small, materialize from S3/Azure when I need a path.” I wanted something **explicit, auditable, and agent-safe** — dry-run before delete, hash before offload, no background sync jobs.

**How it works (high level):**

- Large files under policy-defined prefixes (default: ≥50 MB under `data/`) get a **per-file inventory** (`.cloud-vfs/index/…`) with sha256, blob path, and state.
- **`cloud-vfs offload`** uploads and replaces local bytes with stubs/refs (you choose paths; `--dry-run` first).
- **`cloud-vfs ensure`** lazy-fetches back when a training job or notebook needs the file.
- **`cloud-vfs reconcile`** audits disk ↔ inventory ↔ blob for drift.
- Works with **AWS S3 and Azure Blob** via existing `aws` / `az` CLI + credentials.
- Optional **Cursor skill** (`cloud-vfs init --skill`) so agents know the workflow.

**Compared to DVC / Git LFS:**

| Aspect | cloud-vfs | DVC / Git LFS |
|--------|-----------|----------------|
| Data in git | No — lean repo | LFS tied to git; DVC lineage tied to commits |
| Scope | Large `data/` only (policy-driven) | Often broader tracking |
| Operations | Manual offload + ensure | Heavier toolchain / remotes |
| Safety | Dry-run offload, drift audit | Varies |

It's **beta** (MIT, `pip install cloud-vfs`). No cron, no auto-tracking — you decide what leaves disk.

**Try it in ~5 minutes** (uses a throwaway demo dir + your own test bucket):

```bash
pip install cloud-vfs
cloud-vfs try
cd cloud-vfs-try
cp .cloud-vfs/config.env.example .cloud-vfs/config.env   # set a TEST bucket
cloud-vfs doctor --roundtrip
./scripts/create-sample.sh
cloud-vfs offload --dry-run data/sample && cloud-vfs offload data/sample
cloud-vfs ensure data/sample
```

Docs: [TRY.md](https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/TRY.md) · [README](https://github.com/sahasrarjn/cloud-vfs)

**Asking for feedback from this sub specifically:**

- Does “manual offload + lazy ensure” fit how you handle artifacts today, or do you need stronger lineage/commit coupling?
- What would you need to trust this beside DVC in a team setting (RBAC, versioning, CI hooks)?
- If you run the try flow above, what broke or felt wrong?

Happy to answer questions in the thread. Not trying to replace DVC for experiment tracking — more interested in whether this **thin blob VFS layer** is useful for disk-bound dev machines and ephemeral training targets.

---

## First comment (optional — post immediately after submission)

Links for convenience:

- Repo: https://github.com/sahasrarjn/cloud-vfs  
- 5-min walkthrough: https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/TRY.md  
- Design principles (source/target model, no vendor-specific workflows): https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/DESIGN.md  

If `cloud-vfs try` fails on your setup, please paste `cloud-vfs doctor` output — that's the most helpful bug report at this stage.

---

## Before you post — checklist

- [ ] Account meets sub karma/age requirements (if any)
- [ ] Run `cloud-vfs try` on a clean machine today
- [ ] GitHub Issues enabled; respond to comments same day
- [ ] Optional: 3 screenshots or a short terminal GIF (scan → dry-run offload → ensure)
