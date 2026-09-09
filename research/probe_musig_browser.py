#!/usr/bin/env python3
"""Does the MuSig2 flow work inside the browser?

Every MuSig2 spend so far was driven by Python on the box. This drives the same
calls through the page's worker instead, and compares the answers with the ones
Python gives for the same inputs. If they differ, the browser path is not the
path that was tested.
"""
import json
import subprocess
import sys

sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")

URL = "http://127.0.0.1:8792/wallet.html?firmware=doomsigner-musig&debug=1&wallet=1"

DRIVE = """
async ([descriptor, utxo, destination, amount]) => {
  const A = self.EmbitCoordinator;
  await A.ready();
  const address = await A.musigAddress(descriptor, 0, 0);
  const started = await A.spendStart({
    descriptor, branch: 0, index: 0, utxo, destination, amount, pool: {},
  });
  const back = await A.spendReturned(started, started.psbt);
  return {
    address: address.address,
    internal: address.internal_key,
    merkle: address.merkle_root,
    psbt: started.psbt,
    aggregate: started.aggregate,
    waiting: started.waiting_for.length,
    trips: back.trips,
  };
}
"""


def main():
    from simdrive import Sim

    fixture = json.load(open("/tmp/musig-browser-fixture.json"))
    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        sim.open(timeout=300)
        got = sim.page.evaluate(DRIVE, [fixture["descriptor"], fixture["utxo"],
                                        fixture["destination"], fixture["amount"]])
    finally:
        sim.stop()

    want = json.loads(subprocess.run(
        ["/home/rob/apps/_scratch/musig2-venv/bin/python", "-c", PYTHON_SIDE],
        capture_output=True, text=True, check=True).stdout)

    bad = 0
    for name in ("address", "internal", "merkle", "psbt", "aggregate", "waiting",
                 "trips"):
        same = got[name] == want[name]
        bad += not same
        print("  %s %-10s %s" % ("ok  " if same else "FAIL", name,
                                 str(got[name])[:46]))
        if not same:
            print("        python %s" % str(want[name])[:46])
    print("\n%s" % ("the browser answers exactly as Python does" if not bad
                    else "%d disagreements" % bad))
    return 1 if bad else 0


PYTHON_SIDE = """
import json, sys
sys.path.insert(0, '/home/rob/apps/_scratch/embit-musig/src')
sys.path.insert(0, '/home/rob/apps/_scratch/musig2-sim/src/web')
import coordinator
f = json.load(open('/tmp/musig-browser-fixture.json'))
a = coordinator.musig_address(f['descriptor'], 0, 0)
s = coordinator.spend_start({'descriptor': f['descriptor'], 'branch': 0, 'index': 0,
                             'utxo': f['utxo'], 'destination': f['destination'],
                             'amount': f['amount'], 'pool': {}})
b = coordinator.spend_returned(s, s['psbt'])
print(json.dumps({'address': a['address'], 'internal': a['internal_key'],
                  'merkle': a['merkle_root'], 'psbt': s['psbt'],
                  'aggregate': s['aggregate'], 'waiting': len(s['waiting_for']),
                  'trips': b['trips']}))
"""


if __name__ == "__main__":
    sys.exit(main())
