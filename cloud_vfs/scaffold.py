from __future__ import annotations

import shutil
from pathlib import Path

from cloud_vfs.project import package_path


def cmd_init(project: Path, *, install_skill: bool) -> int:
    project = project.resolve()
    cfg_dir = project / ".cloud-vfs"
    cfg_dir.mkdir(exist_ok=True)

    copies = {
        "config.env.example": cfg_dir / "config.env",
        "secrets.env.example": cfg_dir / "secrets.env.example",
        "manifest.json": cfg_dir / "manifest.json",
        "inventory-policy.json.example": cfg_dir / "inventory-policy.json",
    }
    for src_name, dest in copies.items():
        src = package_path("templates", src_name)
        if dest.name in ("config.env", "manifest.json", "inventory-policy.json") and dest.exists():
            print(f"keep existing: {dest}")
            continue
        shutil.copy2(src, dest)
        print(f"wrote: {dest}")

    index_dir = cfg_dir / "index"
    index_dir.mkdir(exist_ok=True)
    index_readme = index_dir / "README.md"
    if not index_readme.exists():
        shutil.copy2(package_path("templates", "index-README.md"), index_readme)
        print(f"wrote: {index_readme}")

    gitignore = project / ".gitignore"
    lines = [
        ".cloud-vfs/secrets.env",
        ".cloud-vfs/.tmp",
        ".cloud-vfs/locks",
        "**/.cloudstub",
        ".cloud-vfs/index/data/generated/",
        ".cloud-vfs/index/code.json",
    ]
    if gitignore.exists():
        existing = gitignore.read_text().splitlines()
        missing = [line for line in lines if line not in existing]
        if missing:
            gitignore.write_text(gitignore.read_text().rstrip() + "\n" + "\n".join(missing) + "\n")
            print(f"updated: {gitignore}")
    else:
        gitignore.write_text("\n".join(lines) + "\n")
        print(f"wrote: {gitignore}")

    if install_skill:
        skill_src = package_path("skills", "cloud-vfs")
        skill_dest = project / ".cursor" / "skills" / "cloud-vfs"
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        if skill_dest.exists():
            print(f"keep existing skill: {skill_dest}")
        else:
            shutil.copytree(skill_src, skill_dest)
            print(f"installed skill: {skill_dest}")

    print()
    print("Next:")
    print("  1. cloud-vfs doctor")
    print("  2. Edit .cloud-vfs/manifest.json -> local_archive")
    print("       (provider/bucket/region/account) — this is what doctor & offload read.")
    print("       Put Azure storage keys in secrets.env; config.env holds CLI fallbacks.")
    print("  3. cloud-vfs-setup   # optional interactive wizard + Azure provision")
    print("  4. cloud-vfs doctor --roundtrip")
    print("  5. cloud-vfs scan                 # see what you can offload")
    print("  6. cloud-vfs scan --add && cloud-vfs offload --dry-run")
    print("  7. cloud-vfs offload <paths>      # after you confirm")
    print()
    print("New to cloud-vfs? Run: cloud-vfs try   # sandbox demo in ./cloud-vfs-try")
    return 0
