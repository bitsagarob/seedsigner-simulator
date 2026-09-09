#!/usr/bin/env python3
"""The device's half of a MuSig2 spend, using only the firmware modules.

No hand-poked PSBT fields and no descriptor: everything the signer needs is read
out of the PSBT by seedsigner.helpers.musig2_psbt, exactly as the device will
have to. Bitcoin Core plays the coordinator and the other signer.

This is the shape the UI layer wraps, so it doubles as the spec for it.
"""
import sys, json, subprocess, hashlib

sys.path.insert(0, "/home/rob/.cache/tmp/ss-sp-spend-nno_t15g")
sys.path.insert(0, "/home/rob/apps/_scratch/musig2-app/src")

from embit import bip32, bip39, networks
from embit.psbt import PSBT

from seedsigner.helpers import musig2 as m
from seedsigner.helpers import musig2_psbt as mp

RD = "/home/rob/.cache/tmp/musig-regtest"
NET = networks.NETWORKS["regtest"]
ACCT = "m/86h/1h/0h"


def cli(*a, wallet=None):
    cmd = ["/usr/local/bin/bitcoin-cli", "-datadir=" + RD, "-conf=" + RD + "/bitcoin.conf"]
    if wallet:
        cmd.append("-rpcwallet=" + wallet)
    cmd += [str(x) for x in a]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(cmd[3:6]) + " -> " + r.stderr.strip()[:400])
    return r.stdout.strip()


def j(*a, **kw):
    return json.loads(cli(*a, **kw))


def wallet(name, nopriv):
    if name in j("listwallets"):
        return
    try:
        cli("loadwallet", name)
    except RuntimeError:
        cli("createwallet", name, nopriv, "true", "", "false", "true", "true")


def imp(name, desc):
    ck = j("getdescriptorinfo", desc)["checksum"]
    r = j("importdescriptors", json.dumps(
        [{"desc": desc + "#" + ck, "active": False, "timestamp": "now", "range": [0, 999]}]),
        wallet=name)
    if not r[0]["success"]:
        assert "current range" in r[0]["error"]["message"], r
    return desc + "#" + ck


# Published BIP-39 test vectors, so the fixture can be driven through the real
# Seed code path and so nothing here is ever mistaken for a key worth stealing.
MNEMONICS = {
    "A": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
    "B": "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong",
    "C": "legal winner thank year wave sausage worth useful legal winner thank yellow",
}
roots = {n: bip32.HDKey.from_seed(bip39.mnemonic_to_seed(MNEMONICS[n])) for n in "ABC"}
acct = {n: roots[n].derive(ACCT) for n in "ABC"}
ORIG = {n: "[%s/86h/1h/0h]" % roots[n].my_fingerprint.hex() for n in "ABC"}
pub = {n: ORIG[n] + acct[n].to_public().to_string(version=NET["xpub"]) for n in "ABC"}
prv = {n: ORIG[n] + acct[n].to_string(version=NET["xprv"]) for n in "ABC"}


def desc(a_priv=False):
    A = prv["A"] if a_priv else pub["A"]
    return "tr(musig(%s,%s)/0/*,{pk(musig(%s,%s)/0/*),pk(musig(%s,%s)/0/*)})" % (
        A, pub["B"], A, pub["C"], pub["B"], pub["C"])


wallet("mw3", "true"); wallet("ma3", "false"); wallet("miner", "false")
d_watch = imp("mw3", desc()); imp("ma3", desc(a_priv=True))
addr = j("deriveaddresses", d_watch, json.dumps([0, 0]))[0]

maddr = cli("getnewaddress", wallet="miner")
while float(j("getbalances", wallet="miner")["mine"]["trusted"]) < 1:
    cli("generatetoaddress", 101, maddr)
txid = cli("sendtoaddress", addr, "0.5", wallet="miner")
cli("generatetoaddress", 1, maddr)
rawtx = j("gettransaction", txid, "true", "true", wallet="miner")["hex"]
vout = [o["n"] for o in j("decoderawtransaction", rawtx)["vout"]
        if o["scriptPubKey"].get("address") == addr][0]
dest = cli("getnewaddress", "", "bech32m", wallet="miner")
raw = cli("createrawtransaction", json.dumps([{"txid": txid, "vout": vout}]),
          json.dumps([{dest: 0.4999}]))
psbt_b64 = cli("utxoupdatepsbt", cli("converttopsbt", raw))
psbt_b64 = j("walletprocesspsbt", psbt_b64, "false", "DEFAULT", "true", wallet="mw3")["psbt"]
psbt_b64 = j("walletprocesspsbt", psbt_b64, "true", "DEFAULT", "true", wallet="ma3")["psbt"]
psbt_round_one = psbt_b64
print("coordinator   : psbt with Core's round-1 nonce, %d chars" % len(psbt_b64))

# ============================================================ the device begins
# Exactly what the signing screen will call, and nothing else.
from seedsigner.helpers import musig2_session as msess

root = roots["B"]
session = msess.Musig2Session()

psbt = PSBT.from_string(psbt_b64)
progress = msess.advance(psbt, root, session)
print("\nround one     : %s  signed=%d waiting=%d leaves_skipped=%d" % (
    progress.stage, progress.signed_inputs, progress.waiting_inputs, progress.skipped_leaves))
assert progress.stage == msess.ROUND_ONE
psbt_round_one = psbt_b64
psbt_b64 = str(psbt)

# the coordinator collects, and the other signer takes its turn
psbt_b64 = j("walletprocesspsbt", psbt_b64, "true", "DEFAULT", "true", wallet="ma3")["psbt"]
psbt_both_nonces = psbt_b64

psbt = PSBT.from_string(psbt_b64)
progress = msess.advance(psbt, root, session)
print("round two     : %s  signed=%d waiting=%d" % (
    progress.stage, progress.signed_inputs, progress.waiting_inputs))
assert progress.stage == msess.SIGNED
assert len(session) == 0, "the secret nonce outlived the signature"
role = [r for r in __import__("seedsigner.helpers.musig2_psbt", fromlist=["x"]).roles_for_root(psbt, root) if r.is_keypath][0]
msg = __import__("seedsigner.helpers.musig2_psbt", fromlist=["x"]).sighash_for(psbt, role)

# ============================================================ back to the coordinator
fin = j("finalizepsbt", str(psbt))
print("\nfinalizepsbt  : complete=%s" % fin["complete"])
assert fin["complete"], "Core would not finalize"
txid = cli("sendrawtransaction", fin["hex"])
d = j("decoderawtransaction", fin["hex"])
wit = d["vin"][0]["txinwitness"]
print("BROADCAST     :", txid)
print("witness       :", [len(x) // 2 for x in wit], " vsize", d["vsize"])
assert len(wit) == 1 and len(wit[0]) // 2 == 64
print("\nPASSED: the session module drove both rounds; Core finalized and relayed.")

# ---------------------------------------------------------------- fixture dump
if "--fixture" in sys.argv:
    import base64
    fixture = {
        "comment": "Captured from Bitcoin Core v31.1.0 on regtest by "
                   "_scratch/musig2/device_half.py --fixture. Throwaway keys.",
        "network": "regtest",
        "mnemonics": MNEMONICS,
        "descriptor": d_watch,
        "address": addr,
        "psbt_round_one": psbt_round_one,
        "psbt_both_nonces": psbt_both_nonces,
        "final_tx_hex": fin["hex"],
        "expected": {
            "roles": 2,
            "keypath_agg_id": role.agg_id.hex(),
            "participants": [x.hex() for x in role.participants],
            "my_index": role.my_index,
            "my_derivation": role.my_derivation,
            "tweaks": [t.hex() for t in role.tweaks],
            "is_xonly": role.is_xonly,
            "sighash": msg.hex(),
        },
    }
    out = "/home/rob/apps/_scratch/musig2-app/tests/data/musig2_psbts.json"
    with open(out, "w") as f:
        json.dump(fixture, f, indent=2)
        f.write("\n")
    print("fixture written:", out)
