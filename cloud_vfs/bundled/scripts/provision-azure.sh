#!/usr/bin/env bash
# Provision BOTH Azure blob accounts from .cloud-vfs/config.env
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-azure.sh
source "$SCRIPT_DIR/lib-azure.sh"
load_cloud_vfs_config

echo "Azure user: $(azure_user)"
echo "Subscription: $(az account show --query name -o tsv)"

STATE=$(az provider show --namespace Microsoft.Storage --query registrationState -o tsv 2>/dev/null || echo "Unknown")
if [ "$STATE" != "Registered" ]; then
  echo "[register] Microsoft.Storage provider ($STATE)..."
  az provider register --namespace Microsoft.Storage --wait -o none
fi

echo "==> Local archive: ${AZ_LOCAL_STORAGE_ACCOUNT} (${AZ_LOCAL_LOC})"
LOCAL_KEY=$(provision_storage_account "$AZ_LOCAL_RG" "$AZ_LOCAL_LOC" \
  "$AZ_LOCAL_STORAGE_ACCOUNT" "$AZ_LOCAL_CONTAINER")

echo "==> Cloud staging: ${AZ_RUNPOD_STORAGE_ACCOUNT} (${AZ_RUNPOD_LOC})"
RUNPOD_KEY=$(provision_storage_account "$AZ_RUNPOD_RG" "$AZ_RUNPOD_LOC" \
  "$AZ_RUNPOD_STORAGE_ACCOUNT" "$AZ_RUNPOD_CONTAINER")

SECRETS="$(secrets_file)"
cat > "$SECRETS" <<EOF
# Auto-generated — do not commit
AZ_LOCAL_STORAGE_KEY='$LOCAL_KEY'
AZ_RUNPOD_STORAGE_KEY='$RUNPOD_KEY'
EOF
chmod 600 "$SECRETS"

echo
echo "Local archive: $(az_local_blob_url)"
echo "Cloud staging: $(az_runpod_blob_url)"
echo "Secrets: $SECRETS"
