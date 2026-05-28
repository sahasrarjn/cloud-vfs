#!/usr/bin/env bash
# Install cloud-vfs from GitHub (sahasrarjn/cloud-vfs)
set -euo pipefail

BIN_DIR="${CLOUD_VFS_BIN:-$HOME/.local/bin}"
REPO="${CLOUD_VFS_REPO:-https://github.com/sahasrarjn/cloud-vfs.git}"

echo "Installing cloud-vfs from $REPO ..."
pip3 install --user "git+${REPO}" --upgrade

mkdir -p "$BIN_DIR"
SCRIPTS="$(python3 -c "from cloud_vfs.project import package_path; print(package_path('scripts'))")"
ln -sf "$SCRIPTS/setup-blob-storage.sh" "$BIN_DIR/cloud-vfs-setup"
chmod +x "$SCRIPTS"/*.sh

if ! echo "$PATH" | grep -q "$HOME/Library/Python"; then
  echo
  echo "Add Python user scripts to PATH (e.g. in ~/.zshrc):"
  echo '  export PATH="$HOME/Library/Python/3.9/bin:$PATH"   # adjust 3.9 for your Python version'
fi

cat <<'EOF'

Installed:
  cloud-vfs --help
  cloud-vfs-setup

Quick start in your project:
  cloud-vfs init --skill
  cloud-vfs-setup

https://github.com/sahasrarjn/cloud-vfs

EOF
