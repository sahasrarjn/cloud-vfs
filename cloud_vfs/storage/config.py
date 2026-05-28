from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cloud_vfs.storage.env import normalize_archive


@dataclass
class ArchiveConfig:
    name: str
    provider: str  # azure | aws
    bucket: str
    region: str | None = None
    # Azure
    account: str | None = None
    key: str | None = None
    # AWS
    profile: str | None = None

    @property
    def base_url(self) -> str:
        if self.provider == "aws":
            region = self.region or "us-east-1"
            if region == "us-east-1":
                return f"s3://{self.bucket}"
            return f"s3://{self.bucket} ({region})"
        account = self.account or "ACCOUNT"
        container = self.bucket
        return f"https://{account}.blob.core.windows.net/{container}"


def manifest_with_provider(
    manifest: dict[str, Any], archive: str, provider: str | None
) -> dict[str, Any]:
    if not provider:
        return manifest
    archive = normalize_archive(archive)
    block = dict(manifest.get(archive) or {})
    block["provider"] = provider
    return {**manifest, archive: block}


def resolve_archive(
    env: dict[str, str],
    manifest: dict[str, Any],
    archive_name: str,
) -> ArchiveConfig:
    archive_name = normalize_archive(archive_name)
    block = manifest.get(archive_name, {}) if isinstance(manifest.get(archive_name), dict) else {}

    if archive_name == "local_archive":
        provider = block.get("provider") or env.get("LOCAL_PROVIDER") or "azure"
    elif archive_name == "remote_staging":
        provider = block.get("provider") or env.get("REMOTE_PROVIDER") or "azure"
    else:
        raise ValueError(f"Unknown archive: {archive_name}")

    provider = str(provider).lower()
    if provider not in ("azure", "aws"):
        raise ValueError(f"Unsupported provider {provider!r} for {archive_name} (use azure or aws)")

    if provider == "aws":
        prefix = "AWS_LOCAL" if archive_name == "local_archive" else "AWS_REMOTE"
        legacy = "AWS"
        bucket = _first(
            block.get("bucket"),
            env.get(f"{prefix}_BUCKET"),
            env.get(f"{legacy}_BUCKET") if archive_name == "local_archive" else None,
        )
        region = _first(
            block.get("region"),
            env.get(f"{prefix}_REGION"),
            env.get("AWS_REGION"),
            env.get("AWS_DEFAULT_REGION"),
        )
        profile = _first(block.get("profile"), env.get(f"{prefix}_PROFILE"), env.get("AWS_PROFILE"))
        if not bucket:
            raise KeyError(f"{prefix}_BUCKET or manifest {archive_name}.bucket")
        return ArchiveConfig(
            name=archive_name,
            provider="aws",
            bucket=bucket,
            region=region,
            profile=profile or None,
        )

    # Azure (default)
    if archive_name == "local_archive":
        return ArchiveConfig(
            name=archive_name,
            provider="azure",
            bucket=_first(block.get("container"), env.get("AZ_LOCAL_CONTAINER"), "data"),
            region=_first(block.get("region"), env.get("AZ_LOCAL_LOC")),
            account=_first(block.get("account"), env.get("AZ_LOCAL_STORAGE_ACCOUNT")),
            key=_first(env.get("AZ_LOCAL_STORAGE_KEY")),
        )
    if archive_name == "remote_staging":
        return ArchiveConfig(
            name=archive_name,
            provider="azure",
            bucket=_first(
                block.get("container"),
                env.get("AZ_REMOTE_CONTAINER"),
                env.get("AZ_RUNPOD_CONTAINER"),
                "data",
            ),
            region=_first(block.get("region"), env.get("AZ_REMOTE_LOC"), env.get("AZ_RUNPOD_LOC")),
            account=_first(
                block.get("account"),
                env.get("AZ_REMOTE_STORAGE_ACCOUNT"),
                env.get("AZ_RUNPOD_STORAGE_ACCOUNT"),
            ),
            key=_first(env.get("AZ_REMOTE_STORAGE_KEY"), env.get("AZ_RUNPOD_STORAGE_KEY")),
        )
    raise ValueError(f"Unknown archive: {archive_name}")


def _first(*values: Any) -> str | None:
    for v in values:
        if v:
            return str(v)
    return None
