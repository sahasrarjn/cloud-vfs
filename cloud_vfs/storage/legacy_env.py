"""Undocumented env keys for older installs (not referenced in public docs)."""

from __future__ import annotations

from typing import Any

# (canonical env key, legacy fallbacks…)
_REMOTE_AZURE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "AZ_REMOTE_CONTAINER": ("AZ_RUNPOD_CONTAINER",),
    "AZ_REMOTE_LOC": ("AZ_RUNPOD_LOC",),
    "AZ_REMOTE_STORAGE_ACCOUNT": ("AZ_RUNPOD_STORAGE_ACCOUNT",),
    "AZ_REMOTE_STORAGE_KEY": ("AZ_RUNPOD_STORAGE_KEY",),
}


def _first(*values: Any) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


def remote_azure_env(env: dict[str, str], canonical_key: str, *manifest_values: Any) -> str | None:
    keys = (canonical_key, *_REMOTE_AZURE_FALLBACKS.get(canonical_key, ()))
    return _first(*manifest_values, *(env.get(k) for k in keys))
