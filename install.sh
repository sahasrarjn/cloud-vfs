#!/usr/bin/env bash
# Install cloud-vfs from GitHub (sahasrarjn/cloud-vfs)
set -euo pipefail

BIN_DIR="${CLOUD_VFS_BIN:-$HOME/.local/bin}"
REPO="${CLOUD_VFS_REPO:-https://github.com/sahasrarjn/cloud-vfs.git}"

echo "Installing cloud-vfs ..."
if pip3 install --user "cloud-vfs" --upgrade 2>/dev/null; then
  echo "Installed from PyPI (cloud-vfs)"
else
  echo "PyPI install failed; falling back to $REPO ..."
  pip3 install --user "git+${REPO}" --upgrade
fi

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
  cloud-vfs doctor
  cloud-vfs-setup

Try it (sandbox demo):
  cloud-vfs try
  cd cloud-vfs-try && cp .cloud-vfs/config.env.example .cloud-vfs/config.env

Quick start in your project:
  cloud-vfs init --skill
  cloud-vfs doctor --roundtrip

https://github.com/sahasrarjn/cloud-vfs

EOF
