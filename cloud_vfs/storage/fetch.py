from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root

from .env import archive_credentials, load_azure_env
from .paths import abs_path, normalize_rel


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def fetch_path(meta: dict[str, Any], rel: str) -> int:
    rel = normalize_rel(rel)
    env = load_azure_env()
    archive = meta.get("archive", "local_archive")
    account, key, container = archive_credentials(env, archive)
    root = project_root()

    dest = abs_path(rel)
    if meta.get("blob"):
        blob = meta["blob"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "az",
                "storage",
                "blob",
                "download",
                "--account-name",
                account,
                "--account-key",
                key,
                "--container-name",
                container,
                "--name",
                blob,
                "--file",
                str(dest),
                "--no-progress",
            ]
        )
        return dest.stat().st_size

    prefix = (meta.get("blob_prefix") or rel).rstrip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    pattern = f"{prefix}/*"
    _run(
        [
            "az",
            "storage",
            "blob",
            "download-batch",
            "--account-name",
            account,
            "--account-key",
            key,
            "--source",
            container,
            "--destination",
            str(root),
            "--pattern",
            pattern,
            "--no-progress",
        ]
    )
    target = abs_path(rel)
    if not target.exists():
        raise FileNotFoundError(f"Batch download completed but {rel} missing")
    return _dir_size(target)


def upload_path(rel: str, archive: str = "local_archive") -> str:
    rel = normalize_rel(rel)
    src = abs_path(rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    env = load_azure_env()
    account, key, container = archive_credentials(env, archive)

    if src.is_dir():
        _run(
            [
                "az",
                "storage",
                "blob",
                "upload-batch",
                "--account-name",
                account,
                "--account-key",
                key,
                "--destination",
                container,
                "--source",
                str(src),
                "--destination-path",
                rel,
                "--overwrite",
                "true",
                "--no-progress",
            ]
        )
        sample = next(src.rglob("*"))
        blob_name = f"{rel}/{sample.relative_to(src).as_posix()}"
    else:
        _run(
            [
                "az",
                "storage",
                "blob",
                "upload",
                "--account-name",
                account,
                "--account-key",
                key,
                "--container-name",
                container,
                "--name",
                rel,
                "--file",
                str(src),
                "--overwrite",
            ]
        )
        blob_name = rel

    _run(
        [
            "az",
            "storage",
            "blob",
            "show",
            "--account-name",
            account,
            "--account-key",
            key,
            "--container-name",
            container,
            "--name",
            blob_name,
            "-o",
            "none",
        ]
    )
    return blob_name
