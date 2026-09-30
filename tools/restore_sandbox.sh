#!/usr/bin/env bash
# Restore this sandbox after a workspace reset.
#
# The resets wipe the installed pip packages, kill running processes and rewind git to the
# base commit; only the repository itself survives.  This reinstalls the runtime deps, points
# the checkout back at the pushed branch, and reports how much of an export is on disk.
#
#   bash tools/restore_sandbox.sh
#
# Then start the two long-lived processes:
#   python3 -u -m mg.server.app --no-browser --host 0.0.0.0 --port 8000
#   bash tools/build_ashenfall.sh out/ashenfall 4
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
BRANCH="arena/01a0ece3-map-generator"

echo "== deps =="
if python3 -c "import numpy, scipy, PIL" 2>/dev/null; then
  echo "   already installed"
else
  pip install --break-system-packages -q numpy scipy pillow pypdf && echo "   installed"
fi
python3 -c "import numpy, scipy, PIL; print('   numpy', numpy.__version__, 'scipy', scipy.__version__)" 2>&1 | tail -1

echo "== repo =="
cd "$REPO" || exit 1
git fetch origin -q
git reset --hard "origin/$BRANCH" >/dev/null 2>&1
echo "   HEAD $(git log --oneline -1)"

echo "== generated output =="
if [ -d out/ashenfall ]; then
  echo "   $(find out/ashenfall -name '*.mca' 2>/dev/null | wc -l) region files, $(du -sh out/ashenfall 2>/dev/null | cut -f1)"
else
  echo "   none - run: bash tools/build_ashenfall.sh out/ashenfall 4"
fi
echo "== done =="
