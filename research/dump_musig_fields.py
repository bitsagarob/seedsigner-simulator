#!/usr/bin/env python3
"""What a real MuSig2 PSBT field actually looks like on the wire.

BIP-373 says PSBT_IN_MUSIG2_PUB_NONCE is 0x1b with a participant key, an
aggregate key and an optional leaf hash in the keydata. Reading that off a PSBT
Core produced is cheaper than being wrong about it later, because a coordinator
that writes the wrong keydata produces a field the signer silently ignores.
"""

import base64
import json
import subprocess

DATADIR = "$TMP/musig-regtest"
CONF = f"{DATADIR}/bitcoin.conf"


def cli(*args, wallet=None):
    cmd = ["bitcoin-cli", f"-datadir={DATADIR}", f"-conf={CONF}"]
    if wallet:
        cmd.append(f"-rpcwallet={wallet}")
    cmd += [str(a) for a in args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    try:
        return json.loads(out.stdout.strip())
    except json.JSONDecodeError:
        return out.stdout.strip()


def read_compact_size(buf, pos):
    first = buf[pos]
    if first < 0xFD:
        return first, pos + 1
    if first == 0xFD:
        return int.from_bytes(buf[pos + 1:pos + 3], "little"), pos + 3
    if first == 0xFE:
        return int.from_bytes(buf[pos + 1:pos + 5], "little"), pos + 5
    return int.from_bytes(buf[pos + 1:pos + 9], "little"), pos + 9


def maps(psbt_b64):
    buf = base64.b64decode(psbt_b64)
    assert buf[:5] == b"psbt\xff"
    pos, out = 5, []
    while pos < len(buf):
        m = []
        while True:
            klen, pos = read_compact_size(buf, pos)
            if klen == 0:
                break
            key = buf[pos:pos + klen]
            pos += klen
            vlen, pos = read_compact_size(buf, pos)
            m.append((key, buf[pos:pos + vlen]))
            pos += vlen
        out.append(m)
    return out


mineto = cli("getnewaddress", wallet="miner")
cli("sendtoaddress", cli("getnewaddress", "", "bech32m", wallet="mW"), 1.0,
    wallet="miner")
cli("generatetoaddress", 1, mineto, wallet="miner")
dest = cli("getnewaddress", wallet="miner")
psbt = cli("walletcreatefundedpsbt", "[]", json.dumps([{dest: 0.5}]), 0,
           json.dumps({"fee_rate": 5}), wallet="mW")["psbt"]
psbt = cli("walletprocesspsbt", psbt, "true", "DEFAULT", "true",
           wallet="mA")["psbt"]

TYPES = {0x1a: "PSBT_IN_MUSIG2_PARTICIPANT_PUBKEYS",
         0x1b: "PSBT_IN_MUSIG2_PUB_NONCE",
         0x1c: "PSBT_IN_MUSIG2_PARTIAL_SIG"}

for key, value in maps(psbt)[1]:
    if key[0] not in TYPES:
        continue
    data = key[1:]
    print(f"{TYPES[key[0]]}  (type byte 0x{key[0]:02x})")
    print(f"  keydata   {len(data)} bytes")
    if key[0] == 0x1b:
        print(f"    participant  {data[:33].hex()}")
        print(f"    aggregate    {data[33:66].hex()}")
        print(f"    leaf hash    {data[66:].hex() or '(none: key path)'}")
    else:
        print(f"    {data.hex()}")
    print(f"  value     {len(value)} bytes")
    print()
