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


def load_azure_env() -> dict[str, str]:
    env = _parse_env_file(config_path())
    env.update(_parse_env_file(secrets_path()))
    env.update({k: v for k, v in os.environ.items() if k.startswith("AZ_")})
    return env


def archive_credentials(env: dict[str, str], archive: str) -> tuple[str, str, str]:
    if archive == "local_archive":
        return (
            env["AZ_LOCAL_STORAGE_ACCOUNT"],
            env["AZ_LOCAL_STORAGE_KEY"],
            env["AZ_LOCAL_CONTAINER"],
        )
    if archive == "runpod_staging":
        return (
            env["AZ_RUNPOD_STORAGE_ACCOUNT"],
            env["AZ_RUNPOD_STORAGE_KEY"],
            env["AZ_RUNPOD_CONTAINER"],
        )
    raise ValueError(f"Unknown archive: {archive}")
