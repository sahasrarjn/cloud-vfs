from __future__ import annotations

from pathlib import Path

from cloud_vfs.project import project_root
from cloud_vfs.storage.errors import PathOutsideProjectError

STUB_NAME = ".cloudstub"


def normalize_rel(path: str | Path) -> str:
    p = Path(path)
    root = project_root()
    if p.is_absolute():
        try:
            p = p.relative_to(root)
        except ValueError as exc:
            raise PathOutsideProjectError(
                f"Path {path!r} is outside project root {root}"
            ) from exc
    return p.as_posix().rstrip("/")


def abs_path(rel: str) -> Path:
    return project_root() / rel


def stub_file_for(rel: str) -> Path:
    rel = normalize_rel(rel)
    if Path(rel).suffix:
        return Path(f"{abs_path(rel)}{STUB_NAME}")
    return abs_path(rel) / STUB_NAME


def is_real_local(rel: str) -> bool:
    from .stub import is_ref_path

    rel = normalize_rel(rel)
    target = abs_path(rel)

    if target.is_file():
        return not is_ref_path(target)

    if not target.is_dir():
        legacy_sidecar = stub_file_for(rel)
        if legacy_sidecar.exists() and not target.exists():
            return False
        return target.exists()

    others = [
        p
        for p in target.rglob("*")
        if p.name != STUB_NAME and p.is_file() and not is_ref_path(p)
    ]
    return len(others) > 0
