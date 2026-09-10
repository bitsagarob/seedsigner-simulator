#!/usr/bin/env python3
"""Do the coordinator and the firmware agree on the pooled-nonce field?

Both sides build the same proprietary key, independently. If they ever drift,
the coordinator writes a nonce the device does not look for and the device
mints a fresh one instead, which costs the visit this feature exists to remove
and reports nothing. So the two are compared here rather than trusted.

The firmware side comes out of the built wallet zip, so this checks what ships.

    python3 test/test_pool_conformance.py
"""
import ast
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZIP = HERE.parent / "build" / "out" / "wallet-doomsigner.zip"

sys.path.insert(0, "/home/rob/apps/_scratch/embit-musig/src")
sys.path.insert(0, str(HERE.parent / "src" / "web" / "extras"))

import coordinator


def firmware_key(index=0):
    """The device's own pooled-nonce key, run out of the shipped zip."""
    if not ZIP.exists():
        raise SystemExit("no wallet zip: run build/build-wallet-zip.sh doomsigner")
    with zipfile.ZipFile(ZIP) as z:
        for name in ("seedsigner/helpers/musig2_psbt.py",
                     "seedsigner/helpers/musig2_card.py"):
            if name not in z.namelist():
                raise SystemExit("%s carries no %s" % (ZIP.name, name))
        source = z.read("seedsigner/helpers/musig2_psbt.py").decode()

    # Just the field builder and what it needs. Importing the module would drag
    # in embit and the whole signing path.
    wanted = {"PSBT_IN_PROPRIETARY", "PROPRIETARY_IDENTIFIER", "compact_size",
              "proprietary_key"}
    tree = ast.parse(source)
    keep = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in wanted)
            or (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) in wanted for t in node.targets))]
    scope = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "musig2_psbt", "exec"), scope)
    return scope["proprietary_key"](0x01)


def main():
    theirs = firmware_key()
    ours = coordinator._pooled_key(b"", 0)[:-2]
    same = theirs == ours
    print("  firmware    %s" % theirs.hex())
    print("  coordinator %s" % ours.hex())
    print("\n%s" % ("the two agree on the pooled-nonce field"
                    if same else "THEY DISAGREE"))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
