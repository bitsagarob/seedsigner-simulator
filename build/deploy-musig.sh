#!/usr/bin/env bash
#
# Deploy the MuSig2 page.
#
#   ./build/deploy-musig.sh [SITE_DIR]
#
# Copies the whole of src/web plus the build output this page needs, rather than
# a list of filenames somebody has to remember to extend.
#
# It used to be a symlink farm with a hand-written list of overrides, and the
# list is how the page went stale: wallet-worker.js and the firmware zip were
# never on it, so the only copies that ever reached the site were put there by
# hand, and the live page ran firmware months older than the page around it. A
# list of names cannot be checked by looking at the directory; a copy of a tree
# can.
#
# The Pyodide runtime stays a symlink to the one beside it. It is 26 MB of
# somebody else's release, identical for both pages, and fetch-assets.sh already
# hash-checks it where it is fetched.
#
# Requires: bash, rsync.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SITE_DIR="${1:-/home/rob/apps/bitsaga/webapp/seedsigner-simulator/musig}"

[ -d "${SITE_DIR}" ] || { echo "no such directory: ${SITE_DIR}" >&2; exit 2; }

# What this page serves out of build/out. Named because they are build output,
# not source: everything else comes from the tree wholesale.
ARTEFACTS="
wallet-embit.zip
wallet-doomsigner-musig.zip
wallet-doomsigner-musig.build-info.json
"

for name in ${ARTEFACTS}; do
    [ -f "${REPO_ROOT}/build/out/${name}" ] || {
        echo "build/out/${name} is missing; run the build first" >&2
        exit 2
    }
done

echo "==> src/web"
# --delete so a file removed from the tree stops being served. The excludes are
# the things that do not come from src/web: the runtime symlink, the build
# output copied below, and the screenshots this page keeps for its own README.
rsync --archive --delete --no-links \
      --exclude 'pyodide*' \
      --exclude '__pycache__' \
      --exclude 'wallet-*.zip' \
      --exclude 'wallet-*.build-info.json' \
      --exclude 'shots' \
      "${REPO_ROOT}/src/web/" "${SITE_DIR}/"

echo "==> src/shims"
rsync --archive "${REPO_ROOT}/src/shims/" "${SITE_DIR}/"

echo "==> build/out"
for name in ${ARTEFACTS}; do
    cp -- "${REPO_ROOT}/build/out/${name}" "${SITE_DIR}/${name}"
    echo "  ${name}"
done

# Everything this page shares with the one beside it, linked rather than copied:
# the Pyodide runtime, the DOOM build, the fonts, the content-hashed icons and
# the other firmwares' zips. None of those come from src/web, and all of them
# are large, identical for both pages, and built by something else.
#
# Derived rather than listed, for the same reason the files above are: a name
# somebody has to remember to add is a name somebody forgets. Anything the
# parent has and this page does not, is linked.
PARENT="$(dirname -- "${SITE_DIR}")"
MINE="$(basename -- "${SITE_DIR}")"
linked=0
for shared in "${PARENT}"/*; do
    name="$(basename -- "${shared}")"
    [ "${name}" = "${MINE}" ] && continue
    [ -e "${SITE_DIR}/${name}" ] && continue
    ln -s -- "../${name}" "${SITE_DIR}/${name}"
    linked=$((linked + 1))
done
echo "==> ${linked} shared file(s) linked from ${PARENT}"

echo "==> ${SITE_DIR}"
