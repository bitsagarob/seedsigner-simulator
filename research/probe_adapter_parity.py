#!/usr/bin/env python3
"""Does the adapter answer the same as the JavaScript it stands in for?

Both implementations are on the page. This asks each the questions the tutorial
asks, with the same inputs, and compares every answer.
"""
import json
import sys

sys.path.insert(0, "$SIGNET_TEST_DIR")

URL = "http://127.0.0.1:8792/wallet.html?firmware=doomsigner-musig&debug=1&wallet=1"

DRIVE = """
async ([keys, utxo, destination, amount]) => {
  const JS = self.SignetCoordinator;
  const EM = self.EmbitCoordinator;
  await EM.ready();

  const both = async (fn) => [await fn(JS), await fn(EM)];
  const out = {};

  const [wj, we] = await both((C) => C.buildWallet(keys));
  out.descriptor = [wj.descriptor, we.descriptor];

  const [aj, ae] = await both(async (C) => {
    const w = await C.buildWallet(keys);
    return C.deriveAddress(w, 0, 0);
  });
  out.address = [aj.address, ae.address];
  out.script = [JS.hex(aj.witnessScript), JS.hex(ae.witnessScript)];
  out.spk = [JS.hex(aj.scriptPubkey), JS.hex(ae.scriptPubkey)];

  const dest = JS.unhex(destination);
  const [pj, pe] = await both(async (C) => {
    const w = await C.buildWallet(keys);
    const src = await C.deriveAddress(w, 0, 0);
    return C.buildPsbt(utxo, src, dest, amount);
  });
  out.psbt = [JS.toBase64(pj), JS.toBase64(pe)];

  const sigs = {};
  aj.cosigners.slice(0, 2).forEach((leaf, i) => {
    sigs[JS.hex(leaf.pubkey)] = JS.unhex(i ? "3044" + "33".repeat(34) + "01"
                                           : "3044" + "22".repeat(34) + "01");
  });
  const [fj, fe] = await both(async (C) => {
    const w = await C.buildWallet(keys);
    const src = await C.deriveAddress(w, 0, 0);
    return C.finalise(utxo, src, dest, amount, sigs);
  });
  out.tx = [fj.hex, fe.hex];
  out.txid = [fj.txid, fe.txid];
  return out;
}
"""


def main():
    from simdrive import Sim

    fixture = json.load(open("/tmp/adapter-fixture.json"))
    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        sim.open(timeout=300)
        out = sim.page.evaluate(DRIVE, [fixture["keys"], fixture["utxo"],
                                        fixture["destination"], fixture["amount"]])
        bad = 0
        for name, (js, embit) in out.items():
            same = js == embit
            bad += not same
            print("  %s %-11s %s" % ("ok  " if same else "FAIL", name, str(embit)[:44]))
            if not same:
                print("        javascript %s" % str(js)[:44])
        print("\n%s" % ("the adapter answers exactly as the JavaScript does"
                        if not bad else "%d disagreements" % bad))
        return 1 if bad else 0
    finally:
        sim.stop()


if __name__ == "__main__":
    sys.exit(main())
