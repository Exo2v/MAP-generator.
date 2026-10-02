#!/usr/bin/env bash
# Build the continent of Vantyra, and keep building it until it finishes.
#
# A full 8,000 x 8,000 export is 250,000 chunks and takes tens of minutes, so this wraps
# the turnkey generator in a supervise loop: if the process dies, it starts again, and
# because the ashenfall preset sets export.resume the exporter skips every chunk already
# on disk.  A restart therefore costs one pipeline run, not the whole world.
#
#   tools/build_ashenfall.sh [out_dir] [cell_size]
#
# Written for a POSIX shell; on Windows use generate_ashfall.py directly.
set -u
DIR=${1:-out/ashenfall}
CELL=${2:-4}
HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$HERE" || exit 1
mkdir -p "$DIR"

for attempt in $(seq 1 40); do
  echo "=== attempt $attempt  $(date '+%Y-%m-%d %H:%M:%S') ==="
  python3 -u generate_ashfall.py --out both --dir "$DIR" --cell "$CELL"
  code=$?
  echo "=== attempt $attempt exited $code  $(date '+%Y-%m-%d %H:%M:%S') ==="
  if [ "$code" -eq 0 ]; then
    echo "=== BUILD COMPLETE -> $DIR ==="
    break
  fi
  sleep 15
done
