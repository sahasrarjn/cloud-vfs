from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from cloud_vfs import __version__
from cloud_vfs.project import config_path, manifest_path, project_root, secrets_path
from cloud_vfs.storage.config import resolve_archive
from cloud_vfs.storage.env import load_cloud_env
from cloud_vfs.storage.inventory import load_policy
from cloud_vfs.storage.manifest import load_manifest


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # ok | warn | fail
    detail: str


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run_quiet(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 127, str(exc)
    out = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, out[:500]


def _check_python() -> CheckResult:
    if sys.version_info >= (3, 9):
        return CheckResult("python", "ok", f"{sys.version_info.major}.{sys.version_info.minor}")
    return CheckResult(
        "python",
        "fail",
        f"Python {sys.version_info.major}.{sys.version_info.minor} (need >= 3.9)",
    )


def _check_package() -> CheckResult:
    return CheckResult("cloud-vfs", "ok", __version__)


def _check_project() -> CheckResult:
    root = project_root()
    cfg = root / ".cloud-vfs"
    if cfg.is_dir():
        return CheckResult("project", "ok", str(root))
    return CheckResult(
        "project",
        "warn",
        f"No .cloud-vfs/ under {root} (run: cloud-vfs init)",
    )


def _check_file(path: Path, label: str) -> CheckResult:
    if path.exists():
        return CheckResult(label, "ok", str(path))
    return CheckResult(label, "fail", f"Missing: {path}")


def _check_manifest() -> CheckResult:
    try:
        load_manifest()
        return CheckResult("manifest", "ok", str(manifest_path()))
    except FileNotFoundError as exc:
        return CheckResult("manifest", "fail", str(exc))
    except ValueError as exc:
        return CheckResult("manifest", "fail", str(exc))


def _check_policy() -> CheckResult:
    path = project_root() / ".cloud-vfs" / "inventory-policy.json"
    if not path.exists():
        return CheckResult("inventory-policy", "warn", f"Missing (defaults apply): {path}")
    try:
        load_policy()
        return CheckResult("inventory-policy", "ok", str(path))
    except (json.JSONDecodeError, ValueError) as exc:
        return CheckResult("inventory-policy", "fail", str(exc))


def _check_download_temps() -> CheckResult | None:
    """Warn about stale download temps that re-bill egress if a retry re-downloads (issue #21)."""
    from cloud_vfs.storage.cleanup import find_download_temps, human_bytes

    temps = find_download_temps()
    if not temps:
        return None
    total = sum(size for _, size in temps)
    return CheckResult(
        "download-temps",
        "warn",
        f"{len(temps)} stale temp(s), {human_bytes(total)} — run: cloud-vfs cleanup-downloads",
    )


def _check_archive() -> CheckResult:
    try:
        env = load_cloud_env()
        manifest = load_manifest()
        cfg = resolve_archive(env, manifest, "local_archive")
        detail = f"{cfg.provider} bucket={cfg.bucket}"
        if cfg.provider == "azure" and not cfg.account:
            return CheckResult("local_archive", "fail", "Azure account not set in config.env / manifest")
        if cfg.provider == "azure" and not cfg.key:
            return CheckResult(
                "local_archive",
                "warn",
                f"{detail} (no AZ_LOCAL_STORAGE_KEY — will try Azure CLI login)",
            )
        return CheckResult("local_archive", "ok", detail)
    except FileNotFoundError as exc:
        return CheckResult("local_archive", "fail", str(exc))
    except (KeyError, ValueError) as exc:
        return CheckResult("local_archive", "fail", str(exc))


# local_archive placeholders shipped by the scaffolded manifest.json template.
_PLACEHOLDER_TOKENS = (
    "YOUR_AZURE_ACCOUNT",
    "your-s3-bucket-if-aws",
    "YOUR_REGION",
)


def _is_placeholder(value: str | None) -> bool:
    """True if a manifest field still holds a scaffolded template placeholder."""
    if not value:
        return False
    return value in _PLACEHOLDER_TOKENS


def _check_archive_sources() -> CheckResult | None:
    """Warn when config.env and manifest.json disagree (issue #34).

    ``resolve_archive`` reads provider/bucket/region/account from the manifest
    ``local_archive`` block first and falls back to config.env only when the block
    field is empty. So editing config.env alone is silently ignored whenever the
    manifest holds a (possibly placeholder) value. Surface that mismatch.
    """
    try:
        env = load_cloud_env()
        manifest = load_manifest()
    except (FileNotFoundError, ValueError):
        return None
    block = manifest.get("local_archive")
    if not isinstance(block, dict):
        return None

    provider = str(block.get("provider") or env.get("LOCAL_PROVIDER") or "azure").lower()
    if provider == "aws":
        pairs = [
            ("provider", block.get("provider"), env.get("LOCAL_PROVIDER")),
            ("bucket", block.get("bucket"), env.get("AWS_LOCAL_BUCKET")),
            ("region", block.get("region"), env.get("AWS_LOCAL_REGION")),
        ]
    else:
        pairs = [
            ("provider", block.get("provider"), env.get("LOCAL_PROVIDER")),
            ("container", block.get("container"), env.get("AZ_LOCAL_CONTAINER")),
            ("account", block.get("account"), env.get("AZ_LOCAL_STORAGE_ACCOUNT")),
            ("region", block.get("region"), env.get("AZ_LOCAL_LOC")),
        ]

    placeholders = [name for name, mval, _ in pairs if _is_placeholder(mval)]
    disagreements = [
        name
        for name, mval, env_val in pairs
        if mval
        and env_val
        and not _is_placeholder(mval)
        and str(mval).lower() != str(env_val).lower()
    ]

    if placeholders:
        return CheckResult(
            "config-source",
            "warn",
            "manifest.json local_archive still has scaffolded placeholder(s): "
            f"{', '.join(placeholders)}. doctor/offload read provider/bucket/region "
            "from manifest.json, so editing config.env alone has no effect — "
            "edit .cloud-vfs/manifest.json local_archive.",
        )
    if disagreements:
        return CheckResult(
            "config-source",
            "warn",
            f"config.env and manifest.json disagree on: {', '.join(disagreements)}. "
            "manifest.json wins, so the config.env value is ignored — "
            "align .cloud-vfs/manifest.json local_archive.",
        )
    return CheckResult(
        "config-source",
        "ok",
        "manifest.json drives local_archive (config.env consistent)",
    )


def _check_cli(provider: str) -> CheckResult:
    if provider == "aws":
        exe = "aws"
        version_cmd = [exe, "--version"]
    else:
        exe = "az"
        version_cmd = [exe, "version", "-o", "tsv"]
    if not _which(exe):
        return CheckResult(f"{exe}-cli", "fail", f"{exe} not found on PATH")
    code, out = _run_quiet(version_cmd)
    if code != 0:
        return CheckResult(f"{exe}-cli", "fail", out or f"{exe} --version failed")
    first = out.splitlines()[0] if out else exe
    return CheckResult(f"{exe}-cli", "ok", first)


def _check_credentials(provider: str, env: dict[str, str], cfg) -> CheckResult:
    if provider == "aws":
        cmd = ["aws"]
        if cfg.profile:
            cmd += ["--profile", cfg.profile]
        if cfg.region:
            cmd += ["--region", cfg.region]
        cmd += ["sts", "get-caller-identity", "-o", "json"]
        code, out = _run_quiet(cmd)
        if code != 0:
            return CheckResult("credentials", "fail", out or "aws sts get-caller-identity failed")
        try:
            data = json.loads(out)
            arn = data.get("Arn", "?")
        except json.JSONDecodeError:
            arn = out.splitlines()[0] if out else "ok"
        return CheckResult("credentials", "ok", arn)

    if env.get("AZ_LOCAL_STORAGE_KEY"):
        return CheckResult("credentials", "ok", "AZ_LOCAL_STORAGE_KEY in secrets.env")
    code, out = _run_quiet(["az", "account", "show", "-o", "json"])
    if code != 0:
        return CheckResult(
            "credentials",
            "fail",
            "Set AZ_LOCAL_STORAGE_KEY in secrets.env or run az login",
        )
    try:
        data = json.loads(out)
        name = data.get("name") or data.get("user", {}).get("name", "?")
    except json.JSONDecodeError:
        name = "azure account"
    return CheckResult("credentials", "ok", f"Azure CLI: {name}")


def _check_probe(provider: str, cfg) -> CheckResult:
    if provider == "aws":
        cmd = ["aws"]
        if cfg.profile:
            cmd += ["--profile", cfg.profile]
        if cfg.region:
            cmd += ["--region", cfg.region]
        cmd += ["s3", "ls", f"s3://{cfg.bucket}/", "--page-size", "1"]
        code, out = _run_quiet(cmd)
        if code != 0:
            return CheckResult("bucket-access", "fail", out or f"Cannot list s3://{cfg.bucket}/")
        return CheckResult("bucket-access", "ok", f"s3://{cfg.bucket}/")

    cmd = [
        "az",
        "storage",
        "container",
        "exists",
        "--account-name",
        cfg.account or "",
        "--account-key",
        cfg.key or "",
        "--name",
        cfg.bucket,
    ]
    if not cfg.key:
        cmd = [
            "az",
            "storage",
            "container",
            "exists",
            "--account-name",
            cfg.account or "",
            "--auth-mode",
            "login",
            "--name",
            cfg.bucket,
        ]
    code, out = _run_quiet(cmd)
    if code != 0:
        return CheckResult("bucket-access", "fail", out or f"Cannot access container {cfg.bucket}")
    if "true" not in out.lower():
        return CheckResult("bucket-access", "fail", f"Container not found: {cfg.bucket}")
    return CheckResult("bucket-access", "ok", cfg.bucket)


def _check_roundtrip(provider: str, cfg) -> CheckResult:
    token = uuid.uuid4().hex[:12]
    payload = f"cloud-vfs doctor probe {token}\n".encode()
    key = f"cloud-vfs-doctor/{token}.txt"

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.txt"
        dst = Path(tmp) / "probe-out.txt"
        src.write_bytes(payload)

        if provider == "aws":
            upload = ["aws"]
            if cfg.profile:
                upload += ["--profile", cfg.profile]
            if cfg.region:
                upload += ["--region", cfg.region]
            uri = f"s3://{cfg.bucket}/{key}"
            code, out = _run_quiet(upload + ["s3", "cp", str(src), uri])
            if code != 0:
                return CheckResult("roundtrip", "fail", out or "upload failed")
            code, out = _run_quiet(upload + ["s3", "cp", uri, str(dst)])
            if code != 0:
                _run_quiet(upload + ["s3", "rm", uri])
                return CheckResult("roundtrip", "fail", out or "download failed")
            _run_quiet(upload + ["s3", "rm", uri])
        else:
            base = [
                "az",
                "storage",
                "blob",
            ]
            if not cfg.key:
                return CheckResult(
                    "roundtrip",
                    "fail",
                    "Round-trip needs AZ_LOCAL_STORAGE_KEY in secrets.env",
                )
            code, out = _run_quiet(
                base
                + [
                    "upload",
                    "--account-name",
                    cfg.account or "",
                    "--account-key",
                    cfg.key or "",
                    "--container-name",
                    cfg.bucket,
                    "--name",
                    key,
                    "--file",
                    str(src),
                    "--overwrite",
                ]
            )
            if code != 0:
                return CheckResult("roundtrip", "fail", out or "upload failed")
            code, out = _run_quiet(
                base
                + [
                    "download",
                    "--account-name",
                    cfg.account or "",
                    "--account-key",
                    cfg.key or "",
                    "--container-name",
                    cfg.bucket,
                    "--name",
                    key,
                    "--file",
                    str(dst),
                ]
            )
            if code != 0:
                _run_quiet(
                    base
                    + [
                        "delete",
                        "--account-name",
                        cfg.account or "",
                        "--account-key",
                        cfg.key or "",
                        "--container-name",
                        cfg.bucket,
                        "--name",
                        key,
                    ]
                )
                return CheckResult("roundtrip", "fail", out or "download failed")
            _run_quiet(
                base
                + [
                    "delete",
                    "--account-name",
                    cfg.account or "",
                    "--account-key",
                    cfg.key or "",
                    "--container-name",
                    cfg.bucket,
                    "--name",
                    key,
                ]
            )

        if not dst.exists() or dst.read_bytes() != payload:
            return CheckResult("roundtrip", "fail", "Downloaded bytes do not match upload")
    return CheckResult("roundtrip", "ok", f"Uploaded and fetched {key}")


def run_checks(
    *,
    probe: bool = False,
    roundtrip: bool = False,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(_check_python())
    results.append(_check_package())
    results.append(_check_project())

    cfg_dir = project_root() / ".cloud-vfs"
    if not cfg_dir.is_dir():
        return results

    results.append(_check_file(config_path(), "config.env"))
    secrets = secrets_path()
    if secrets.exists():
        results.append(_check_file(secrets, "secrets.env"))
    else:
        results.append(
            CheckResult(
                "secrets.env",
                "warn",
                f"Optional: {secrets} (Azure keys; AWS uses CLI creds)",
            )
        )
    results.append(_check_manifest())
    results.append(_check_policy())
    temps_check = _check_download_temps()
    if temps_check is not None:
        results.append(temps_check)

    try:
        env = load_cloud_env()
        manifest = load_manifest()
        cfg = resolve_archive(env, manifest, "local_archive")
        provider = cfg.provider
    except (FileNotFoundError, KeyError, ValueError) as exc:
        results.append(CheckResult("local_archive", "fail", str(exc)))
        return results

    results.append(_check_archive())
    src_check = _check_archive_sources()
    if src_check is not None:
        results.append(src_check)
    results.append(_check_cli(provider))
    results.append(_check_credentials(provider, env, cfg))

    if probe or roundtrip:
        results.append(_check_probe(provider, cfg))
    if roundtrip:
        results.append(_check_roundtrip(provider, cfg))
    return results


def _status_icon(status: str) -> str:
    return {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(status, status.upper())


def cmd_doctor(*, as_json: bool, probe: bool, roundtrip: bool) -> int:
    if roundtrip:
        probe = True
    results = run_checks(probe=probe, roundtrip=roundtrip)
    if as_json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        print("cloud-vfs doctor\n")
        for r in results:
            print(f"  [{_status_icon(r.status):4}] {r.name}: {r.detail}")
        fails = sum(1 for r in results if r.status == "fail")
        warns = sum(1 for r in results if r.status == "warn")
        print()
        if fails:
            print(f"{fails} failure(s), {warns} warning(s). Fix failures before offload/fetch.")
        elif warns:
            print(f"All required checks passed ({warns} warning(s)).")
        else:
            print("All checks passed.")
        if not probe and not roundtrip:
            print("Tip: cloud-vfs doctor --probe   (test bucket access)")
            print("     cloud-vfs doctor --roundtrip   (upload + download probe object)")
    return 1 if any(r.status == "fail" for r in results) else 0
