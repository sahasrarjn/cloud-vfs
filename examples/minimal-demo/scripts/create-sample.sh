#!/usr/bin/env bash
# Create ~2 MB sample file under data/sample/ for register/offload demo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/data/sample/large.bin"
mkdir -p "$(dirname "$OUT")"

if [[ -f "$OUT" ]]; then
  echo "Already exists: $OUT ($(wc -c <"$OUT") bytes)"
  exit 0
fi

# 2 MiB of deterministic-ish bytes (fast, no dd dependency)
python3 - <<'PY' "$OUT"
import sys
from pathlib import Path
out = Path(sys.argv[1])
chunk = b"cloud-vfs-demo\n" * 65536  # 1 MiB per write
with out.open("wb") as f:
    f.write(chunk)
    f.write(chunk)
print(f"wrote {out} ({out.stat().st_size} bytes)")
PY

echo "Next: cloud-vfs register data/sample/large.bin"
