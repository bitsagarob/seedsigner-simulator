#!/usr/bin/env python3
"""Phase 1b: the demo's real shape.

Ranged 2-of-3, keypath musig(A,B) with two fallback leaves, xpubs and BIP-328
derivation. Core holds A. This script holds B and does the aggregate-key
derivation and the partial signature in pure Python.
"""
import sys, json, subprocess, hashlib, hmac, struct

sys.path.insert(0, "/home/rob/.cache/tmp/ss-sp-spend-nno_t15g")
sys.path.insert(0, "/home/rob/apps/_scratch/musig2")

from embit import bip32, networks
from embit.psbt import PSBT
from embit.transaction import SIGHASH
import bip327_reference as m

RD = "/home/rob/.cache/tmp/musig-regtest"
NET = networks.NETWORKS["regtest"]
ACCT = "m/86h/1h/0h"
MUSIG_CC = hashlib.sha256(b"MuSig2MuSig2MuSig2").digest()   # BIP-328


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
    have = {d["desc"] for d in j("listdescriptors", wallet=name)["descriptors"]}
    if desc + "#" + ck not in have:
        r = j("importdescriptors", json.dumps(
            [{"desc": desc + "#" + ck, "active": False, "timestamp": "now", "range": [0, 999]}]),
            wallet=name)
        if not r[0]["success"]:
            assert "current range" in r[0]["error"]["message"], r
    return desc + "#" + ck


# ---------------------------------------------------------------- three keys
roots = {n: bip32.HDKey.from_seed(hashlib.sha256(("musig-2of3-" + n).encode()).digest())
         for n in "ABC"}
acct = {n: k.derive(ACCT) for n, k in roots.items()}
pub = {n: acct[n].to_public().to_string(version=NET["xpub"]) for n in "ABC"}
prv = {n: acct[n].to_string(version=NET["xprv"]) for n in "ABC"}
fp = {n: roots[n].my_fingerprint.hex() for n in "ABC"}
ORIG = {n: "[%s/86h/1h/0h]" % fp[n] for n in "ABC"}


def key(n, priv=False):
    return ORIG[n] + (prv[n] if priv else pub[n])


def desc(a_priv=False, b_priv=False):
    A, B, C = key("A", a_priv), key("B", b_priv), key("C")
    return "tr(musig(%s,%s)/0/*,{pk(musig(%s,%s)/0/*),pk(musig(%s,%s)/0/*)})" % (A, B, A, C, B, C)


wallet("mw2", "true")     # watch-only, all three xpubs
wallet("ma2", "false")    # Core: A's xprv only
d_watch = imp("mw2", desc())
imp("ma2", desc(a_priv=True))
addr = j("deriveaddresses", d_watch, json.dumps([0, 0]))[0]
print("2of3 address  :", addr)

# ---------------------------------------------------------------- fund + build
wallet("miner", "false")
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
psbt_b64 = j("walletprocesspsbt", psbt_b64, "false", "DEFAULT", "true", wallet="mw2")["psbt"]
psbt_b64 = j("walletprocesspsbt", psbt_b64, "true", "DEFAULT", "true", wallet="ma2")["psbt"]
print("core round1   : done")

# ---------------------------------------------------------------- our half
TY_PART, TY_NONCE, TY_PSIG = 0x1a, 0x1b, 0x1c
mf = lambda inp, ty: {k[1:]: v for k, v in inp.unknown.items() if k[0] == ty}

pkA = acct["A"].to_public().key.sec()
pkB = acct["B"].to_public().key.sec()
skB = acct["B"].key.secret

p = PSBT.from_string(psbt_b64)
inp = p.inputs[0]

# Core wrote its nonce under exactly one aggregate: that is the keypath one
# Core signs for every aggregate it belongs to, keypath and leaves alike.
# The keypath one is the entry with no 32-byte leaf hash appended.
keypath = [k for k in mf(inp, TY_NONCE) if len(k) == 66 and k[:33] == pkA]
print("core nonces   : %d total, %d keypath" % (len(mf(inp, TY_NONCE)), len(keypath)))
assert len(keypath) == 1, keypath
agg_id = keypath[0][33:66]

# find the 0x1a entry whose participants are the A,B pair
cand = {a: [b[i:i + 33] for i in range(0, len(b), 33)] for a, b in mf(inp, TY_PART).items()}
parent_agg, pubkeys = next((a, ps) for a, ps in cand.items() if set(ps) == {pkA, pkB})
my_index = pubkeys.index(pkB)
print("participants  :", [x.hex()[:10] for x in pubkeys], "we are #%d" % my_index)

# ---- BIP-328: aggregate -> synthetic xpub -> unhardened /0/0 -> plain tweaks
ctx = m.key_agg(pubkeys)
assert m.cbytes(ctx.Q) == parent_agg, "our KeyAgg disagrees with Core's 0x1a"
print("keyagg        : matches Core's parent aggregate")

tweaks, is_xonly, cc = [], [], MUSIG_CC
for index in (0, 0):                                   # the descriptor's /0/*  at index 0
    I = hmac.new(cc, m.cbytes(ctx.Q) + struct.pack(">I", index), hashlib.sha512).digest()
    tweaks.append(I[:32]); is_xonly.append(False); cc = I[32:]
    ctx = m.apply_tweak(ctx, I[:32], False)
derived = m.cbytes(ctx.Q)
print("derived agg   :", derived.hex(), "== 0x1b id:", derived == agg_id)

merkle = inp.taproot_merkle_root or b""
taptweak = m.tagged_hash("TapTweak", m.xbytes(ctx.Q) + merkle)
tweaks.append(taptweak); is_xonly.append(True)
full = m.key_agg_and_tweak(pubkeys, tweaks, is_xonly)
spk = inp.witness_utxo.script_pubkey.data
assert spk[2:] == m.xbytes(full.Q), "tweak chain wrong: %s vs %s" % (spk[2:].hex(), m.xbytes(full.Q).hex())
print("tweak chain   : OK (2 bip32 tweaks + taptweak) matches scriptPubKey")

print("agg_id        :", agg_id.hex())
print("output key    :", m.xbytes(full.Q).hex())
print("agg_id == 02/03||xonly(output key)?", agg_id[1:] == m.xbytes(full.Q), " parity byte:", hex(agg_id[0]))
msg = p.sighash(0, sighash=SIGHASH.DEFAULT)
secnonce, pubnonce = m.nonce_gen(skB, pkB, m.xbytes(full.Q), msg, None)
inp.unknown[bytes([TY_NONCE]) + pkB + agg_id] = pubnonce
psbt_b64 = str(p)

r = j("walletprocesspsbt", psbt_b64, "true", "DEFAULT", "true", wallet="ma2")
psbt_b64 = r["psbt"]
p = PSBT.from_string(psbt_b64); inp = p.inputs[0]
nonces, psigs = mf(inp, TY_NONCE), mf(inp, TY_PSIG)
print("core round2   : nonces=%d psigs=%d" % (len(nonces), len(psigs)))
assert len(psigs) == 1, "Core produced no partial signature"

aggnonce = m.nonce_agg([nonces[pk + agg_id] for pk in pubkeys])
session = m.SessionContext(aggnonce, pubkeys, tweaks, is_xonly, msg)
psig = m.sign(secnonce, skB, session)
assert m.partial_sig_verify(psig, [nonces[pk + agg_id] for pk in pubkeys],
                            pubkeys, tweaks, is_xonly, msg, my_index)
inp.unknown[bytes([TY_PSIG]) + pkB + agg_id] = psig

fin = j("finalizepsbt", str(p))
print("finalizepsbt  : complete=%s" % fin["complete"])
if not fin.get("complete"):
    raise SystemExit("FAILED")
txid = cli("sendrawtransaction", fin["hex"])
d = j("decoderawtransaction", fin["hex"])
wit = d["vin"][0]["txinwitness"]
print("BROADCAST     :", txid)
print("witness       :", [len(x) // 2 for x in wit], "vsize", d["vsize"])
assert len(wit) == 1 and len(wit[0]) // 2 == 64
print("\nPASSED: ranged 2-of-3, keypath spend, 64-byte witness, half of it signed by our own code.")
