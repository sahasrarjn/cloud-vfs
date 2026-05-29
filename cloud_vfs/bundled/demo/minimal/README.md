# cloud-vfs minimal demo

A tiny project you can clone to try [cloud-vfs](https://github.com/sahasrarjn/cloud-vfs) end-to-end. Uses a **1 MB** inventory threshold (not the production 50 MB default) so the sample file is easy to create.

## Prerequisites

- Python 3.9+
- [AWS CLI](https://aws.amazon.com/cli/) **or** [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- A dedicated test bucket/container (not production data)

## 1. Install

```bash
pip install cloud-vfs
# or: pip install git+https://github.com/sahasrarjn/cloud-vfs.git
```

## 2. Configure cloud

**AWS**

```bash
cp .cloud-vfs/config.env.example .cloud-vfs/config.env
# Edit: LOCAL_PROVIDER=aws, AWS_LOCAL_BUCKET, AWS_LOCAL_REGION
aws configure   # or AWS_PROFILE
```

**Azure**

```bash
cp .cloud-vfs/config.env.example .cloud-vfs/config.env
cp .cloud-vfs/secrets.env.example .cloud-vfs/secrets.env
# Edit config.env + secrets.env (AZ_LOCAL_STORAGE_KEY)
```

## 3. Verify setup

```bash
cd examples/minimal-demo   # if you cloned the main repo
cloud-vfs doctor
cloud-vfs doctor --probe
cloud-vfs doctor --roundtrip   # optional: upload + download probe object
```

## 4. Create sample data

```bash
./scripts/create-sample.sh
```

This writes `data/sample/large.bin` (~2 MB).

## 5. Walkthrough

```bash
cloud-vfs register data/sample/large.bin
cloud-vfs status

cloud-vfs offload --dry-run data/sample
cloud-vfs offload data/sample          # uploads + stub; frees local bytes

cloud-vfs status
cloud-vfs ensure data/sample           # fetch back from cloud
ls -la data/sample/
```

## Use as a GitHub template

Copy this folder to a new repo, or click **Use this template** after enabling templates on a repo that contains only `examples/minimal-demo/` at its root.

## Next steps

- [Main README](https://github.com/sahasrarjn/cloud-vfs/blob/main/README.md)
- [docs/CLOUD_VFS.md](https://github.com/sahasrarjn/cloud-vfs/blob/main/docs/CLOUD_VFS.md)
- `cloud-vfs init --skill` in your real ML project
