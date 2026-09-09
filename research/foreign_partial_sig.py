#!/usr/bin/env python3
"""Phase 1 gate: will Bitcoin Core finalize a MuSig2 partial signature that Core
did not produce?

Core holds key A. This script holds key B and does its half in pure Python with
the BIP-327 reference implementation. If the broadcast lands, foreign partial
signatures interoperate and the SeedSigner side is only wiring after this.
"""
import sys, json, subprocess, hashlib

sys.path.insert(0, "$TMP/ss-sp-spend-nno_t15g")
sys.path.insert(0, "$RESEARCH_DIR")

from embit import ec, networks
from embit.psbt import PSBT
from embit.transaction import SIGHASH
import bip327_reference as m

RD = "$TMP/musig-regtest"
NET = networks.NETWORKS["regtest"]


def cli(*a, wallet=None):
    cmd = ["/usr/local/bin/bitcoin-cli", "-datadir=" + RD, "-conf=" + RD + "/bitcoin.conf"]
    if wallet:
        cmd.append("-rpcwallet=" + wallet)
    cmd += [str(x) for x in a]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(cmd[-3:]) + " -> " + r.stderr.strip()[:400])
    return r.stdout.strip()


def j(*a, **kw):
    return json.loads(cli(*a, **kw))


# ---------------------------------------------------------------- keys + wallets
skA = hashlib.sha256(b"musig-test-A").digest()
skB = hashlib.sha256(b"musig-test-B").digest()
kA, kB = ec.PrivateKey(skA), ec.PrivateKey(skB)
pkA, pkB = kA.sec(), kB.sec()

d_watch = "tr(musig(%s,%s))" % (pkA.hex(), pkB.hex())
d_sign = "tr(musig(%s,%s))" % (kA.wif(NET), pkB.hex())

for name, desc, nopriv in (("mw1", d_watch, "true"), ("ma1", d_sign, "false")):
    try:
        cli("createwallet", name, nopriv, "true", "", "false", "true", "true")
    except RuntimeError:
        pass
    ck = j("getdescriptorinfo", desc)["checksum"]
    imp = [{"desc": desc + "#" + ck, "active": False, "timestamp": "now"}]
    res = j("importdescriptors", json.dumps(imp), wallet=name)
    assert res[0]["success"], res

addr = j("deriveaddresses", d_watch + "#" + j("getdescriptorinfo", d_watch)["checksum"])[0]
print("musig address :", addr)

# ---------------------------------------------------------------- fund + build
try:
    cli("loadwallet", "miner")
except RuntimeError:
    try:
        cli("createwallet", "miner")
    except RuntimeError:
        pass
maddr = cli("getnewaddress", wallet="miner")
if int(cli("getblockcount")) < 210:
    cli("generatetoaddress", 101, maddr)
if float(j("getbalances", wallet="miner")["mine"]["trusted"]) < 1:
    cli("generatetoaddress", 101, maddr)

txid = cli("sendtoaddress", addr, "0.5", wallet="miner")
cli("generatetoaddress", 1, maddr)
rawtx = j("gettransaction", txid, "true", "true", wallet="miner")["hex"]
vout = [o["n"] for o in j("decoderawtransaction", rawtx)["vout"]
        if o["scriptPubKey"].get("address") == addr][0]
print("funded        :", txid[:16], "vout", vout)

dest = cli("getnewaddress", "", "bech32m", wallet="miner")
raw = cli("createrawtransaction",
          json.dumps([{"txid": txid, "vout": vout}]),
          json.dumps([{dest: 0.4999}]))
psbt_b64 = cli("converttopsbt", raw)
psbt_b64 = cli("utxoupdatepsbt", psbt_b64)

# the watch wallet knows the musig fields; let it fill them in
psbt_b64 = j("walletprocesspsbt", psbt_b64, "false", "DEFAULT", "true", wallet="mw1")["psbt"]
print("psbt built    :", len(psbt_b64), "b64 chars")

# ---------------------------------------------------------------- round 1: Core
r = j("walletprocesspsbt", psbt_b64, "true", "DEFAULT", "true", wallet="ma1")
psbt_b64 = r["psbt"]
print("core round1   : complete=%s" % r["complete"])


# ---------------------------------------------------------------- our half (B)
TY_PART, TY_NONCE, TY_PSIG = 0x1a, 0x1b, 0x1c


def musig_fields(inp, ty):
    return {k[1:]: v for k, v in inp.unknown.items() if k[0] == ty}


def load(b64):
    return PSBT.from_string(b64)


p = load(psbt_b64)
inp = p.inputs[0]

# participant order is whatever Core put in 0x1a, never the descriptor order
(agg_id_1a, participants_blob), = musig_fields(inp, TY_PART).items()
pubkeys = [participants_blob[i:i + 33] for i in range(0, len(participants_blob), 33)]
assert pkB in pubkeys and pkA in pubkeys, "our key is not a participant"
my_index = pubkeys.index(pkB)
print("participants  :", [x.hex()[:12] for x in pubkeys], "we are #%d" % my_index)

# the identifier 0x1b/0x1c are keyed by: copy Core's rather than re-derive it
(core_nonce_key, core_pubnonce), = musig_fields(inp, TY_NONCE).items()
agg_id = core_nonce_key[33:66]
assert core_nonce_key[:33] == pkA
print("agg id (0x1b) :", agg_id.hex())
print("agg id (0x1a) :", agg_id_1a.hex())

# tweak chain: KeyAgg -> BIP-341 taptweak, no merkle root on this descriptor
ctx = m.key_agg(pubkeys)
taptweak = m.tagged_hash("TapTweak", m.xbytes(ctx.Q))
tweaks, is_xonly = [taptweak], [True]
tweaked = m.key_agg_and_tweak(pubkeys, tweaks, is_xonly)
print("internal key  :", m.xbytes(ctx.Q).hex())
print("output key    :", m.xbytes(tweaked.Q).hex())
assert inp.witness_utxo.script_pubkey.data[2:].hex() == m.xbytes(tweaked.Q).hex(), "tweak chain wrong"
print("tweak chain   : OK, matches the funded scriptPubKey")

msg = p.sighash(0, sighash=SIGHASH.DEFAULT)
print("sighash       :", msg.hex())

# ---- round 1 (ours): fresh nonce, kept in a variable = the RAM session
secnonce, pubnonce = m.nonce_gen(skB, pkB, m.xbytes(ctx.Q), msg, None)
inp.unknown[bytes([TY_NONCE]) + pkB + agg_id] = pubnonce
psbt_b64 = str(p)
print("our round1    : pubnonce written, psbt %d chars" % len(psbt_b64))

# ---- round 2 (Core): now that both nonces are present Core can partial-sign
r = j("walletprocesspsbt", psbt_b64, "true", "DEFAULT", "true", wallet="ma1")
psbt_b64 = r["psbt"]
print("core round2   : complete=%s" % r["complete"])

p = load(psbt_b64)
inp = p.inputs[0]
nonces = musig_fields(inp, TY_NONCE)
psigs_before = musig_fields(inp, TY_PSIG)
print("nonces present:", len(nonces), " core partial sigs:", len(psigs_before))
assert len(psigs_before) == 1, "Core did not produce its partial signature"

# ---- round 2 (ours)
aggnonce = m.nonce_agg([nonces[pkA + agg_id], nonces[pkB + agg_id]])
session = m.SessionContext(aggnonce, pubkeys, tweaks, is_xonly, msg)
psig = m.sign(secnonce, skB, session)
assert m.partial_sig_verify(psig, [nonces[pk + agg_id] for pk in pubkeys],
                            pubkeys, tweaks, is_xonly, msg, my_index), "our own psig fails verify"
print("our psig      :", psig.hex(), "(self-verified)")
inp.unknown[bytes([TY_PSIG]) + pkB + agg_id] = psig
psbt_b64 = str(p)

# ---------------------------------------------------------------- the gate
fin = j("finalizepsbt", psbt_b64)
print("\nfinalizepsbt  : complete=%s" % fin["complete"])
if not fin.get("complete"):
    raise SystemExit("GATE FAILED: Core would not finalize with our partial signature")

txid = cli("sendrawtransaction", fin["hex"])
d = j("decoderawtransaction", fin["hex"])
wit = d["vin"][0]["txinwitness"]
print("BROADCAST     :", txid)
print("witness       :", [len(x) // 2 for x in wit], "vsize", d["vsize"])
assert len(wit) == 1 and len(wit[0]) // 2 == 64, "not a bare keypath spend"
print("\nGATE PASSED: Core finalized and relayed a partial signature it did not make.")
