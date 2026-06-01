from __future__ import annotations


class CloudVfsError(Exception):
    """Base error for cloud-vfs operations."""


class CloudStorageError(CloudVfsError):
    def __init__(self, action: str, cmd: list[str], stderr: str, returncode: int) -> None:
        self.action = action
        self.cmd = cmd
        self.stderr = stderr
        self.returncode = returncode
        detail = stderr or f"exit code {returncode}"
        super().__init__(f"{action} failed ({detail})")


class PathOutsideProjectError(CloudVfsError):
    pass
