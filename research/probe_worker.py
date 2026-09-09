#!/usr/bin/env python3
"""Does the coordinator worker boot, and does it answer the same as the JS?

Boots Pyodide with embit alone in a page worker, asks it for an address, and
compares that with the address the parity test already pinned.
"""
import json
import sys
import time

sys.path.insert(0, "$SIGNET_TEST_DIR")

URL = "http://127.0.0.1:8792/wallet.html?firmware=doomsigner-musig&debug=1&wallet=1"
DESCRIPTOR = None  # filled from the parity fixture below
EXPECTED = "tb1qmv9kucx4tjtyfwddc3698p2flxqvts89n8kllr0hvdv7qs4z476s70nuf5"

DRIVE = """
async ([descriptor]) => {
  // Through the adapter the page will use, not the worker directly.
  const A = self.EmbitCoordinator;
  const t0 = performance.now();
  const hashes = await A.ready();
  const t1 = performance.now();
  const wallet = { descriptor, keys: [], threshold: 2 };
  const derived = await A.deriveAddress(wallet, 0, 0);
  return { sha: hashes.embit, code: hashes.coordinator,
           address: derived.address,
           script: A.hex(derived.scriptPubkey),
           bootMs: Math.round(t1 - t0),
           callMs: Math.round(performance.now() - t1) };
}
"""

UNUSED = """
async ([descriptor]) => {
  const started = performance.now();
  const worker = new Worker("coordinator-worker.js");
  const ask = (message) => new Promise((resolve, reject) => {
    const id = Math.random().toString(36).slice(2);
    const onmessage = (e) => {
      if (e.data.id !== id) return;
      worker.removeEventListener("message", onmessage);
      e.data.ok ? resolve(e.data.value) : reject(new Error(e.data.value));
    };
    worker.addEventListener("message", onmessage);
    worker.postMessage(Object.assign({ id }, message));
  });
  const sha = await ask({ type: "boot", indexURL: "pyodide-e24b45d3/",
                          zipURL: "wallet-embit.zip",
                          codeURL: "coordinator.py" });
  const booted = performance.now();
  const first = await ask({ fn: "address", args: [descriptor, 0, 0] });
  return { sha: sha.embit, code: sha.coordinator,
           address: first.address, script: first.script_pubkey,
           bootMs: Math.round(booted - started),
           callMs: Math.round(performance.now() - booted) };
}
"""


def main():
    from simdrive import Sim

    descriptor = open("/tmp/parity-descriptor.txt").read().strip()
    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        sim.open(timeout=300)
        out = sim.page.evaluate(DRIVE, [descriptor])
        print("embit zip sha256 :", out["sha"][:32], "...")
        print("coordinator.py   :", out["code"][:32], "...")
        print("boot             :", out["bootMs"], "ms")
        print("one address      :", out["callMs"], "ms")
        print("address          :", out["address"])
        print("expected         :", EXPECTED)
        ok = out["address"] == EXPECTED
        print("\n" + ("the worker agrees with the JavaScript"
                      if ok else "MISMATCH"))
        return 0 if ok else 1
    finally:
        sim.stop()


if __name__ == "__main__":
    sys.exit(main())
