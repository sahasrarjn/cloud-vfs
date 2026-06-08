# Design: `offload_always_prefixes` (issue #27)

## Problem

The inventory policy gates files by size: `should_index(rel, size)` is true only
when the path is `in_scope` **and** `size >= min_size_for(rel)`. The default
`min_size_bytes` is 50 MB. Trees that are numerous-but-small — per-run
`metrics.json` / `*_predictions.csv`, pooling variant seq dicts (~15–35 MB each) —
never get inventory rows, so `offload <prefix>` skips them even when the user
explicitly names the tree.

`prefix_min_size_bytes` can lower the threshold per prefix, but the user must pick
a number low enough to catch everything; there is no "this whole tree is always
eligible, regardless of size" switch.

## Feature

Add a policy key:

```json
{ "offload_always_prefixes": [] }
```

Any path matching one of these prefixes is **eligible for inventory and offload
regardless of `min_size_bytes`**, while still respecting `include_prefixes` /
`exclude_prefixes`. This covers behaviors 1 and 2 of the issue. The optional
many-small-files count heuristic (behavior 3) is out of scope (YAGNI).

## Approach: single chokepoint in `min_size_for`

Every size gate in the codebase derives from `min_size_for(rel, policy)` —
directly (`detect_drift`) or via `should_index` (`scan.discover_large_local`,
`register_paths`, `hash_paths_before_offload`, `index_offloaded_path`,
`prune_inventory`). So the entire feature is one change:

```python
def min_size_for(rel, policy):
    rel = normalize_rel(rel)
    for prefix in policy.get("offload_always_prefixes") or []:
        if _prefix_matches(rel, prefix):
            return 0
    # ... existing prefix_min_size_bytes longest-prefix logic ...
```

A small file under a named prefix now gets scanned, hashed, uploaded, indexed,
and survives `prune`. No call-site edits.

### Precedence (decided)

- **`offload_always_prefixes` beats `prefix_min_size_bytes`.** Returning `0` early
  short-circuits the longest-prefix override; an "always" prefix is by definition
  the lowest possible threshold, so order doesn't matter.
- **`exclude_prefixes` beats `offload_always_prefixes`.** `min_size_for` only
  relaxes the size gate. `in_scope` (include/exclude) is untouched, so an excluded
  path stays excluded even if it also matches an always-prefix. This matches the
  issue's "still respect include_prefixes / exclude_prefixes" requirement and keeps
  exclude an absolute opt-out.

## Changes

1. **`cloud_vfs/storage/inventory.py`**
   - Add `"offload_always_prefixes": []` to `DEFAULT_POLICY`.
   - Add the early-return loop to `min_size_for`.

2. **`cloud_vfs/bundled/templates/inventory-policy.json.example`**
   - Add `"offload_always_prefixes": []` with a representative commented intent
     (kept as `[]` so fresh repos opt in deliberately).

3. **`docs/INVENTORY.md`**
   - Add a row to the policy field table and a short note on precedence
     (always-prefix bypasses size but not exclude).

4. **`CHANGELOG.md`** — one line under the unreleased section.

## Tests (`tests/test_issues.py`)

A new `unittest.TestCase` (or additions to `IssueFixTests`) covering
`min_size_for` / `should_index` directly — no cloud round-trip needed:

- Sub-`min_size_bytes` file under an `offload_always_prefixes` entry →
  `should_index` is `True` (eligible).
- Same-size file **outside** the prefix → `should_index` is `False`.
- File matching both `exclude_prefixes` and `offload_always_prefixes` →
  `should_index` is `False` (exclude wins).
- File matching both `offload_always_prefixes` and a larger
  `prefix_min_size_bytes` override → `min_size_for` returns `0` (always wins).
- Default policy (no key) → behavior unchanged (regression guard).

## Out of scope

- `offload_many_small_files_min_count` directory-count heuristic.
- Any change to `in_scope` / include-exclude semantics.
- A dedicated `offload --force` CLI flag (the policy key is the mechanism).
