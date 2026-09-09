#!/usr/bin/env python3
"""Does Bitcoin Core v31.1 actually coordinate a MuSig2 signing session?

Not "does it parse musig() descriptors" -- getdescriptorinfo already answers
that. The question that decides the architecture is whether the wallet plays
both rounds: publishes a public nonce for its own participant key in round one,
then turns everyone's nonces into a partial signature in round two, with the
rounds separated by a PSBT that leaves the process.

Run against the regtest node already up at ~/.cache/tmp/musig-regtest.
"""

import json
import subprocess
import sys

DATADIR = "$TMP/musig-regtest"
CONF = f"{DATADIR}/bitcoin.conf"


def cli(*args, wallet=None):
    cmd = ["bitcoin-cli", f"-datadir={DATADIR}", f"-conf={CONF}"]
    if wallet:
        cmd.append(f"-rpcwallet={wallet}")
    cmd += [str(a) for a in args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(" ".join(cmd[-3:]) + " -> " + out.stderr.strip())
    text = out.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def fresh_wallet(name, **kw):
    try:
        cli("unloadwallet", name)
    except RuntimeError:
        pass
    args = ["-named", "createwallet", f"wallet_name={name}"]
    for k, v in kw.items():
        args.append(f"{k}={json.dumps(v)}")
    try:
        cli(*args)
    except RuntimeError as e:
        if "already exists" not in str(e):
            raise
        cli("loadwallet", name)


def master_keys(wallet):
    """The wallet's master tprv (from its own descriptor) and master tpub."""
    tprv = None
    for d in cli("listdescriptors", "true", wallet=wallet)["descriptors"]:
        if "tprv" in d["desc"]:
            tprv = d["desc"].split("tprv", 1)[1].split("/")[0]
            tprv = "tprv" + tprv
            break
    tpub = cli("gethdkeys", '{"active_only":true}', wallet=wallet)[0]["xpub"]
    return tprv, tpub


def with_sum(desc):
    return f"{desc}#{cli('getdescriptorinfo', desc)['checksum']}"


def show(psbt, label):
    inp = cli("decodepsbt", psbt)["inputs"][0]
    print(f"\n--- {label} ---")
    for k in ("musig2_participant_pubkeys", "musig2_pubnonces",
              "musig2_partial_sigs"):
        v = inp.get(k)
        if v is None:
            print(f"  {k:32} absent")
        else:
            print(f"  {k:32} {len(v)} entry/entries")
    return inp


def main():
    fresh_wallet("mA")
    fresh_wallet("mB")
    fresh_wallet("mW", blank=True, disable_private_keys=True)
    fresh_wallet("miner")

    prvA, pubA = master_keys("mA")
    prvB, pubB = master_keys("mB")
    print(f"A master {pubA[:14]}...  B master {pubB[:14]}...")

    watch = with_sum(f"tr(musig({pubA}/0/<0;1>/*,{pubB}/0/<0;1>/*))")
    sideA = with_sum(f"tr(musig({prvA}/0/<0;1>/*,{pubB}/0/<0;1>/*))")
    sideB = with_sum(f"tr(musig({pubA}/0/<0;1>/*,{prvB}/0/<0;1>/*))")
    print(f"musig() solvable: {cli('getdescriptorinfo', watch[:-9])['issolvable']}")

    def imp(wallet, desc, active):
        r = cli("importdescriptors", json.dumps(
            [{"desc": desc, "active": active, "timestamp": "now",
              "range": [0, 999]}]), wallet=wallet)
        if not r[0]["success"]:
            raise SystemExit(f"{wallet} import failed: {json.dumps(r, indent=2)}")

    imp("mW", watch, True)
    imp("mA", sideA, False)
    imp("mB", sideB, False)
    print("all three wallets hold the same aggregate")

    addr = cli("getnewaddress", "", "bech32m", wallet="mW")
    mineto = cli("getnewaddress", wallet="miner")
    cli("generatetoaddress", 101, mineto, wallet="miner")
    cli("sendtoaddress", addr, 1.0, wallet="miner")
    cli("generatetoaddress", 1, mineto, wallet="miner")
    print(f"funded {addr}")

    dest = cli("getnewaddress", wallet="miner")
    psbt = cli("walletcreatefundedpsbt", "[]", json.dumps([{dest: 0.5}]),
               0, json.dumps({"fee_rate": 5}), wallet="mW")["psbt"]
    show(psbt, "round 0: the PSBT the watch-only wallet built")

    steps = [("mA", "A"), ("mB", "B"), ("mA", "A again"), ("mB", "B again")]
    for wallet, label in steps:
        r = cli("walletprocesspsbt", psbt, "true", "DEFAULT", "true",
                wallet=wallet)
        psbt = r["psbt"]
        show(psbt, f"after {label} processes it (complete={r['complete']})")

    fin = cli("finalizepsbt", psbt)
    print(f"\nfinalizepsbt complete={fin['complete']}")
    if fin.get("complete"):
        print(f"BROADCAST {cli('sendrawtransaction', fin['hex'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
