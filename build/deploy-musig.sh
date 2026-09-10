#!/usr/bin/env bash
#
# Copy the MuSig2 page into the site's musig/ override directory.
#
#   ./build/deploy-musig.sh [SITE_DIR]
#
# seedsigner-simulator/musig/ is a symlink farm over the simulator beside it:
# every file it does not override is a link to the one the ordinary page uses.
# So a deploy here replaces exactly the files that differ, and a file that stops
# differing should go back to being a link rather than a stale copy.
#
# Requires: bash, rsync.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SITE_DIR="${1:-/home/rob/apps/bitsaga/webapp/seedsigner-simulator/musig}"

[ -d "${SITE_DIR}" ] || { echo "no such directory: ${SITE_DIR}" >&2; exit 2; }
[ -f "${REPO_ROOT}/build/out/wallet-embit.zip" ] || {
    echo "build/out/wallet-embit.zip is missing; run build/build-embit-zip.sh" >&2
    exit 2
}

# The page's own files. Everything else in src/web is identical to the ordinary
# simulator's and stays a symlink.
FILES="
coordinator.py
coordinator-worker.js
embit-coordinator.js
signet-coordinator.js
sw.js
wallet-coordinator.js
wallet-tutorial.js
wallet-worker.js
wallet.html
"

for name in ${FILES}; do
    src="${REPO_ROOT}/src/web/${name}"
    [ -f "${src}" ] || { echo "missing in the repository: ${name}" >&2; exit 2; }
    # Replace the symlink rather than writing through it: writing through one
    # edits the ordinary simulator's copy, which is not what a deploy here means.
    rm -f -- "${SITE_DIR}/${name}"
    cp -- "${src}" "${SITE_DIR}/${name}"
    echo "  ${name}"
done

cp -- "${REPO_ROOT}/build/out/wallet-embit.zip" "${SITE_DIR}/wallet-embit.zip"
echo "  wallet-embit.zip"

# The firmware the MuSig2 page actually runs. It was not on this list, so the
# only copies that ever reached the site were put there by hand, and the live
# page ran firmware months older than the page around it. That is the whole
# reason a deploy exists: nobody should have to remember a file.
[ -f "${REPO_ROOT}/build/out/wallet-doomsigner-musig.zip" ] || {
    echo "build/out/wallet-doomsigner-musig.zip is missing;" \
         "run build/build-wallet-zip.sh doomsigner-musig" >&2
    exit 2
}
cp -- "${REPO_ROOT}/build/out/wallet-doomsigner-musig.zip" \
      "${SITE_DIR}/wallet-doomsigner-musig.zip"
echo "  wallet-doomsigner-musig.zip"

echo "==> ${SITE_DIR}"
