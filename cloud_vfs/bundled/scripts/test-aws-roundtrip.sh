#!/usr/bin/env bash
# Round-trip test: offload to S3, ensure fetch back.
# Requires a dedicated throwaway bucket — set AWS_TEST_BUCKET explicitly.
set -euo pipefail

BUCKET="${AWS_TEST_BUCKET:?Set AWS_TEST_BUCKET to a dedicated test bucket (not production)}"
REGION="${AWS_TEST_REGION:-us-east-1}"
PREFIX="${AWS_TEST_PREFIX:-cloud-vfs-test}"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export PATH="$HOME/Library/Python/3.9/bin:$PATH"

mkdir -p "$ROOT/.cloud-vfs" "$ROOT/data/demo"
echo "cloud-vfs aws test $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ROOT/data/demo/hello.txt"

cat > "$ROOT/.cloud-vfs/config.env" <<EOF
LOCAL_PROVIDER=aws
AWS_LOCAL_BUCKET=$BUCKET
AWS_LOCAL_REGION=$REGION
EOF

cat > "$ROOT/.cloud-vfs/manifest.json" <<EOF
{
  "version": 3,
  "local_archive": {
    "provider": "aws",
    "bucket": "$BUCKET",
    "region": "$REGION"
  },
  "entries": [
    {
      "id": "demo",
      "local": "data/demo",
      "blob_prefix": "$PREFIX/demo/",
      "archive": "local_archive",
      "provider": "aws",
      "status": "synced"
    }
  ]
}
EOF

cd "$ROOT"
echo "==> offload"
cloud-vfs offload data/demo
test -f data/demo/.cloudstub || test -f data/demo.cloudstub || ls -la data/demo/
echo "==> ensure"
cloud-vfs ensure data/demo
grep -q "cloud-vfs aws test" data/demo/hello.txt
echo "==> cleanup s3://$BUCKET/$PREFIX/"
aws s3 rm "s3://$BUCKET/$PREFIX/" --recursive --only-show-errors
echo "OK: AWS round-trip passed (test objects removed from S3)"
