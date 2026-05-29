from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root

from .config import ArchiveConfig
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


def _aws_base(cfg: ArchiveConfig) -> list[str]:
    cmd = ["aws"]
    if cfg.profile:
        cmd += ["--profile", cfg.profile]
    if cfg.region:
        cmd += ["--region", cfg.region]
    return cmd


def fetch_path(meta: dict[str, Any], rel: str, cfg: ArchiveConfig) -> int:
    rel = normalize_rel(rel)
    root = project_root()
    dest = abs_path(rel)

    if cfg.provider == "aws":
        if meta.get("blob"):
            key = meta["blob"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            uri = f"s3://{cfg.bucket}/{key}"
            _run(_aws_base(cfg) + ["s3", "cp", uri, str(dest)])
            return dest.stat().st_size
        prefix = (meta.get("blob_prefix") or rel).rstrip("/")
        dest.mkdir(parents=True, exist_ok=True)
        uri = f"s3://{cfg.bucket}/{prefix}/"
        _run(_aws_base(cfg) + ["s3", "sync", uri, str(dest), "--only-show-errors"])
        if not dest.exists() or not any(dest.iterdir()):
            raise FileNotFoundError(f"S3 sync completed but {rel} is empty or missing")
        return _dir_size(dest)

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
                cfg.account or "",
                "--account-key",
                cfg.key or "",
                "--container-name",
                cfg.bucket,
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
            cfg.account or "",
            "--account-key",
            cfg.key or "",
            "--source",
            cfg.bucket,
            "--destination",
            str(root),
            "--pattern",
            pattern,
            "--no-progress",
        ]
    )
    if not dest.exists():
        raise FileNotFoundError(f"Batch download completed but {rel} missing")
    return _dir_size(dest)


def upload_path(rel: str, cfg: ArchiveConfig, *, blob_prefix: str | None = None) -> str:
    rel = normalize_rel(rel)
    src = abs_path(rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    key_base = (blob_prefix or rel).rstrip("/")

    if cfg.provider == "aws":
        if src.is_dir():
            uri = f"s3://{cfg.bucket}/{key_base}/"
            _run(_aws_base(cfg) + ["s3", "sync", str(src), uri, "--only-show-errors"])
            sample = next(src.rglob("*"))
            key = f"{key_base}/{sample.relative_to(src).as_posix()}"
        else:
            key = key_base if blob_prefix else rel
            uri = f"s3://{cfg.bucket}/{key}"
            _run(_aws_base(cfg) + ["s3", "cp", str(src), uri])
        _run(_aws_base(cfg) + ["s3", "ls", f"s3://{cfg.bucket}/{key}"])
        return key

    if src.is_dir():
        dest_path = key_base if blob_prefix else rel
        _run(
            [
                "az",
                "storage",
                "blob",
                "upload-batch",
                "--account-name",
                cfg.account or "",
                "--account-key",
                cfg.key or "",
                "--destination",
                cfg.bucket,
                "--source",
                str(src),
                "--destination-path",
                dest_path,
                "--overwrite",
                "true",
                "--no-progress",
            ]
        )
        sample = next(src.rglob("*"))
        blob_name = f"{dest_path}/{sample.relative_to(src).as_posix()}"
    else:
        _run(
            [
                "az",
                "storage",
                "blob",
                "upload",
                "--account-name",
                cfg.account or "",
                "--account-key",
                cfg.key or "",
                "--container-name",
                cfg.bucket,
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
            cfg.account or "",
            "--account-key",
            cfg.key or "",
            "--container-name",
            cfg.bucket,
            "--name",
            blob_name,
            "-o",
            "none",
        ]
    )
    return blob_name
