# Try cloud-vfs in 5 minutes

Use a **dedicated test bucket** (not production data).

## One command sandbox

```bash
pip install cloud-vfs
# or: curl -fsSL https://raw.githubusercontent.com/sahasrarjn/cloud-vfs/main/install.sh | bash

cloud-vfs try
cd cloud-vfs-try
```

`cloud-vfs try` creates `./cloud-vfs-try` with demo policy (1 MB threshold), manifest, and scripts.

## Configure cloud

**AWS**

```bash
cp .cloud-vfs/config.env.example .cloud-vfs/config.env
# Set LOCAL_PROVIDER=aws, AWS_LOCAL_BUCKET, AWS_LOCAL_REGION
aws configure   # or export AWS_PROFILE
```

**Azure**

```bash
cp .cloud-vfs/config.env.example .cloud-vfs/config.env
cp .cloud-vfs/secrets.env.example .cloud-vfs/secrets.env
# Set account, container, and AZ_LOCAL_STORAGE_KEY
```

## Verify

```bash
cloud-vfs doctor
cloud-vfs doctor --roundtrip
```

Fix anything marked `FAIL` before continuing.

## Run the demo

```bash
./scripts/create-sample.sh
cloud-vfs register data/sample/large.bin
cloud-vfs offload --dry-run data/sample
cloud-vfs offload data/sample
cloud-vfs ensure data/sample
ls -la data/sample/
```

## Use in your real repo

```bash
cd your-ml-project
cloud-vfs init --path . --skill
cloud-vfs doctor --roundtrip
cloud-vfs scan
cloud-vfs scan --add && cloud-vfs offload --dry-run
```

See [YOUR_REPO.md](YOUR_REPO.md), [README](../README.md), and [CLOUD_VFS.md](CLOUD_VFS.md).
