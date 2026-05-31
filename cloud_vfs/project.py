from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
BUNDLED = PKG_ROOT / "bundled"


def find_project_root(start: Path | None = None) -> Path:
    if os.environ.get("CLOUD_VFS_PROJECT_ROOT"):
        return Path(os.environ["CLOUD_VFS_PROJECT_ROOT"]).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".cloud-vfs").is_dir():
            return candidate
        # Optional consumer layouts (not required by cloud-vfs core)
        if (candidate / "infra" / "blob-manifest.json").exists():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return here


@lru_cache(maxsize=1)
def project_root() -> Path:
    return find_project_root()


def config_path() -> Path:
    if os.environ.get("CLOUD_VFS_CONFIG"):
        return Path(os.environ["CLOUD_VFS_CONFIG"]).expanduser()
    root = project_root()
    for candidate in (
        root / ".cloud-vfs" / "config.env",
        root / "runpod" / "config.env",  # legacy optional layout
    ):
        if candidate.exists():
            return candidate
    return root / ".cloud-vfs" / "config.env"


def secrets_path() -> Path:
    if os.environ.get("CLOUD_VFS_SECRETS"):
        return Path(os.environ["CLOUD_VFS_SECRETS"]).expanduser()
    root = project_root()
    for candidate in (
        root / ".cloud-vfs" / "secrets.env",
        root / "runpod" / "secrets.env",  # legacy optional layout
    ):
        if candidate.exists():
            return candidate
    return root / ".cloud-vfs" / "secrets.env"


def manifest_path() -> Path:
    if os.environ.get("CLOUD_VFS_MANIFEST"):
        return Path(os.environ["CLOUD_VFS_MANIFEST"]).expanduser()
    root = project_root()
    for candidate in (
        root / ".cloud-vfs" / "manifest.json",
        root / "infra" / "blob-manifest.json",
    ):
        if candidate.exists():
            return candidate
    return root / ".cloud-vfs" / "manifest.json"


def inventory_policy_path() -> Path:
    return project_root() / ".cloud-vfs" / "inventory-policy.json"


def temp_dir() -> Path:
    path = project_root() / ".cloud-vfs" / ".tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def package_path(*parts: str) -> Path:
    return BUNDLED.joinpath(*parts)


def fetch_cmd(rel: str) -> str:
    return f"cloud-vfs ensure {rel}"
