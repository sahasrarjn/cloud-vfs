from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root

from .config import ArchiveConfig
from .errors import CloudStorageError
from .paths import STUB_NAME, abs_path, normalize_rel


def _run(cmd: list[str], *, action: str, **_ignored: Any) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or "").strip()
        raise CloudStorageError(action, cmd, stderr, exc.returncode) from exc


def _run_with_progress(
    cmd: list[str],
    *,
    action: str,
    label: str | None = None,
    heartbeat_sec: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    if label:
        print(label, flush=True)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines: list[str] = []
    stop = threading.Event()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            output_lines.append(line)

    def _heartbeat() -> None:
        start = time.monotonic()
        while not stop.wait(heartbeat_sec):
            elapsed = int(time.monotonic() - start)
            mins, secs = divmod(elapsed, 60)
            if mins:
                elapsed_str = f"{mins}m{secs:02d}s"
            else:
                elapsed_str = f"{secs}s"
            print(f"[cloud-vfs offload] still uploading… ({elapsed_str} elapsed)", flush=True)

    reader = threading.Thread(target=_reader, daemon=True)
    heartbeat = threading.Thread(target=_heartbeat, daemon=True)
    reader.start()
    heartbeat.start()
    rc = proc.wait()
    stop.set()
    reader.join()
    heartbeat.join(timeout=1.0)

    stdout = "".join(output_lines)
    if rc != 0:
        raise CloudStorageError(action, cmd, stdout.strip(), rc)
    return subprocess.CompletedProcess(cmd, rc, stdout=stdout)


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def _first_data_file(path: Path) -> Path:
    for candidate in path.rglob("*"):
        if candidate.is_file() and candidate.name != STUB_NAME:
            return candidate
    raise ValueError(f"Cannot upload empty directory: {path}")


def _aws_base(cfg: ArchiveConfig) -> list[str]:
    cmd = ["aws"]
    if cfg.profile:
        cmd += ["--profile", cfg.profile]
    if cfg.region:
        cmd += ["--region", cfg.region]
    return cmd


def fetch_path(
    meta: dict[str, Any],
    rel: str,
    cfg: ArchiveConfig,
    *,
    dest: Path | None = None,
    dest_root: Path | None = None,
) -> int:
    rel = normalize_rel(rel)
    root = dest_root or project_root()
    final_dest = dest or abs_path(rel)

    if cfg.provider == "aws":
        if meta.get("blob"):
            key = meta["blob"]
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            uri = f"s3://{cfg.bucket}/{key}"
            _run(_aws_base(cfg) + ["s3", "cp", uri, str(final_dest)], action=f"download s3://{cfg.bucket}/{key}")
            size = final_dest.stat().st_size
            if size == 0:
                raise FileNotFoundError(f"Downloaded blob is empty: {key}")
            return size
        prefix = (meta.get("blob_prefix") or rel).rstrip("/")
        final_dest.mkdir(parents=True, exist_ok=True)
        uri = f"s3://{cfg.bucket}/{prefix}/"
        _run(
            _aws_base(cfg) + ["s3", "sync", uri, str(final_dest), "--only-show-errors"],
            action=f"sync s3://{cfg.bucket}/{prefix}/",
        )
        if not final_dest.exists() or not any(final_dest.iterdir()):
            raise FileNotFoundError(f"S3 sync completed but {rel} is empty or missing")
        return _dir_size(final_dest)

    if meta.get("blob"):
        blob = meta["blob"]
        final_dest.parent.mkdir(parents=True, exist_ok=True)
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
                str(final_dest),
                "--no-progress",
            ],
            action=f"download azure blob {blob}",
        )
        size = final_dest.stat().st_size
        if size == 0:
            raise FileNotFoundError(f"Downloaded blob is empty: {blob}")
        return size

    prefix = (meta.get("blob_prefix") or rel).rstrip("/")
    final_dest.parent.mkdir(parents=True, exist_ok=True)
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
        ],
        action=f"download-batch azure prefix {prefix}/",
    )
    if not final_dest.exists():
        raise FileNotFoundError(f"Batch download completed but {rel} missing")
    if final_dest.is_dir() and not any(final_dest.iterdir()):
        raise FileNotFoundError(f"Batch download completed but {rel} is empty")
    return _dir_size(final_dest)


def upload_path(
    rel: str,
    cfg: ArchiveConfig,
    *,
    blob_prefix: str | None = None,
    progress_label: str | None = None,
) -> str:
    rel = normalize_rel(rel)
    src = abs_path(rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    key_base = (blob_prefix or rel).rstrip("/")

    upload = _run_with_progress if progress_label else _run

    if cfg.provider == "aws":
        if src.is_dir():
            sample = _first_data_file(src)
            uri = f"s3://{cfg.bucket}/{key_base}/"
            upload(
                _aws_base(cfg) + ["s3", "sync", str(src), uri, "--only-show-errors"],
                action=f"sync upload to s3://{cfg.bucket}/{key_base}/",
                label=progress_label,
            )
            key = f"{key_base}/{sample.relative_to(src).as_posix()}"
        else:
            key = key_base if blob_prefix else rel
            uri = f"s3://{cfg.bucket}/{key}"
            upload(
                _aws_base(cfg) + ["s3", "cp", str(src), uri],
                action=f"upload s3://{cfg.bucket}/{key}",
                label=progress_label,
            )
        _run(_aws_base(cfg) + ["s3", "ls", f"s3://{cfg.bucket}/{key}"], action=f"verify s3://{cfg.bucket}/{key}")
        return key

    if src.is_dir():
        sample = _first_data_file(src)
        dest_path = key_base if blob_prefix else rel
        upload(
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
            ],
            action=f"upload-batch azure {dest_path}/",
            label=progress_label,
        )
        blob_name = f"{dest_path}/{sample.relative_to(src).as_posix()}"
    else:
        upload(
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
            ],
            action=f"upload azure blob {rel}",
            label=progress_label,
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
        ],
        action=f"verify azure blob {blob_name}",
    )
    return blob_name


def list_blob_keys(cfg: ArchiveConfig, prefix: str) -> list[str]:
    prefix = prefix.rstrip("/")
    if prefix:
        prefix = prefix + "/"

    if cfg.provider == "aws":
        target = f"s3://{cfg.bucket}/{prefix}" if prefix else f"s3://{cfg.bucket}/"
        result = _run(
            _aws_base(cfg) + ["s3", "ls", target, "--recursive"],
            action=f"list s3://{cfg.bucket}/{prefix}",
        )
        keys: list[str] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 4:
                keys.append(parts[-1])
        return keys

    cmd = [
        "az",
        "storage",
        "blob",
        "list",
        "--account-name",
        cfg.account or "",
        "--account-key",
        cfg.key or "",
        "--container-name",
        cfg.bucket,
        "-o",
        "json",
    ]
    if prefix:
        cmd += ["--prefix", prefix]
    result = _run(cmd, action=f"list azure blobs under {prefix or '(root)'}")
    data = json.loads(result.stdout or "[]")
    return [item["name"] for item in data if item.get("name")]
