from __future__ import annotations

from pathlib import Path

from cloud_vfs.project import project_root

STUB_NAME = ".cloudstub"


def normalize_rel(path: str | Path) -> str:
    p = Path(path)
    root = project_root()
    if p.is_absolute():
        p = p.relative_to(root)
    return p.as_posix().rstrip("/")


def abs_path(rel: str) -> Path:
    return project_root() / rel


def stub_file_for(rel: str) -> Path:
    rel = normalize_rel(rel)
    if Path(rel).suffix:
        return Path(f"{abs_path(rel)}{STUB_NAME}")
    return abs_path(rel) / STUB_NAME


def is_real_local(rel: str) -> bool:
    rel = normalize_rel(rel)
    stub = stub_file_for(rel)
    target = abs_path(rel)
    if stub.exists():
        if not target.exists():
            return False
        if target.is_file():
            return False
        if target.is_dir():
            others = [p for p in target.rglob("*") if p.name != STUB_NAME and p.is_file()]
            return len(others) > 0
        return False
    return target.exists()
