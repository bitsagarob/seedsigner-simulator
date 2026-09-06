#!/usr/bin/env python3
"""Does Bitcoin Core read back what the nonce pool wrote?

test_nonce_pool.js checks the pool against its own parser, which would pass just
as happily if both halves were wrong in the same way. This asks the other
implementation. The JavaScript dresses a PSBT, Core decodes it, and the public
nonce has to come back as a MuSig2 field bound to the right participant and the
right aggregate -- not as an unknown record Core kept but never understood.

    python3 test/test_nonce_pool_core.py
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATADIR = "/home/rob/.cache/tmp/musig-regtest"
CONF = f"{DATADIR}/bitcoin.conf"


def cli(*args):
    cmd = ["bitcoin-cli", f"-datadir={DATADIR}", f"-conf={CONF}"]
    cmd += [str(a) for a in args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    try:
        return json.loads(out.stdout.strip())
    except json.JSONDecodeError:
        return out.stdout.strip()


DRESS = r"""
const path = require("path");
const fs = require("fs");
const pool = require(path.join(process.argv[2], "src", "web", "nonce-pool.js"))
  .NoncePool;
const fixtures = JSON.parse(fs.readFileSync(
  path.join(process.argv[2], "test", "fixtures", "nonce-pool.json"), "utf8"));

function unhex(text) {
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(text.substr(i * 2, 2), 16);
  return out;
}

const p = pool.create();
p.harvest("w", fixtures.stocked);
const out = p.dress("w", fixtures.bare, [{
  participant: unhex(fixtures.participant),
  aggregate: unhex(fixtures.aggregate),
}]);
process.stdout.write(JSON.stringify(out));
"""


def main():
    repo = str(HERE.parent)
    fixtures = json.loads((HERE / "fixtures" / "nonce-pool.json").read_text())

    node = subprocess.run(["node", "-e", DRESS, "--", repo],
                          capture_output=True, text=True)
    if node.returncode != 0:
        print(node.stderr)
        return 1
    dressed = json.loads(node.stdout)

    print(f"the pool issued  {dressed['used'][0]['id'][:32]}...")

    decoded = cli("decodepsbt", dressed["psbt"])["inputs"][0]

    nonces = decoded.get("musig2_pubnonces")
    if not nonces:
        print("FAIL: Core saw no MuSig2 public nonce in the dressed PSBT")
        print(f"      input keys were {sorted(decoded)}")
        return 1

    entry = nonces[0]
    print("Core parsed it as a MuSig2 public nonce for")
    print(f"  participant  {entry['participant_pubkey']}")
    print(f"  aggregate    {entry['aggregate_pubkey']}")
    print(f"  pubnonce     {entry['pubnonce'][:32]}...")

    failures = []
    if entry["participant_pubkey"] != fixtures["participant"]:
        failures.append("the participant key does not match")
    if entry["aggregate_pubkey"] != fixtures["aggregate"]:
        failures.append("the aggregate key does not match")
    if entry["pubnonce"] != dressed["used"][0]["id"]:
        failures.append("the nonce Core read is not the one the pool issued")

    # The sealed half must survive as a proprietary record, since that is what
    # the device needs to open the nonce again in round two.
    sealed = [record for record in decoded.get("proprietary", [])
              if bytes.fromhex(record["identifier"]) == b"DOOMSIGNER"
              and record["subtype"] == 1]
    if len(sealed) != 1:
        failures.append(f"expected one sealed record, Core reported {len(sealed)}")
    elif len(sealed[0]["value"]) != (66 + 144) * 2:
        failures.append("the sealed record is the wrong length")
    else:
        print(f"  sealed half  carried through as a DOOMSIGNER "
              f"proprietary record, {len(sealed[0]['value']) // 2} bytes")

    if failures:
        for line in failures:
            print(f"FAIL: {line}")
        return 1

    print("\nCore agrees with the pool on every field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
