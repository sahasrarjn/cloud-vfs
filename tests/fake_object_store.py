"""In-memory fake S3 for end-to-end tests.

Patches the single subprocess choke point (``cloud_vfs.storage.backends._run``)
so the *real* offload / ensure / reconcile code paths execute against an
in-memory bucket instead of the `aws` CLI. This exercises upload, the
post-upload HEAD verification, tree verification, download, and reconcile blob
probing exactly as in production — only the network is faked.

Failure modes are first-class so we can reproduce the data-loss bugs:
- ``drop_uploads=True`` — every upload reports success but stores nothing
  (the issue #37 silent-upload scenario).
- ``drop_keys`` — a predicate to drop *specific* keys (partial tree upload).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from cloud_vfs.storage.errors import CloudStorageError
from cloud_vfs.storage.paths import STUB_NAME


def _key_from_uri(uri: str) -> str:
    # s3://bucket/some/key -> some/key
    assert uri.startswith("s3://"), uri
    rest = uri[len("s3://") :]
    return rest.split("/", 1)[1] if "/" in rest else ""


class FakeS3:
    def __init__(
        self,
        *,
        drop_uploads: bool = False,
        drop_keys: Callable[[str], bool] | None = None,
    ) -> None:
        self.store: dict[str, bytes] = {}
        self.drop_uploads = drop_uploads
        self.drop_keys = drop_keys

    # -- helpers ---------------------------------------------------------
    def put(self, key: str, data: bytes) -> None:
        self.store[key] = data

    def delete_prefix(self, prefix: str) -> int:
        victims = [k for k in self.store if k == prefix or k.startswith(prefix.rstrip("/") + "/")]
        for k in victims:
            del self.store[k]
        return len(victims)

    def keys_under(self, prefix: str) -> list[str]:
        p = prefix.rstrip("/")
        return [k for k in self.store if k == p or k.startswith(p + "/")]

    def _should_store(self, key: str) -> bool:
        if self.drop_uploads:
            return False
        if self.drop_keys and self.drop_keys(key):
            return False
        return True

    # -- the fake _run ---------------------------------------------------
    def _run(self, cmd, *, action: str, **kwargs):  # noqa: ANN001
        cmd = list(cmd)
        if "s3api" in cmd and "head-object" in cmd:
            key = cmd[cmd.index("--key") + 1]
            if key in self.store:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"ContentLength": len(self.store[key])})
                )
            raise CloudStorageError(action, cmd, "An error occurred (404) ... Not Found", 254)

        if "s3" in cmd:
            i = cmd.index("s3")
            sub = cmd[i + 1]
            positional = [a for a in cmd[i + 2 :] if not a.startswith("--")]
            if sub == "cp":
                src, dst = positional[0], positional[1]
                if src.startswith("s3://"):  # download
                    key = _key_from_uri(src)
                    if key not in self.store:
                        raise CloudStorageError(action, cmd, "An error occurred (404) ... Not Found", 1)
                    Path(dst).write_bytes(self.store[key])
                    return subprocess.CompletedProcess(cmd, 0, stdout="")
                key = _key_from_uri(dst)  # upload
                if self._should_store(key):
                    self.store[key] = Path(src).read_bytes()
                return subprocess.CompletedProcess(cmd, 0, stdout="")
            if sub == "sync":
                src, dst = positional[0], positional[1]
                if src.startswith("s3://"):  # download tree
                    prefix = _key_from_uri(src).rstrip("/")
                    for key in self.keys_under(prefix):
                        rel = key[len(prefix) :].lstrip("/")
                        out = Path(dst) / rel
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(self.store[key])
                    return subprocess.CompletedProcess(cmd, 0, stdout="")
                prefix = _key_from_uri(dst).rstrip("/")  # upload tree
                base = Path(src)
                for p in sorted(base.rglob("*")):
                    if not p.is_file() or p.name == STUB_NAME:
                        continue
                    key = f"{prefix}/{p.relative_to(base).as_posix()}"
                    if self._should_store(key):
                        self.store[key] = p.read_bytes()
                return subprocess.CompletedProcess(cmd, 0, stdout="")
            if sub == "ls":
                prefix = _key_from_uri(positional[0]) if positional[0].startswith("s3://") else ""
                lines = [
                    f"2026-06-27 00:00:00 {len(self.store[k]):>10} {k}"
                    for k in sorted(self.store)
                    if k.startswith(prefix)
                ]
                return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines) + ("\n" if lines else ""))

        raise CloudStorageError(action, cmd, f"fake-s3: unhandled command {cmd}", 1)

    def patch(self):
        return patch("cloud_vfs.storage.backends._run", side_effect=self._run)
