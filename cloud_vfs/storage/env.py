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


def _env_get(env: dict[str, str], primary: str, legacy: str) -> str:
    if primary in env and env[primary]:
        return env[primary]
    if legacy in env and env[legacy]:
        return env[legacy]
    raise KeyError(primary)


def normalize_archive(archive: str) -> str:
    if archive == "runpod_staging":
        return "remote_staging"
    return archive


def archive_credentials(env: dict[str, str], archive: str) -> tuple[str, str, str]:
    archive = normalize_archive(archive)
    if archive == "local_archive":
        return (
            _env_get(env, "AZ_LOCAL_STORAGE_ACCOUNT", "AZ_LOCAL_STORAGE_ACCOUNT"),
            _env_get(env, "AZ_LOCAL_STORAGE_KEY", "AZ_LOCAL_STORAGE_KEY"),
            _env_get(env, "AZ_LOCAL_CONTAINER", "AZ_LOCAL_CONTAINER"),
        )
    if archive == "remote_staging":
        return (
            _env_get(env, "AZ_REMOTE_STORAGE_ACCOUNT", "AZ_RUNPOD_STORAGE_ACCOUNT"),
            _env_get(env, "AZ_REMOTE_STORAGE_KEY", "AZ_RUNPOD_STORAGE_KEY"),
            _env_get(env, "AZ_REMOTE_CONTAINER", "AZ_RUNPOD_CONTAINER"),
        )
    raise ValueError(f"Unknown archive: {archive} (use local_archive or remote_staging)")
