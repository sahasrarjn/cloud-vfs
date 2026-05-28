#!/usr/bin/env bash
# Shared Azure helpers for cloud-vfs scripts.
set -euo pipefail

find_project_root() {
  local dir="$PWD"
  if [ -n "${CLOUD_VFS_PROJECT_ROOT:-}" ]; then
    echo "$CLOUD_VFS_PROJECT_ROOT"
    return
  fi
  while [ "$dir" != "/" ]; do
    if [ -d "$dir/.cloud-vfs" ] || [ -f "$dir/infra/blob-manifest.json" ]; then
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
  root="$(find_project_root)"
  if [ -f "$root/.cloud-vfs/config.env" ]; then
    echo "$root/.cloud-vfs/config.env"
  elif [ -f "$root/runpod/config.env" ]; then
    echo "$root/runpod/config.env"
  else
    echo "$root/.cloud-vfs/config.env"
  fi
}

secrets_file() {
  if [ -n "${CLOUD_VFS_SECRETS:-}" ]; then
    echo "$CLOUD_VFS_SECRETS"
    return
  fi
  local root cfg
  root="$(find_project_root)"
  cfg="$(config_file)"
  if [ -f "$root/.cloud-vfs/secrets.env" ]; then
    echo "$root/.cloud-vfs/secrets.env"
  elif [ -f "$root/runpod/secrets.env" ]; then
    echo "$root/runpod/secrets.env"
  else
    echo "$root/.cloud-vfs/secrets.env"
  fi
}

load_cloud_vfs_config() {
  local cfg sec
  cfg="$(config_file)"
  sec="$(secrets_file)"
  # shellcheck disable=SC1090
  [ -f "$cfg" ] && source "$cfg"
  # shellcheck disable=SC1090
  [ -f "$sec" ] && source "$sec"
}

require_az() {
  command -v az >/dev/null || { echo "Install Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2; exit 1; }
  az account show >/dev/null || { echo "Run: az login" >&2; exit 1; }
}

azure_user() {
  az account show --query user.name -o tsv 2>/dev/null
}

az_local_blob_url() {
  echo "https://${AZ_LOCAL_STORAGE_ACCOUNT}.blob.core.windows.net/${AZ_LOCAL_CONTAINER}"
}

az_runpod_blob_url() {
  echo "https://${AZ_RUNPOD_STORAGE_ACCOUNT}.blob.core.windows.net/${AZ_RUNPOD_CONTAINER}"
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
