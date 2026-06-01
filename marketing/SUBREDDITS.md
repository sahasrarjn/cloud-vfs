# Marketing — subreddit targets

Local-only notes (this folder is gitignored). Goal: **5–10 people** run `cloud-vfs try` and give feedback.

## Start here (best odds)

| Subreddit | Size (approx.) | Why it fits | Post angle |
|-----------|----------------|-------------|------------|
| [r/LocalLLaMA](https://reddit.com/r/LocalLLaMA) | ~400k+ | Disk/checkpoint pain; S3, HF caches, large `models/` trees | Keep repo small; lazy-fetch weights from S3/Azure — lighter than DVC for local workflows |
| [r/MachineLearning](https://reddit.com/r/MachineLearning) | ~2.8M | Core ML audience; tool posts OK if framed as discussion | DVC/LFS comparison + `cloud-vfs try`; ask what would make them trust it |
| [r/bioinformatics](https://reddit.com/r/bioinformatics) | ~80k+ | Huge `data/` dirs, shared lab repos, cluster disk limits | Offload run outputs to blob without bloating git — manual, agent-safe |
| [r/computationalbiology](https://reddit.com/r/computationalbiology) | ~15k+ | Smaller, aligned for research repos with large artifacts | Same as bioinformatics; optional real repo layout as example |
| [r/mlops](https://reddit.com/r/mlops) | ~30k+ | Artifact storage vs git is already the conversation | Explicit offload + inventory drift audit; no background jobs |

## Strong second wave

| Subreddit | Why | Caveat |
|-----------|-----|--------|
| [r/devops](https://reddit.com/r/devops) | S3/Azure blob as project VFS; dry-run before delete | Frame as infra pattern, not a product launch |
| [r/aws](https://reddit.com/r/aws) | Multi-cloud + `aws` CLI | Stay technical; link docs not sales copy |
| [r/AZURE](https://reddit.com/r/AZURE) | Azure Blob + `az` CLI | Same as r/aws |
| [r/Python](https://reddit.com/r/Python) | `pip install`, simple CLI, MIT | Broad — fewer people have the pain |
| [r/SideProject](https://reddit.com/r/SideProject) | “I built this because DVC felt heavy” | Good for feedback; less ML-specific |
| [r/selfhosted](https://reddit.com/r/selfhosted) | Manual control, no cron — matches design | You choose what leaves disk |

## Niche but high conversion

| Subreddit | Why |
|-----------|-----|
| [r/Cursor](https://reddit.com/r/Cursor) | Built-in Cursor skill is a differentiator |
| [r/StableDiffusion](https://reddit.com/r/StableDiffusion) | Model/checkpoint hoarding = disk pain |
| [r/homelab](https://reddit.com/r/homelab) | Small disks, lots of data |

## Skip or save for later

| Subreddit | Why skip |
|-----------|----------|
| r/learnmachinelearning | Mostly beginners; low “200GB in `data/`” pain |
| r/programming | Too generic; tool posts often die |
| r/technology | Wrong crowd; hostile to self-promo |

## Posting tips

- **Timing:** Tue–Thu, US morning for ML/dev subs.
- **Title template:** *I built a lightweight alternative to DVC for large `data/` dirs — lazy fetch from S3/Azure, agent-safe dry-run offload*
- **CTA:** Ask for 5 people to run `pip install cloud-vfs && cloud-vfs try` (~5 min) and report what broke.
- **Reuse body** across subs; change only the **first paragraph** for each audience.
- Reply to comments within a few hours — often doubles installs.

## Also consider (outside Reddit)

- **Hacker News Show HN** — classic launch for dev tools; one good thread beats weeks of tweeting.
- **X (Twitter)** — wait until you have a 60–90s terminal demo + traction from Reddit/HN to link back to.

## Draft posts

| File | Subreddit |
|------|-----------|
| [posts/r-mlops.md](posts/r-mlops.md) | r/mlops |
