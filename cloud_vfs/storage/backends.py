from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cloud_vfs.project import project_root

from .config import ArchiveConfig
from .errors import CloudStorageError
from .paths import STUB_NAME, abs_path, normalize_rel

PROGRESS_MIN_BYTES = 100 * 1024 * 1024  # azcopy threshold + show native CLI progress for large uploads
AZCOPY_MIN_BYTES = PROGRESS_MIN_BYTES
_STREAM_READ_SIZE = 4096
_UPLOAD_RETRY_ATTEMPTS = 3
_UPLOAD_RETRY_BASE_SEC = 5.0


def _is_ci() -> bool:
    if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
        return True
    return bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("GITLAB_CI"))


def _default_idle_timeout_sec() -> float:
    raw = os.environ.get("CLOUD_VFS_SUBPROCESS_IDLE_TIMEOUT_SEC", "600")
    try:
        return float(raw)
    except ValueError:
        return 600.0


def _should_show_upload_progress(src: Path) -> bool:
    if not sys.stdout.isatty() or _is_ci():
        return False
    if src.is_dir():
        return True
    try:
        return src.stat().st_size >= PROGRESS_MIN_BYTES
    except OSError:
        return False


def _run_monitored(
    cmd: list[str],
    *,
    action: str,
    label: str | None = None,
    heartbeat_sec: float = 30.0,
    idle_timeout_sec: float | None = None,
    heartbeat_prefix: str = "[cloud-vfs offload]",
    stream_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    idle_timeout_sec = idle_timeout_sec if idle_timeout_sec is not None else _default_idle_timeout_sec()
    if label:
        print(label, flush=True)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines: list[str] = []
    stop = threading.Event()
    timed_out = threading.Event()
    last_output = time.monotonic()
    output_lock = threading.Lock()

    def _reader() -> None:
        nonlocal last_output
        assert proc.stdout is not None
        if stream_output:
            while True:
                chunk = proc.stdout.read(_STREAM_READ_SIZE)
                if not chunk:
                    break
                with output_lock:
                    output_lines.append(chunk)
                    last_output = time.monotonic()
                print(chunk, end="", flush=True)
        else:
            for line in proc.stdout:
                with output_lock:
                    output_lines.append(line)
                    last_output = time.monotonic()

    def _heartbeat() -> None:
        start = time.monotonic()
        while not stop.wait(heartbeat_sec):
            with output_lock:
                idle = time.monotonic() - last_output
            if idle >= idle_timeout_sec:
                timed_out.set()
                proc.kill()
                stop.set()
                return
            if stream_output and idle < heartbeat_sec:
                continue
            elapsed = int(time.monotonic() - start)
            mins, secs = divmod(elapsed, 60)
            elapsed_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
            print(f"{heartbeat_prefix} still running… ({elapsed_str} elapsed)", flush=True)

    reader = threading.Thread(target=_reader, daemon=True)
    heartbeat = threading.Thread(target=_heartbeat, daemon=True)
    reader.start()
    heartbeat.start()
    rc = proc.wait()
    stop.set()
    reader.join()
    heartbeat.join(timeout=1.0)

    stdout = "".join(output_lines)
    if timed_out.is_set():
        raise CloudStorageError(
            action,
            cmd,
            f"no subprocess output for {int(idle_timeout_sec)}s — aborted (command may be hung)",
            rc or 1,
        )
    if rc != 0:
        raise CloudStorageError(action, cmd, stdout.strip(), rc)
    return subprocess.CompletedProcess(cmd, rc, stdout=stdout)


def _upload_retry_attempts() -> int:
    raw = os.environ.get("CLOUD_VFS_UPLOAD_RETRIES", str(_UPLOAD_RETRY_ATTEMPTS))
    try:
        return max(1, int(raw))
    except ValueError:
        return _UPLOAD_RETRY_ATTEMPTS


def _run(cmd: list[str], *, action: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    label = kwargs.pop("label", None)
    heartbeat_prefix = kwargs.pop("heartbeat_prefix", "[cloud-vfs]")
    idle_timeout_sec = kwargs.pop("idle_timeout_sec", None)
    stream_output = kwargs.pop("stream_output", False)
    retries = kwargs.pop("retries", 1)
    try:
        last_exc: CloudStorageError | None = None
        delay = _UPLOAD_RETRY_BASE_SEC
        for attempt in range(retries):
            try:
                return _run_monitored(
                    cmd,
                    action=action,
                    label=label if attempt == 0 else None,
                    heartbeat_prefix=heartbeat_prefix,
                    idle_timeout_sec=idle_timeout_sec,
                    stream_output=stream_output,
                )
            except CloudStorageError as exc:
                last_exc = exc
                if attempt + 1 >= retries:
                    raise
                print(
                    f"{heartbeat_prefix} upload failed, retry {attempt + 2}/{retries} "
                    f"in {int(delay)}s …",
                    flush=True,
                )
                time.sleep(delay)
                delay *= 2
        if last_exc:
            raise last_exc
        raise CloudStorageError(action, cmd, "upload retries exhausted", 1)
    except subprocess.CalledProcessError:
        raise
    except CloudStorageError:
        raise


def blob_content_length(cfg: ArchiveConfig, blob_key: str) -> int | None:
    """Return blob size in bytes, or None if the object does not exist."""
    if cfg.provider == "aws":
        try:
            result = _run(
                _aws_base(cfg)
                + [
                    "s3api",
                    "head-object",
                    "--bucket",
                    cfg.bucket,
                    "--key",
                    blob_key,
                ],
                action=f"head s3://{cfg.bucket}/{blob_key}",
                idle_timeout_sec=120.0,
            )
        except CloudStorageError:
            return None
        data = json.loads(result.stdout or "{}")
        length = data.get("ContentLength")
        return int(length) if length is not None else None

    try:
        result = _run(
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
                blob_key,
                "-o",
                "json",
            ],
            action=f"show azure blob {blob_key}",
            idle_timeout_sec=120.0,
        )
    except CloudStorageError:
        return None
    data = json.loads(result.stdout or "{}")
    props = data.get("properties") or {}
    length = props.get("contentLength")
    return int(length) if length is not None else None


def _azcopy_on_path() -> bool:
    return shutil.which("azcopy") is not None


def _azure_blob_url(cfg: ArchiveConfig, blob_name: str) -> str:
    account = cfg.account or "ACCOUNT"
    return f"https://{account}.blob.core.windows.net/{cfg.bucket}/{blob_name}"


def _generate_blob_sas(cfg: ArchiveConfig, blob_name: str, *, write: bool = False) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    permissions = "cw" if write else "r"
    result = _run(
        [
            "az",
            "storage",
            "blob",
            "generate-sas",
            "--account-name",
            cfg.account or "",
            "--account-key",
            cfg.key or "",
            "--container-name",
            cfg.bucket,
            "--name",
            blob_name,
            "--permissions",
            permissions,
            "--expiry",
            expiry,
            "-o",
            "tsv",
        ],
        action=f"generate sas for azure blob {blob_name}",
        idle_timeout_sec=120.0,
    )
    token = (result.stdout or "").strip()
    if not token:
        raise CloudStorageError("generate sas", [], "empty SAS token", 1)
    return token


def choose_azure_transport(size_bytes: int | None) -> str:
    """Return 'azcopy' or 'az-cli' for Azure blob transfers."""
    if size_bytes is not None and size_bytes < AZCOPY_MIN_BYTES:
        return "az-cli"
    if _azcopy_on_path():
        return "azcopy"
    if size_bytes is not None and size_bytes >= AZCOPY_MIN_BYTES:
        print(
            "[cloud-vfs] WARNING: azcopy not found on PATH — falling back to "
            "'az storage blob' (slow for multi-GB files). Install azcopy v10.",
            flush=True,
        )
    return "az-cli"


def azure_blob_url_redacted(cfg: ArchiveConfig, blob_name: str) -> str:
    return _azure_blob_url(cfg, blob_name)


def blob_matches_local_size(
    cfg: ArchiveConfig,
    blob_key: str,
    local_path: Path,
) -> bool:
    """True when blob exists and its size matches the local file (upload resume)."""
    if not local_path.is_file():
        return False
    try:
        local_size = local_path.stat().st_size
    except OSError:
        return False
    blob_size = blob_content_length(cfg, blob_key)
    return blob_size is not None and blob_size == local_size


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


def _should_stream_transfer(size_bytes: int | None) -> bool:
    if not sys.stdout.isatty() or _is_ci():
        return False
    if size_bytes is None:
        return True
    return size_bytes >= AZCOPY_MIN_BYTES


def _azure_download_blob(
    cfg: ArchiveConfig,
    blob: str,
    dest: Path,
    *,
    progress_label: str | None = None,
    known_size: int | None = None,
) -> None:
    expected = known_size if known_size is not None else blob_content_length(cfg, blob)
    transport = choose_azure_transport(expected)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if transport == "azcopy":
        try:
            sas = _generate_blob_sas(cfg, blob, write=False)
        except CloudStorageError:
            print(
                "[cloud-vfs] WARNING: SAS generation failed — falling back to "
                "'az storage blob download'",
                flush=True,
            )
            transport = "az-cli"
        else:
            src_url = f"{_azure_blob_url(cfg, blob)}?{sas}"
            partial = dest.with_name(dest.name + ".part")
            partial.unlink(missing_ok=True)
            cmd = [
                "azcopy",
                "copy",
                src_url,
                str(partial),
                "--from-to=BlobLocal",
                "--overwrite=true",
                "--check-length=true",
                "--output-type=text",
            ]
            try:
                _run(
                    cmd,
                    action=f"azcopy download azure blob {blob}",
                    label=progress_label,
                    heartbeat_prefix="[cloud-vfs ensure]",
                    stream_output=_should_stream_transfer(expected),
                )
                partial.replace(dest)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            return

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
        ],
        action=f"download azure blob {blob}",
        label=progress_label,
        heartbeat_prefix="[cloud-vfs ensure]",
    )


def _azure_upload_blob(
    cfg: ArchiveConfig,
    blob_name: str,
    src: Path,
    *,
    progress_label: str | None = None,
) -> None:
    try:
        size = src.stat().st_size
    except OSError:
        size = None
    transport = choose_azure_transport(size)
    show_progress = _should_show_upload_progress(src)

    if transport == "azcopy":
        try:
            sas = _generate_blob_sas(cfg, blob_name, write=True)
        except CloudStorageError:
            print(
                "[cloud-vfs] WARNING: SAS generation failed — falling back to "
                "'az storage blob upload'",
                flush=True,
            )
            transport = "az-cli"
        else:
            dst_url = f"{_azure_blob_url(cfg, blob_name)}?{sas}"
            cmd = [
                "azcopy",
                "copy",
                str(src),
                dst_url,
                "--from-to=LocalBlob",
                "--overwrite=true",
                "--check-length=true",
                "--output-type=text",
            ]
            _run(
                cmd,
                action=f"azcopy upload azure blob {blob_name}",
                label=progress_label,
                heartbeat_prefix="[cloud-vfs offload]",
                stream_output=show_progress or _should_stream_transfer(size),
                retries=_upload_retry_attempts(),
            )
            return

    upload_cmd = [
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
        blob_name,
        "--file",
        str(src),
        "--overwrite",
    ]
    if not show_progress:
        upload_cmd.append("--no-progress")
    _run(
        upload_cmd,
        action=f"upload azure blob {blob_name}",
        label=progress_label,
        stream_output=show_progress,
        retries=_upload_retry_attempts(),
    )


def fetch_path(
    meta: dict[str, Any],
    rel: str,
    cfg: ArchiveConfig,
    *,
    dest: Path | None = None,
    dest_root: Path | None = None,
    progress_label: str | None = None,
) -> int:
    rel = normalize_rel(rel)
    root = dest_root or project_root()
    final_dest = dest or abs_path(rel)
    heartbeat_prefix = "[cloud-vfs ensure]"

    if cfg.provider == "aws":
        if meta.get("blob"):
            key = meta["blob"]
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            uri = f"s3://{cfg.bucket}/{key}"
            _run(
                _aws_base(cfg) + ["s3", "cp", uri, str(final_dest)],
                action=f"download s3://{cfg.bucket}/{key}",
                label=progress_label,
                heartbeat_prefix=heartbeat_prefix,
            )
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
            label=progress_label,
            heartbeat_prefix=heartbeat_prefix,
        )
        if not final_dest.exists() or not any(final_dest.iterdir()):
            raise FileNotFoundError(f"S3 sync completed but {rel} is empty or missing")
        return _dir_size(final_dest)

    if meta.get("blob"):
        blob = meta["blob"]
        expected = blob_content_length(cfg, blob)
        _azure_download_blob(
            cfg,
            blob,
            final_dest,
            progress_label=progress_label,
            known_size=expected,
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
        label=progress_label,
        heartbeat_prefix=heartbeat_prefix,
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
    source_path: Path | None = None,
) -> str:
    rel = normalize_rel(rel)
    src = source_path if source_path is not None else abs_path(rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    key_base = (blob_prefix or rel).rstrip("/")
    show_progress = _should_show_upload_progress(src)

    if cfg.provider == "aws":
        if src.is_dir():
            sample = _first_data_file(src)
            uri = f"s3://{cfg.bucket}/{key_base}/"
            sync_cmd = _aws_base(cfg) + ["s3", "sync", str(src), uri]
            if not show_progress:
                sync_cmd.append("--only-show-errors")
            _run(
                sync_cmd,
                action=f"sync upload to s3://{cfg.bucket}/{key_base}/",
                label=progress_label,
                stream_output=show_progress,
                retries=_upload_retry_attempts(),
            )
            key = f"{key_base}/{sample.relative_to(src).as_posix()}"
        else:
            key = key_base if blob_prefix else rel
            uri = f"s3://{cfg.bucket}/{key}"
            cp_cmd = _aws_base(cfg) + ["s3", "cp", str(src), uri]
            if show_progress:
                cp_cmd.append("--progress-multiline")
            else:
                cp_cmd.append("--no-progress")
            _run(
                cp_cmd,
                action=f"upload s3://{cfg.bucket}/{key}",
                label=progress_label,
                stream_output=show_progress,
                retries=_upload_retry_attempts(),
            )
        _run(
            _aws_base(cfg) + ["s3", "ls", f"s3://{cfg.bucket}/{key}"],
            action=f"verify s3://{cfg.bucket}/{key}",
            idle_timeout_sec=120.0,
        )
        return key

    if src.is_dir():
        sample = _first_data_file(src)
        dest_path = key_base if blob_prefix else rel
        batch_cmd = [
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
        ]
        if not show_progress:
            batch_cmd.append("--no-progress")
        _run(
            batch_cmd,
            action=f"upload-batch azure {dest_path}/",
            label=progress_label,
            stream_output=show_progress,
            retries=_upload_retry_attempts(),
        )
        blob_name = f"{dest_path}/{sample.relative_to(src).as_posix()}"
    else:
        blob_name = rel
        _azure_upload_blob(
            cfg,
            blob_name,
            src,
            progress_label=progress_label,
        )

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
        idle_timeout_sec=120.0,
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
            idle_timeout_sec=300.0,
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
    result = _run(cmd, action=f"list azure blobs under {prefix or '(root)'}", idle_timeout_sec=300.0)
    data = json.loads(result.stdout or "[]")
    return [item["name"] for item in data if item.get("name")]
