#!/usr/bin/env python3
"""The spend flow, without a device.

spend_start and spend_returned hold the coordinator's decisions, so they are
worth testing where a browser is not needed: pooled nonces are issued once,
spares are harvested, and a nonce the signer did not publish is refused.

    python3 test/test_coordinator_flow.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/home/rob/apps/_scratch/embit-musig/src")
sys.path.insert(0, str(HERE.parent / "src" / "web"))

import coordinator
from embit.psbt import PSBT

DESCRIPTOR = open("/tmp/musig-fresh.desc").read().strip().split("#")[0]
UTXO = {"txid": "8f3a1c2e5d4b6a79808182838485868788898a8b8c8d8e8f90919293949596ab",
        "vout": 0, "value": 50000000}
DESTINATION = "5120" + "11" * 32
AMOUNT = 49990000

checks = []


def check(what, ok, detail=""):
    checks.append(ok)
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what,
                         "" if ok else "   " + str(detail)[:70]))


def stock(psbt_string, participant, count):
    """Spare nonces of the shape a device leaves behind."""
    psbt = PSBT.from_string(psbt_string)
    for i in range(count):
        blob = bytes([0xA0 + i]) * coordinator.SIZE_PUBNONCE + bytes(coordinator.SIZE_SEALED)
        psbt.inputs[0].unknown[coordinator._pooled_key(bytes.fromhex(participant), i)] = blob
    return psbt.to_string()


def main():
    base = {"descriptor": DESCRIPTOR, "branch": 0, "index": 0, "utxo": UTXO,
            "destination": DESTINATION, "amount": AMOUNT, "pool": {}}

    empty = coordinator.spend_start(base)
    check("an empty pool issues nothing", empty["issued"] == [])
    check("and says who it is still waiting for", len(empty["waiting_for"]) == 2)

    who = next(a for a in coordinator.musig_aggregates(DESCRIPTOR, 0, 0)
               if a["leaf"] is None)["participants"][0]
    back = coordinator.spend_returned(empty, stock(empty["psbt"], who, 4))
    check("spares come back into the pool", len(back["pool"].get(who, [])) == 4,
          back["pool"].keys())
    check("and the trip is counted", back["trips"] == 1)

    second = coordinator.spend_start(dict(base, pool=back["pool"]))
    check("the next spend issues one", len(second["issued"]) == 1)
    check("and takes it out of the pool", len(second["pool"][who]) == 3)
    check("and waits for the other signer only", second["waiting_for"] != [])

    scope = PSBT.from_string(second["psbt"]).inputs[0]
    wanted = coordinator._nonce_key(bytes.fromhex(who),
                                    bytes.fromhex(second["aggregate"]))
    check("the nonce is filed under the signing key", wanted in scope.unknown)

    try:
        coordinator.spend_returned(second, empty["psbt"])
        check("a signer that published nothing is refused", False)
    except ValueError:
        check("a signer that published nothing is refused", True)

    good = coordinator.spend_returned(second, second["psbt"])
    check("a signer that published it is accepted", good["verified"] == [who])
    check("and the entry is not issued twice", good["issued"] == [])

    print("\n%d/%d" % (sum(checks), len(checks)))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
