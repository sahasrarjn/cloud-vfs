from __future__ import annotations

import os
from pathlib import Path

from cloud_vfs.project import config_path, secrets_path


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def load_cloud_env() -> dict[str, str]:
    env = _parse_env_file(config_path())
    env.update(_parse_env_file(secrets_path()))
    for prefix in ("AZ_", "AWS_", "LOCAL_", "REMOTE_", "CLOUD_VFS_"):
        env.update({k: v for k, v in os.environ.items() if k.startswith(prefix)})
    env.update(
        {
            k: v
            for k, v in os.environ.items()
            if k in ("AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION")
        }
    )
    return env


def load_azure_env() -> dict[str, str]:
    """Backward-compatible alias."""
    return load_cloud_env()


def normalize_archive(archive: str) -> str:
    if archive == "runpod_staging":
        return "remote_staging"
    return archive
