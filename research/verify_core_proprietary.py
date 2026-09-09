#!/usr/bin/env python3
"""Two questions Core has to answer before any coordinator is worth writing.

1. Does a DOOMSIGNER proprietary field survive walletprocesspsbt, combinepsbt
   and finalizepsbt? If Core strips it, the pooled nonce never reaches the
   device and the whole feature is dead.
2. Does a signer that finds every public nonce already present produce its
   partial signature in a single pass? That is the entire claim of the
   feature: one visit per signer instead of two.

Run against the regtest node at ~/.cache/tmp/musig-regtest.
"""

import base64
import json
import subprocess
import sys

DATADIR = "/home/rob/.cache/tmp/musig-regtest"
CONF = f"{DATADIR}/bitcoin.conf"

MAGIC = b"psbt\xff"
PROPRIETARY = 0xFC
IDENTIFIER = b"DOOMSIGNER"
SUBTYPE_POOLED_NONCE = 0x01


def cli(*args, wallet=None):
    cmd = ["bitcoin-cli", f"-datadir={DATADIR}", f"-conf={CONF}"]
    if wallet:
        cmd.append(f"-rpcwallet={wallet}")
    cmd += [str(a) for a in args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(" ".join(cmd[3:6]) + " -> " + out.stderr.strip())
    try:
        return json.loads(out.stdout.strip())
    except json.JSONDecodeError:
        return out.stdout.strip()


# --- the smallest PSBT reader that can splice one field into one input -------
#
# Deliberately not a PSBT library. It walks the key-value maps, leaves every
# byte it does not understand exactly where it found it, and inserts one record
# into one input map. Anything cleverer would be a second implementation of a
# format Core already implements, and would have to be trusted.

def _compact_size(n):
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + n.to_bytes(2, "little")
    if n <= 0xFFFFFFFF:
        return b"\xfe" + n.to_bytes(4, "little")
    return b"\xff" + n.to_bytes(8, "little")


def _read_compact_size(buf, pos):
    first = buf[pos]
    if first < 0xFD:
        return first, pos + 1
    if first == 0xFD:
        return int.from_bytes(buf[pos + 1:pos + 3], "little"), pos + 3
    if first == 0xFE:
        return int.from_bytes(buf[pos + 1:pos + 5], "little"), pos + 5
    return int.from_bytes(buf[pos + 1:pos + 9], "little"), pos + 9


def _map_end(buf, pos):
    """Where the key-value map starting at pos ends (past its 0x00 separator)."""
    while True:
        keylen, nxt = _read_compact_size(buf, pos)
        if keylen == 0:
            return nxt
        pos = nxt + keylen
        vallen, pos = _read_compact_size(buf, pos)
        pos += vallen


def pooled_nonce_key(pubkey, index):
    return (bytes([PROPRIETARY])
            + _compact_size(len(IDENTIFIER)) + IDENTIFIER
            + _compact_size(SUBTYPE_POOLED_NONCE)
            + pubkey + index.to_bytes(2, "big"))


def splice_into_input(psbt_b64, input_index, key, value):
    """Return the PSBT with one extra record in the given input's map."""
    buf = base64.b64decode(psbt_b64)
    assert buf[:5] == MAGIC
    pos = _map_end(buf, 5)                        # past the global map
    num_inputs = len(cli("decodepsbt", psbt_b64)["inputs"])
    for i in range(num_inputs):
        start = pos
        pos = _map_end(buf, pos)
        if i == input_index:
            record = (_compact_size(len(key)) + key
                      + _compact_size(len(value)) + value)
            # Insert just before the map's 0x00 separator.
            return base64.b64encode(
                buf[:pos - 1] + record + buf[pos - 1:]).decode()
    raise IndexError(input_index)


def find_pooled(psbt_b64):
    """Every DOOMSIGNER pooled-nonce record Core handed back, per input."""
    decoded = cli("decodepsbt", psbt_b64)
    found = []
    for i, inp in enumerate(decoded["inputs"]):
        for entry in inp.get("proprietary", []):
            if (bytes.fromhex(entry["identifier"]) == IDENTIFIER
                    and entry["subtype"] == SUBTYPE_POOLED_NONCE):
                found.append((i, entry["key"], entry["value"]))
        for k, v in (inp.get("unknown") or {}).items():
            if bytes.fromhex(k).startswith(bytes([PROPRIETARY]) + b"\x0a" + IDENTIFIER):
                found.append((i, k, v))
    return found


def main():
    # A fake pooled nonce. The bytes are meaningless on purpose: this test asks
    # whether Core moves them, not whether they are a valid nonce.
    fake_pubkey = bytes.fromhex(
        "02f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9")
    key = pooled_nonce_key(fake_pubkey, 0)
    value = bytes(66) + bytes(144)

    mineto = cli("getnewaddress", wallet="miner")
    cli("sendtoaddress", cli("getnewaddress", "", "bech32m", wallet="mW"),
        1.0, wallet="miner")
    cli("generatetoaddress", 1, mineto, wallet="miner")

    dest = cli("getnewaddress", wallet="miner")
    psbt = cli("walletcreatefundedpsbt", "[]", json.dumps([{dest: 0.5}]),
               0, json.dumps({"fee_rate": 5}), wallet="mW")["psbt"]

    spliced = splice_into_input(psbt, 0, key, value)
    print("QUESTION 1 -- does the field survive Core?")
    print(f"  spliced in:              {len(find_pooled(spliced))} record(s)")

    a1 = cli("walletprocesspsbt", spliced, "true", "DEFAULT", "true",
             wallet="mA")["psbt"]
    print(f"  after walletprocesspsbt: {len(find_pooled(a1))} record(s)")

    b1 = cli("walletprocesspsbt", a1, "true", "DEFAULT", "true",
             wallet="mB")["psbt"]
    combined = cli("combinepsbt", json.dumps([b1, a1]))
    print(f"  after combinepsbt:       {len(find_pooled(combined))} record(s)")

    # QUESTION 2 -- with every nonce already present, is one pass enough?
    print("\nQUESTION 2 -- with every nonce present, does one pass sign?")
    both = b1   # A's nonce and B's nonce, no partial signature yet
    d = cli("decodepsbt", both)["inputs"][0]
    print(f"  nonces present before:   {len(d.get('musig2_pubnonces', []))}")
    print(f"  partial sigs before:     {len(d.get('musig2_partial_sigs', []))}")

    a2 = cli("walletprocesspsbt", both, "true", "DEFAULT", "true", wallet="mA")
    da = cli("decodepsbt", a2["psbt"])["inputs"][0]
    print(f"  after ONE pass by A:     "
          f"{len(da.get('musig2_partial_sigs', []))} partial sig(s)")

    b2 = cli("walletprocesspsbt", a2["psbt"], "true", "DEFAULT", "true",
             wallet="mB")
    print(f"  after ONE pass by B:     complete={b2['complete']}")
    print(f"  pooled field still there:{len(find_pooled(b2['psbt']))} record(s)")

    fin = cli("finalizepsbt", b2["psbt"])
    print(f"\n  finalizepsbt complete={fin['complete']} "
          f"(with our field present throughout)")
    if fin.get("complete"):
        print(f"  BROADCAST {cli('sendrawtransaction', fin['hex'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
