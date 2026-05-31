from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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


def load_cloud_env(
    *,
    config: Path | None = None,
    secrets: Path | None = None,
) -> dict[str, str]:
    env = _parse_env_file(config or config_path())
    env.update(_parse_env_file(secrets or secrets_path()))
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


BLOB_ROLE_ALIASES: dict[str, str] = {
    "primary": "local_archive",
    "archive": "local_archive",
    "local": "local_archive",
    "secondary": "remote_staging",
    "staging": "remote_staging",
    "remote": "remote_staging",
    "runpod_staging": "remote_staging",
    # legacy aliases (still accepted)
    "mac_archive": "local_archive",
    "gpu_staging": "remote_staging",
    "gpu": "remote_staging",
}


def normalize_archive(archive: str) -> str:
    return BLOB_ROLE_ALIASES.get(archive, archive)


def archive_from_entry(entry: dict[str, Any] | None, default: str = "local_archive") -> str:
    if not entry:
        return normalize_archive(default)
    raw = entry.get("blob_role") or entry.get("archive") or default
    return normalize_archive(str(raw))


def source_target_hints(rel: str, source_archive: str) -> dict[str, Any]:
    """CLI hints: cloud source archive -> filesystem target paths."""
    source_archive = normalize_archive(source_archive)
    ensure = f"cloud-vfs ensure {rel}"
    if source_archive != "local_archive":
        ensure += f" --source {source_archive}"
    return {
        "source": {
            "archive": source_archive,
            "ensure": ensure,
        },
        "target": {
            "project_root": ensure,
            "custom_root": (
                f"cloud-vfs ensure --target-root <DIR>"
                f" --source {source_archive} {rel}"
            ),
        },
    }
