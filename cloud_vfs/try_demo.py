from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

from cloud_vfs.project import package_path


def _copy_demo(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        if item.suffix == ".sh":
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def cmd_try(path: Path, *, force: bool) -> int:
    src = package_path("demo", "minimal")
    if not src.is_dir():
        print(f"ERROR: bundled demo missing at {src}", file=sys.stderr)
        return 1

    dest = path.resolve()
    if dest.exists():
        if (dest / ".cloud-vfs").is_dir() and not force:
            print(f"Already a cloud-vfs project: {dest}")
            print("  cloud-vfs doctor")
            print("  ./scripts/create-sample.sh")
            return 0
        if any(dest.iterdir()) and not force:
            print(f"ERROR: {dest} is not empty (use --force or pick an empty directory)", file=sys.stderr)
            return 1
    else:
        dest.mkdir(parents=True)

    _copy_demo(src, dest)
    print(f"Demo project ready: {dest}\n")
    print("Next (5 minutes):")
    print(f"  cd {dest}")
    print("  cp .cloud-vfs/config.env.example .cloud-vfs/config.env")
    print("  # Edit bucket/region (use a dedicated TEST bucket)")
    print("  cloud-vfs doctor")
    print("  cloud-vfs doctor --roundtrip")
    print("  ./scripts/create-sample.sh")
    print("  cloud-vfs register data/sample/large.bin")
    print("  cloud-vfs offload --dry-run data/sample")
    print("  cloud-vfs offload data/sample")
    print("  cloud-vfs ensure data/sample")
    return 0
