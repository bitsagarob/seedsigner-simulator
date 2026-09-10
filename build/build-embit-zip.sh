#!/usr/bin/env bash
#
# The coordinator's Python: embit, and nothing else.
#
#   ./build/build-embit-zip.sh          ->  build/out/wallet-embit.zip
#
# The wallet zip carries a whole firmware and is megabytes. The coordinator
# needs a bitcoin library and no firmware at all, so it gets its own, built from
# the same pinned commit the device uses, which is what makes the two halves
# agree about what a PSBT is.
#
# Nothing of ours goes in here. This zip holds one upstream commit and its hash
# says so, which anybody can check by fetching that commit and rebuilding. Our
# own coordinator.py is served as a plain file, covered by build/checksums.txt
# like every other file this page serves.
#
# Deterministic the same way build-wallet-zip.sh is: fixed timestamps, fixed
# permissions, fixed order, no __pycache__. Run it twice and diff the hashes.
#
# Requires: bash, git, python3.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
OUT="${REPO_ROOT}/build/out/wallet-embit.zip"

# The one place this is pinned is the table in build-wallet-zip.sh, so it cannot
# drift from what the device runs.
ROW="$(grep '^git|embit|' "${SCRIPT_DIR}/build-wallet-zip.sh")"
URL="$(cut -d'|' -f5 <<< "${ROW}")"
COMMIT="$(cut -d'|' -f4 <<< "${ROW}")"

echo "==> embit ${COMMIT} from ${URL}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

GIT_TERMINAL_PROMPT=0 git -C "${WORK}" init --quiet
GIT_TERMINAL_PROMPT=0 git -C "${WORK}" remote add origin "${URL}"
GIT_TERMINAL_PROMPT=0 git -C "${WORK}" fetch --quiet --depth 1 origin "${COMMIT}"
GIT_TERMINAL_PROMPT=0 git -C "${WORK}" -c advice.detachedHead=false checkout --quiet FETCH_HEAD

mkdir -p "$(dirname "${OUT}")"
python3 - "${WORK}/src/embit" "${OUT}" <<'PY'
import os, sys, zipfile

source, out = sys.argv[1], sys.argv[2]
names = []
for root, dirs, files in os.walk(source):
    dirs[:] = sorted(d for d in dirs if d != "__pycache__")
    for name in sorted(files):
        if name.endswith(".pyc"):
            continue
        full = os.path.join(root, name)
        names.append((full, os.path.join("embit", os.path.relpath(full, source))))

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for full, arc in sorted(names, key=lambda pair: pair[1]):
        info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(full, "rb") as f:
            z.writestr(info, f.read())
print("%d files" % len(names))
PY

echo "==> ${OUT}"
python3 -c "import hashlib,sys;print(' sha256', hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "${OUT}"
ls -l "${OUT}" | awk '{print " ", $5, "bytes"}'
