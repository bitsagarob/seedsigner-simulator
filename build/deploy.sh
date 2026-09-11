#!/usr/bin/env bash
#
# Deploy the simulator.
#
#   ./build/deploy.sh [SITE_DIR]
#
# One page, one deploy. It used to be two: a stock page here and a MuSig2 page
# under musig/, kept apart by a hand-picked file list that is how the MuSig2
# page came to run firmware months older than the page around it. There is no
# split now. DoomSigner carries MuSig2, and the whole tree ships, extras/ and
# all.
#
# The optional features in extras/ are loaded per firmware by wallet.html: a
# visitor on stock or smartcard fetches none of them. The stock firmware zip is
# still verified byte-for-byte against UPSTREAM by build/check-deploy.sh, so its
# reproducibility does not depend on what JS sits beside it.
#
# The big fetched assets (Pyodide, the DOOM build, the fonts) are placed by
# build/fetch-assets.sh and the game build, not by this script, and are left
# untouched: no --delete, so they survive a code deploy.
#
# Requires: bash, rsync.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SITE_DIR="${1:-$HOME/apps/bitsaga/webapp/seedsigner-simulator}"

[ -d "${SITE_DIR}" ] || { echo "no such directory: ${SITE_DIR}" >&2; exit 2; }

echo "==> src/web (including extras/)"
rsync --archive \
      --exclude '__pycache__' \
      --exclude 'pyodide*' \
      "${REPO_ROOT}/src/web/" "${SITE_DIR}/"

echo "==> src/shims"
rsync --archive "${REPO_ROOT}/src/shims/" "${SITE_DIR}/"

echo "==> build/out (every firmware this page can run)"
shopt -s nullglob
for artefact in "${REPO_ROOT}"/build/out/wallet-*.zip \
                "${REPO_ROOT}"/build/out/wallet-*.build-info.json; do
    name="$(basename -- "${artefact}")"
    cp -- "${artefact}" "${SITE_DIR}/${name}"
    echo "  ${name}"
done

# The separate MuSig2 page is retired. Remove it if an earlier deploy left it,
# so the only thing under musig/ is whatever the nginx redirect serves.
if [ -d "${SITE_DIR}/musig" ]; then
    echo "==> removing the retired musig/ page"
    rm -rf -- "${SITE_DIR}/musig"
fi

echo "==> ${SITE_DIR}"
