#!/usr/bin/env bash
# Shared Azure helpers for cloud-vfs scripts.
set -euo pipefail

_find() {
  local dir="$PWD"
  if [ -n "${CLOUD_VFS_PROJECT_ROOT:-}" ]; then
    echo "$CLOUD_VFS_PROJECT_ROOT"
    return
  fi
  while [ "$dir" != "/" ]; do
    if [ -d "$dir/.cloud-vfs" ]; then
      echo "$dir"
      return
    fi
    dir="$(dirname "$dir")"
  done
  echo "$PWD"
}

config_file() {
  if [ -n "${CLOUD_VFS_CONFIG:-}" ]; then
    echo "$CLOUD_VFS_CONFIG"
    return
  fi
  local root
  root="$(_find)"
  echo "$root/.cloud-vfs/config.env"
}

secrets_file() {
  if [ -n "${CLOUD_VFS_SECRETS:-}" ]; then
    echo "$CLOUD_VFS_SECRETS"
    return
  fi
  local root
  root="$(_find)"
  echo "$root/.cloud-vfs/secrets.env"
}

load_cloud_vfs_config() {
  local cfg sec
  cfg="$(config_file)"
  sec="$(secrets_file)"
  # shellcheck disable=SC1090
  [ -f "$cfg" ] && source "$cfg"
  # shellcheck disable=SC1090
  [ -f "$sec" ] && source "$sec"
  # Undocumented legacy remote env names (still read if present)
  : "${AZ_REMOTE_STORAGE_ACCOUNT:=${AZ_RUNPOD_STORAGE_ACCOUNT:-}}"
  : "${AZ_REMOTE_STORAGE_KEY:=${AZ_RUNPOD_STORAGE_KEY:-}}"
  : "${AZ_REMOTE_CONTAINER:=${AZ_RUNPOD_CONTAINER:-}}"
  : "${AZ_REMOTE_RG:=${AZ_RUNPOD_RG:-}}"
  : "${AZ_REMOTE_LOC:=${AZ_RUNPOD_LOC:-}}"
}

require_az() {
  command -v az >/dev/null || {
    echo "Install Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
    exit 1
  }
  az account show >/dev/null || { echo "Run: az login" >&2; exit 1; }
}

azure_user() {
  az account show --query user.name -o tsv 2>/dev/null
}

az_local_blob_url() {
  echo "https://${AZ_LOCAL_STORAGE_ACCOUNT}.blob.core.windows.net/${AZ_LOCAL_CONTAINER}"
}

az_remote_blob_url() {
  echo "https://${AZ_REMOTE_STORAGE_ACCOUNT}.blob.core.windows.net/${AZ_REMOTE_CONTAINER}"
}

provision_storage_account() {
  local rg="$1" loc="$2" account="$3" container="$4"
  az group show -g "$rg" >/dev/null 2>&1 || az group create -g "$rg" -l "$loc" -o none
  if ! az storage account show -g "$rg" -n "$account" >/dev/null 2>&1; then
    echo "[create] storage account $account ($loc)..." >&2
    az storage account create \
      -g "$rg" -n "$account" -l "$loc" \
      --sku Standard_LRS --kind StorageV2 --access-tier Cool \
      --allow-blob-public-access false -o none
  else
    echo "[ok] storage account $account exists" >&2
  fi
  local key
  key=$(az storage account keys list -g "$rg" -n "$account" --query '[0].value' -o tsv)
  az storage container create --name "$container" --account-name "$account" \
    --account-key "$key" -o none 2>/dev/null || true
  printf '%s' "$key"
}
