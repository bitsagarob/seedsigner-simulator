#!/usr/bin/env bash
#
# Deploy the ordinary simulator page.
#
#   ./build/deploy.sh [SITE_DIR]
#
# This page's claim is that you can rebuild it yourself and get the pinned
# upstream release byte for byte, so what it must NOT carry matters as much as
# what it must. src/web/extras holds features that are not in any upstream
# release: research, served on a page of its own. Copying src/web wholesale, as
# docs/SELF-HOSTING.md used to say, would put them here too.
#
# So the exclusion lives in a script rather than in a sentence somebody has to
# read, and build/check-deploy.sh asks the served page to prove they are absent:
#
#   SIM_DEPLOY_EXTRAS=no ./build/check-deploy.sh
#
# Requires: bash, rsync.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SITE_DIR="${1:-/home/rob/apps/bitsaga/webapp/seedsigner-simulator}"

[ -d "${SITE_DIR}" ] || { echo "no such directory: ${SITE_DIR}" >&2; exit 2; }

echo "==> src/web (without extras/)"
rsync --archive \
      --exclude 'extras' \
      --exclude '__pycache__' \
      --exclude 'pyodide' \
      "${REPO_ROOT}/src/web/" "${SITE_DIR}/"

echo "==> src/shims"
rsync --archive "${REPO_ROOT}/src/shims/" "${SITE_DIR}/"

echo "==> build/out"
shopt -s nullglob
for artefact in "${REPO_ROOT}"/build/out/wallet-*.zip \
                "${REPO_ROOT}"/build/out/wallet-*.build-info.json; do
    name="$(basename -- "${artefact}")"
    case "${name}" in
        # The MuSig2 firmware belongs to the page that carries the feature.
        *doomsigner-musig*|wallet-embit.zip) continue ;;
    esac
    cp -- "${artefact}" "${SITE_DIR}/${name}"
    echo "  ${name}"
done

# Nothing from extras/ may survive here from an earlier copy.
if [ -d "${SITE_DIR}/extras" ]; then
    echo "==> removing an extras/ left by an earlier deploy"
    rm -rf -- "${SITE_DIR}/extras"
fi

echo "==> ${SITE_DIR}"
