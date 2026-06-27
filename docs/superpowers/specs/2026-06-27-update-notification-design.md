# Update notification — design

## Goal

Nudge users running an outdated `cloud-vfs` to upgrade (motivated by the 0.5.10
silent-upload data-loss bug). **Notify only** — never modify the user's
environment. Non-blocking, quiet by default in automation, zero new
dependencies.

## Behavior

After a CLI command finishes, if a newer version exists on PyPI, print one
non-blocking line to **stderr** (never stdout, so `--json` output stays clean):

```
cloud-vfs 0.5.10 → 0.5.11 available — run: pip install -U cloud-vfs
```

### Stays silent when any of:
- `CLOUD_VFS_NO_UPDATE_CHECK=1` (explicit opt-out)
- CI detected (`CI` truthy, or `GITHUB_ACTIONS` / `GITLAB_CI` set)
- stderr is not a TTY (piped / scripted / captured)
- the cache directory cannot be read or written

### Throttle
- Cache file `~/.cache/cloud-vfs/update-check.json` (honors `XDG_CACHE_HOME`;
  override with `CLOUD_VFS_CACHE_DIR`), shape:
  `{"last_check": <epoch>, "latest_version": "X.Y.Z"}`.
- The notice prints from cache on **every** run when a newer version is known —
  no network needed.
- PyPI is queried at most once per `CLOUD_VFS_UPDATE_CHECK_INTERVAL` seconds
  (default 86400). `last_check` is updated even on a failed fetch so a
  persistent network problem doesn't retry every run.

### Network
- `GET https://pypi.org/pypi/cloud-vfs/json`, parse `info.version`.
- stdlib `urllib` only; timeout `CLOUD_VFS_UPDATE_TIMEOUT` seconds (default 1.5).
- Any error (network, timeout, parse, fs) → swallow and do nothing.

### Version comparison
- Parse the leading `X.Y.Z` integer components; compare as tuples.
- Anything unparseable on either side → treat as "not newer" (skip).

## Components

`cloud_vfs/update_check.py`:
- `maybe_notify_update(current_version, *, stream=sys.stderr)` — orchestrator,
  wrapped so it can never raise.
- `_is_enabled()` — env/TTY/CI gating.
- `_cache_path()` / `_load_cache()` / `_save_cache()`.
- `_fetch_latest_version(timeout)` — PyPI query.
- `_parse_version(s)` / `_is_newer(latest, current)`.

`cloud_vfs/cli.py`:
- Rename the current `main()` body to `_dispatch(argv)`.
- New thin `main(argv=None)`: `rc = _dispatch(argv); maybe_notify_update(__version__); return rc`.
  `--version` / `--help` exit inside `_dispatch`, so they get no notice.

## Testing (`tests/test_update_check.py`)

- prints a notice when cache shows a newer version; nothing when equal/older
- silent when disabled / CI / non-TTY
- network/parse errors are swallowed (no raise, no output)
- throttle: no network call when cache is fresh; call when stale; `last_check`
  advanced even on fetch failure
- `_parse_version` / `_is_newer` unit cases (incl. unparseable)
- `main()` still returns the dispatched rc and the notifier never breaks it

## Out of scope (YAGNI)
- Auto-updating / running pip.
- Special-casing specific buggy versions.
- Pre-release / PEP 440 full ordering (best-effort numeric compare only).
