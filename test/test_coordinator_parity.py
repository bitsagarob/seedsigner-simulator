#!/usr/bin/env python3
"""Does embit produce what the page's own JavaScript produces?

The coordinator's chain half is being moved from hand-written JavaScript to
embit, which the device already runs. That is only safe if the two agree byte
for byte first, so this asks them the same questions and compares the answers.

    python3 test/test_coordinator_parity.py
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/home/rob/.cache/tmp/ss-sp-spend-nno_t15g")
sys.path.insert(0, "/home/rob/apps/_scratch/embit-musig/src")

from embit import bip32, bip39
from embit.descriptor import Descriptor
from embit.networks import NETWORKS

NET = NETWORKS["test"]
MNEMONICS = [
    "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
    "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong",
    "legal winner thank year wave sausage worth useful legal winner thank yellow",
]
ACCOUNT = "m/48h/1h/0h/2h"

JS = r"""
const C = require(process.argv[1] + "/src/web/signet-coordinator.js").SignetCoordinator;
const keys = JSON.parse(process.argv[2]);
C.buildWallet(keys).then(function (wallet) {
  return Promise.all([0, 1, 2].map(function (i) {
    return C.deriveAddress(wallet, 0, i);
  })).then(function (addrs) {
    process.stdout.write(JSON.stringify({
      descriptor: wallet.descriptor,
      addresses: addrs.map(function (a) { return a.address; }),
      scripts: addrs.map(function (a) { return C.hex(a.witnessScript); }),
    }));
  });
}).catch(function (e) { process.stdout.write(JSON.stringify({ error: String(e) })); });
"""


def exported_keys():
    """What the device hands over: [fingerprint/path]xpub, one per seed."""
    out = []
    for words in MNEMONICS:
        root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(words))
        account = root.derive(ACCOUNT).to_public()
        origin = "[%s/%s]" % (root.my_fingerprint.hex(), ACCOUNT[2:].replace("h", "h"))
        out.append(origin + account.to_string(version=NET["xpub"]))
    return out


def main():
    keys = exported_keys()
    node = subprocess.run(["node", "-e", JS, "--", str(HERE.parent), json.dumps(keys)],
                          capture_output=True, text=True)
    if node.returncode != 0:
        print(node.stderr.strip()[:400])
        return 1
    js = json.loads(node.stdout)
    if "error" in js:
        print("the JavaScript refused:", js["error"])
        return 1

    theirs = Descriptor.from_string(js["descriptor"])
    ours = {
        "addresses": [theirs.derive(i, branch_index=0).address(NET) for i in range(3)],
        "scripts": [theirs.derive(i, branch_index=0).witness_script().data.hex()
                    for i in range(3)],
    }

    failures = 0
    for field in ("addresses", "scripts"):
        for i, (a, b) in enumerate(zip(js[field], ours[field])):
            same = a == b
            failures += not same
            print("  %s %s[%d] %s" % ("ok  " if same else "FAIL", field, i, b[:48]))
            if not same:
                print("        javascript %s" % a[:48])

    print("\ndescriptor both read:", js["descriptor"][:60], "...")
    print("%s" % ("embit and the page agree on every one" if not failures
                  else "%d disagreements" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
