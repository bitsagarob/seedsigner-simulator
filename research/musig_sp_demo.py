#!/usr/bin/env python3
"""A 2-of-3 MuSig2 key path paying a silent payment address, end to end.

Two simulated SeedSigners hold the two signing seeds. This script is the
coordinator and it holds no key: it builds the PSBT, carries it between the two
devices over QR, checks every share it is handed, writes the recipient's output
script, and hands the finished thing to a stock Bitcoin Core to finalise and
relay. Core's wallet, watch-only, is what fills the MuSig2 fields.

  ./musig_sp_demo.py --network regtest
  ./musig_sp_demo.py --network signet
  ./musig_sp_demo.py --network mainnet [--no-broadcast]

Seeds come from bitsaga/research/seedsigner-sp/MUSIG2_SP.local.json. Real money
on mainnet.
"""
import argparse, base64, json, os, subprocess, sys, time, urllib.request

sys.path.insert(0, "$SIGNET_TEST_DIR")
sys.path.insert(0, "$MUSIG_APP/src")

from embit import bip32, bip39, ec, networks, script
from embit.psbt import PSBT
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from embit.silent_payments.psbt import SilentPaymentsPSBT, SilentPaymentData
from embit.silent_payments.sp import _tweak_mul, get_input_hash, derive_recipient_outputs

from seedsigner.helpers import musig2_psbt as mp
from seedsigner.helpers import silent_payments as sp_keys
from seedsigner.models.settings_definition import SettingsConstants

SEEDS = "$SEEDS_DIR/MUSIG2_SP.local.json"
CACHE = "$TMP"
SIGNET_CHALLENGE = "0014cb5c938876427c86a068fba6d6ddd09ed4fa183f"
DNSSEC_VERIFY = "$DNSSEC_VERIFY_BIN"
PSBT_OUT_DNSSEC_PROOF = b"\x35"


def payment_name_proof(hrn):
    """(RFC 9102 proof bytes, sp1 address) for a BIP-353 name, fetched and validated here.

    Validated here for our own sake only; each device repeats the validation
    from the bytes in the PSBT and trusts nothing done on this machine."""
    user, _, domain = hrn.partition("@")
    proc = subprocess.run([DNSSEC_VERIFY, "%s.user._bitcoin-payment.%s" % (user, domain)],
                          capture_output=True, text=True)
    answer = json.loads(proc.stdout)
    if not answer.get("ok"):
        raise RuntimeError("no valid proof for %s: %s" % (hrn, answer.get("error")))
    address = None
    for record in answer.get("records", []):
        if record.lower().startswith("bitcoin:") and "?" in record:
            for pair in record.split("?", 1)[1].split("&"):
                if pair.lower().startswith("sp="):
                    address = pair[3:]
    if not address:
        raise RuntimeError("%s publishes no silent payment address" % hrn)
    return bytes.fromhex(answer["proof_hex"]), address, answer.get("expires")


def timecode_now():
    """The GoPro oT timecode the device reads as a date: oT + YYMMDDHHMMSS, UTC."""
    return "oT" + time.strftime("%y%m%d%H%M%S", time.gmtime())

NETS = {
    "regtest": dict(embit="regtest", ss=SettingsConstants.REGTEST, coin=1, sim="regtest",
                    datadir=CACHE + "/musig-regtest", cli="/usr/local/bin/bitcoin-cli"),
    "signet": dict(embit="signet", ss=SettingsConstants.TESTNET, coin=1, sim="testnet",
                   datadir=CACHE + "/musig-signet", cli="/opt/bitsaga-signet/bin/bitcoin-cli"),
    "mainnet": dict(embit="main", ss=SettingsConstants.MAINNET, coin=0, sim="mainnet",
                    datadir=CACHE + "/musig-mainnet", cli="/usr/local/bin/bitcoin-cli"),
}
MAIN_NODE = ["/usr/local/bin/bitcoin-cli", "-conf=/etc/bitcoin/bitcoin.conf", "-datadir=/var/lib/bitcoind"]


def log(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------- bitcoin core

class Core:
    """One bitcoin-cli target. `wallet` scopes a call to a loaded wallet."""

    def __init__(self, base_cmd):
        self.base = base_cmd

    def raw(self, *a, wallet=None):
        cmd = list(self.base)
        if wallet:
            cmd.append("-rpcwallet=" + wallet)
        cmd += [str(x) for x in a]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("%s -> %s" % (" ".join(cmd[-3:])[:80], r.stderr.strip()[:400]))
        return r.stdout.strip()

    def j(self, *a, **kw):
        out = self.raw(*a, **kw)
        # bitcoin-cli prints nothing at all for a JSON null, gettxout's answer
        # for an output that is spent or does not exist.
        return json.loads(out) if out else None

    def wallet(self, name, disable_private_keys=True):
        if name in self.j("listwallets"):
            return
        try:
            self.raw("loadwallet", name)
        except RuntimeError:
            self.raw("createwallet", name, "true" if disable_private_keys else "false",
                     "true", "", "false", "true", "true")

    def import_desc(self, name, desc):
        ck = self.j("getdescriptorinfo", desc)["checksum"]
        r = self.j("importdescriptors", json.dumps(
            [{"desc": desc + "#" + ck, "active": False, "timestamp": "now", "range": [0, 99]}]),
            wallet=name)
        if not r[0]["success"]:
            assert "current range" in r[0]["error"]["message"], r
        return desc + "#" + ck


def local_core(net):
    """The Core instance that holds our watch-only wallet, started if needed."""
    n = NETS[net]
    dd = n["datadir"]
    conf = os.path.join(dd, "bitcoin.conf")
    if net == "regtest":
        return Core([n["cli"], "-datadir=" + dd, "-conf=" + conf])
    os.makedirs(dd, exist_ok=True)
    rpcport = {"signet": 38402, "mainnet": 8402}[net]
    if not os.path.exists(conf):
        lines = ["rpcport=%d" % rpcport, "rpcbind=127.0.0.1", "rpcallowip=127.0.0.1",
                 "listen=0", "server=1"]
        if net == "signet":
            lines = ["signet=1", "[signet]"] + lines + [
                "signetchallenge=" + SIGNET_CHALLENGE, "signetblocktime=30",
                "connect=127.0.0.1:38333", "dnsseed=0"]
        else:
            lines = ["chain=main", "[main]"] + lines + ["networkactive=0", "dnsseed=0",
                                                         "connect=0", "prune=550"]
        with open(conf, "w") as f:
            f.write("\n".join(lines) + "\n")
    core = Core([n["cli"], "-datadir=" + dd, "-conf=" + conf])
    try:
        core.j("getblockchaininfo")
    except Exception:
        bitcoind = n["cli"].replace("bitcoin-cli", "bitcoind")
        subprocess.Popen([bitcoind, "-datadir=" + dd, "-conf=" + conf, "-daemon"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            time.sleep(1)
            try:
                core.j("getblockchaininfo")
                break
            except Exception:
                pass
        else:
            raise RuntimeError("bitcoind for %s did not come up" % net)
    return core


# ----------------------------------------------------------------- keys

def load_seeds():
    with open(SEEDS) as f:
        return json.load(f)["seeds"]


def signer_keys(seeds, net):
    n = NETS[net]
    NET = networks.NETWORKS[n["embit"]]
    acct = "m/86h/%dh/0h" % n["coin"]
    out = {}
    for name in "ABC":
        root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(seeds[name]["mnemonic"]))
        key = root.derive(acct).to_public()
        out[name] = "[%s/%s]%s" % (root.my_fingerprint.hex(), acct[2:],
                                    key.to_string(version=NET["xpub"]))
    return out


def descriptor(pub):
    return "tr(musig(%s,%s)/0/*,{pk(musig(%s,%s)/0/*),pk(musig(%s,%s)/0/*)})" % (
        pub["A"], pub["B"], pub["A"], pub["C"], pub["B"], pub["C"])


def recipient(seeds, net):
    n = NETS[net]
    seed_bytes = bip39.mnemonic_to_seed(seeds["R"]["mnemonic"])
    scan, spend = sp_keys.derive_keys(seed_bytes, n["ss"])
    address = sp_keys.payment_address(seed_bytes, n["ss"])
    return scan, spend, address


# ----------------------------------------------------------------- psbt plumbing

def to_v2_silent(psbt_b64, scan_pub, spend_pub, sp_output_index):
    """Core's filled v0 PSBT, as the BIP-375 PSBTv2 the devices need."""
    v0 = PSBT.from_string(psbt_b64)
    psbt = SilentPaymentsPSBT.parse(v0.serialize())
    psbt.version = 2
    psbt.tx_version = v0.tx.version
    psbt.locktime = v0.tx.locktime
    out = psbt.outputs[sp_output_index]
    out.script_pubkey = None
    out.sp_data = SilentPaymentData(scan_pub, spend_pub)
    return SilentPaymentsPSBT.parse(psbt.serialize())


def to_v0(psbt):
    """The finished v2 as a v0 Core can finalise. Same transaction, checked."""
    v2 = SilentPaymentsPSBT.parse(psbt.serialize())
    txid = v2.tx.txid()
    v2.version = None
    for out in v2.outputs:
        out.sp_data = None
    v0 = PSBT.parse(v2.serialize())
    assert v0.tx.txid() == txid, "the v0 and v2 transactions differ"
    return v0


def merge_unknowns(master, contributed, field_types):
    """Copy the given per-input field types from a device's PSBT into ours."""
    assert len(master.inputs) == len(contributed.inputs)
    for mine, theirs in zip(master.inputs, contributed.inputs):
        for k, v in theirs.unknown.items():
            if k and k[0] in field_types:
                mine.unknown[k] = v


def specter_frames(payload, chunk=280):
    parts = [payload[i:i + chunk] for i in range(0, len(payload), chunk)]
    return ["p%dof%d %s" % (i, len(parts), p) for i, p in enumerate(parts, start=1)]


# ----------------------------------------------------------------- devices

class Device:
    """One simulated SeedSigner, holding one seed, alive across every round."""

    def __init__(self, name, seedqr, url, shots, headless=True, timecode=None, pubkey=None):
        from simdrive import Sim
        self.name = name
        self.seedqr = seedqr
        self.pubkey = pubkey            # our 33-byte participant key on the input
        self.timecode = timecode
        self.shots = shots
        self.sim = Sim(headless=headless, url=url)
        self.n = 0

    def shot(self, label):
        self.n += 1
        path = os.path.join(self.shots, "%s-%02d-%s.png" % (self.name, self.n, label))
        self.sim.screen_png(path)
        return path

    def start(self, playwright=None):
        """One Playwright serves every device; each gets its own browser."""
        from simdrive import CAMERA_SHIM, CHROME
        sim = self.sim
        sim._pw = playwright
        launch = dict(headless=sim.headless, args=["--use-fake-ui-for-media-stream"])
        if CHROME:
            launch["executable_path"] = CHROME
        sim.browser = sim._pw.chromium.launch(**launch)
        ctx = sim.browser.new_context(permissions=["camera"],
                                      viewport={"width": 1100, "height": 1200},
                                      service_workers="block")
        ctx.add_init_script(CAMERA_SHIM)
        sim.page = ctx.new_page()
        sim.page.on("console", lambda m: sim.console.append(m.text))
        sim.page.on("pageerror", lambda e: sim.console.append("PAGEERROR %s" % e))
        self.sim.open(timeout=300)
        loaded = self.sim.page.evaluate("() => window.__firmware")
        assert loaded == "doomsigner-musig", "device %s loaded %s" % (self.name, loaded)
        since = self.sim.mark()
        self.sim.select()
        self.sim.wait_screen("ScanScreen", since=since, timeout=60)
        self.sim.scan_qr_frames([self.seedqr], expect_screen="SeedFinalizeScreen",
                                since=since, timeout=120)
        self.shot("seed-loaded")
        self.sim.select()
        time.sleep(1.5)
        self.sim.back_to_home()
        log("   device %s: seed loaded" % self.name)
        if self.timecode:
            # A SeedSigner has no clock. The date it judges a DNSSEC proof
            # against comes from a QR, and the rules say from a second screen.
            # Here the coordinator is that second screen, which the proof page
            # says out loud.
            since = self.sim.mark()
            self.sim.select()
            self.sim.wait_screen("ScanScreen", since=since, timeout=240)
            self.sim.scan_qr_frames([self.timecode], expect_screen="LargeIconStatusScreen",
                                    since=since, timeout=120)
            time.sleep(1.0)
            self.shot("date-set")
            self.sim.select()
            time.sleep(1.0)
            self.sim.back_to_home()
            log("   device %s: date set from %s" % (self.name, self.timecode))

    def stop(self):
        if self.sim.browser:
            self.sim.browser.close()

    def round(self, psbt_b64, label):
        """Scan the PSBT, walk to the QR, read the device's PSBT back."""
        try:
            return self._round(psbt_b64, label)
        except Exception:
            # What was on the screen, and what the wallet said, at the moment
            # it went wrong. Guessing from a 25-line tail cost two runs.
            self.shot("FAILED-" + label)
            with open(os.path.join(self.shots, "%s-FAILED-%s.console.txt" % (self.name, label)), "w") as f:
                f.write("\n".join(self.sim.console[-400:]))
            raise

    def _round(self, psbt_b64, label):
        from accept import read_ur_psbt
        sim = self.sim
        if sim.current_screen() != "MainMenuScreen":
            log("   device %s: not at home before %s (%s), climbing back" % (
                self.name, label, sim.current_screen()))
            sim.back_to_home()
        since = sim.mark()
        sim.select()
        # Generous: with two browsers each running a Python wallet, opening the
        # camera screen has taken over a minute.
        sim.wait_screen("ScanScreen", since=since, timeout=240)
        # Hold the frames up until the device leaves the scan screen, however
        # it leaves it. Naming the next screen would be a guess: it depends on
        # the transaction, and a wrong guess means feeding QR for the whole
        # timeout.
        frames = specter_frames(psbt_b64)
        deadline = time.time() + 300
        time.sleep(1.0)
        while time.time() < deadline and sim.current_screen() == "ScanScreen":
            for frame in frames:
                sim.show_qr(frame)
                time.sleep(0.55)
                if sim.current_screen() != "ScanScreen":
                    break
        sim.clear_camera()
        assert sim.current_screen() != "ScanScreen", "device %s never read the PSBT" % self.name
        time.sleep(1.5)
        seen = []
        for _ in range(40):
            tmp = os.path.join(self.shots, "_tmp")
            sim.screen_png(tmp)
            screen = sim.current_screen() or ""
            if screen != (seen[-1] if seen else None):
                seen.append(screen)
                self.n += 1
                os.replace(tmp + ".png", os.path.join(
                    self.shots, "%s-%02d-%s-%s.png" % (self.name, self.n, label, screen)))
            else:
                os.remove(tmp + ".png")
            if screen == "QRDisplayScreen":
                break
            sim.select()
            time.sleep(0.9)
        else:
            raise RuntimeError("device %s never reached the QR: %s" % (self.name, seen))
        log("   device %s %s: %s" % (self.name, label, " > ".join(seen)))
        sim.up(6)
        time.sleep(0.8)
        out = read_ur_psbt(sim, timeout=300)
        sim.back_to_home()
        return out


# ----------------------------------------------------------------- chain access

def mempool_get(path):
    with urllib.request.urlopen("https://mempool.space/api" + path, timeout=30) as r:
        return r.read().decode()


def fund(net, core, address, amount_sat, sender_cfg=None):
    """Put a coin on the 2-of-3 address. Returns (txid, vout, value)."""
    if net == "regtest":
        core.wallet("miner", disable_private_keys=False)
        maddr = core.raw("getnewaddress", wallet="miner")
        while float(core.j("getbalances", wallet="miner")["mine"]["trusted"]) < 1:
            core.raw("generatetoaddress", 101, maddr)
        txid = core.raw("sendtoaddress", address, "%.8f" % (amount_sat / 1e8), wallet="miner")
        core.raw("generatetoaddress", 1, maddr)
    elif net == "signet":
        req = urllib.request.Request("https://signet.bitsaga.be/api/claim",
                                     data=json.dumps({"address": address}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
        txid = body["txid"]
        log("   faucet paid", txid)
    else:
        raise SystemExit("fund the mainnet address yourself, then pass --utxo txid:vout")
    vout, value = find_vout(chain_core(net, core), txid, address)
    return txid, vout, value


def chain_core(net, core):
    """The node that can see the chain: the production node on mainnet, ours elsewhere."""
    return Core(MAIN_NODE) if net == "mainnet" else core


def find_vout(node, txid, address):
    """(vout, sats) of the output paying `address`, from the UTXO set plus mempool.

    gettxout needs no txindex and sees unconfirmed outputs, which is all a
    just-broadcast funding transaction is."""
    for _ in range(300):
        for i in range(8):
            try:
                info = node.j("gettxout", txid, i, "true")
            except RuntimeError:
                break
            if info and info["scriptPubKey"].get("address") == address:
                return i, round(info["value"] * 1e8)
        time.sleep(2)
    raise RuntimeError("funding output never seen: " + txid)


def wait_confirmed(net, core, txid, vout=0):
    """Block until the output is in a block. Returns its block height."""
    node = chain_core(net, core)
    for _ in range(4000):
        try:
            info = node.j("gettxout", txid, vout, "true")
            if info and info.get("confirmations", 0) > 0:
                return node.j("getblockcount") - info["confirmations"] + 1
        except RuntimeError:
            pass
        time.sleep(15 if net == "mainnet" else 3)
    raise RuntimeError("never confirmed: " + txid)


def recipient_finds_it(tx_hex, prev_script_hex, scan_priv, spend_pub):
    """BIP-352 receiver: no shares, no PSBT, only the chain and the scan key."""
    tx = Transaction.parse(bytes.fromhex(tx_hex))
    prev = bytes.fromhex(prev_script_hex)
    assert prev[:2] == b"\x51\x20"
    A = b"\x02" + prev[2:34]
    input_hash = get_input_hash(tx.vin, A)
    ecdh = _tweak_mul(A, scan_priv.secret)
    outs = derive_recipient_outputs(_tweak_mul(ecdh, input_hash), [spend_pub])
    found = [i for i, o in enumerate(tx.vout) if bytes(o.script_pubkey.data) == b"\x51\x20" + outs[0]]
    return found


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="regtest", choices=list(NETS))
    ap.add_argument("--shots", default=None)
    ap.add_argument("--sim-url", default="http://127.0.0.1:8791/wallet.html?firmware=doomsigner-musig&debug=1")
    ap.add_argument("--amount", type=int, default=20000, help="sats to put on the 2-of-3")
    ap.add_argument("--fee", type=int, default=250)
    ap.add_argument("--headless", default="1")
    ap.add_argument("--no-broadcast", action="store_true")
    ap.add_argument("--utxo", default=None, help="txid:vout already on the 2-of-3, skip funding")
    ap.add_argument("--payment-name", default=None,
                    help="BIP-353 name that resolves to the recipient, e.g. musig2@silentpayments.net; "
                         "its DNSSEC proof goes in the PSBT and each device validates it")
    args = ap.parse_args()
    net = args.network
    n = NETS[net]
    shots = args.shots or os.path.join(CACHE, "musig-sp-shots-" + net)
    os.makedirs(shots, exist_ok=True)
    result = {"network": net, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    seeds = load_seeds()
    pub = signer_keys(seeds, net)
    desc = descriptor(pub)
    scan, spend, sp_address = recipient(seeds, net)
    log("== %s" % net)
    log("   descriptor :", desc[:60] + "...")
    log("   recipient  :", sp_address)

    core = local_core(net)
    core.wallet("dw")
    desc_ck = core.import_desc("dw", desc)
    address = core.j("deriveaddresses", desc_ck, json.dumps([0, 0]))[0]
    log("   2-of-3     :", address)
    result.update(descriptor=desc_ck, address=address, sp_address=sp_address,
                  fingerprints={k: seeds[k]["fingerprint"] for k in "ABCR"})

    # --- the coin
    sender_cfg = None
    if args.utxo:
        txid, vout = args.utxo.split(":")
        vout = int(vout)
        if net == "mainnet":
            info = Core(MAIN_NODE).j("gettxout", txid, vout)
        else:
            info = core.j("gettxout", txid, vout)
        value = round(info["value"] * 1e8)
    else:
        txid, vout, value = fund(net, core, address, args.amount, sender_cfg)
        log("   funded     : %s:%d (%d sat), waiting for a confirmation" % (txid, vout, value))
        result["funding_block"] = wait_confirmed(net, core, txid, vout)
    result.update(funding_txid=txid, funding_vout=vout, funding_value=value)
    prev_script = script.address_to_scriptpubkey(address).data.hex()

    # --- the transaction, filled by Core's watch-only wallet
    placeholder = ec.PublicKey.parse(spend.get_public_key().sec())
    placeholder_addr = script.p2tr(placeholder).address(networks.NETWORKS[n["embit"]])
    raw = core.raw("createpsbt", json.dumps([{"txid": txid, "vout": vout}]),
                   json.dumps([{placeholder_addr: "%.8f" % ((value - args.fee) / 1e8)}]))
    if net == "mainnet":
        raw = Core(MAIN_NODE).raw("utxoupdatepsbt", raw)
    else:
        raw = core.raw("utxoupdatepsbt", raw)
    filled = core.j("walletprocesspsbt", raw, "false", "DEFAULT", "true", wallet="dw")["psbt"]
    psbt = to_v2_silent(filled, scan.get_public_key(), spend.get_public_key(), 0)
    timecode = None
    if args.payment_name:
        proof, named_address, expires = payment_name_proof(args.payment_name)
        assert named_address == sp_address, "the name resolves to %s, not the demo recipient" % named_address
        name_bytes = args.payment_name.encode()
        psbt.outputs[0].unknown[PSBT_OUT_DNSSEC_PROOF] = bytes([len(name_bytes)]) + name_bytes + proof
        psbt = SilentPaymentsPSBT.parse(psbt.serialize())
        timecode = timecode_now()
        result.update(payment_name=args.payment_name, dnssec_proof_bytes=len(proof),
                      dnssec_proof_expires=expires, timecode=timecode)
        log("   name       : %s, %d-byte DNSSEC proof in the PSBT, expires %s" % (
            args.payment_name, len(proof), expires))
    assert mp.has_musig2_fields(psbt) and mp.sp_scripts_missing(psbt)
    log("   psbt v2    : %d bytes, MuSig2 fields in, output script deliberately absent"
        % len(psbt.serialize()))

    # --- the devices
    participant = {}
    for name in "AB":
        root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(seeds[name]["mnemonic"]))
        participant[name] = mp.roles(psbt, root)[0][0].pubkey
    devices = [Device(name, seeds[name]["seedqr"], args.sim_url + "&network=" + n["sim"],
                      shots, headless=args.headless == "1", timecode=timecode,
                      pubkey=participant[name]) for name in "AB"]
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    try:
        for d in devices:
            d.start(playwright)

        log("== round 0: shares and proofs")
        for d in devices:
            back = SilentPaymentsPSBT.from_string(d.round(psbt.to_string(), "shares"))
            merge_unknowns(psbt, back, {mp.PSBT_IN_MUSIG2_PARTIAL_ECDH_SHARE, mp.PSBT_IN_MUSIG2_PARTIAL_DLEQ})
        scripts = mp.expected_scripts(psbt)     # verifies every proof
        for idx, s in scripts.items():
            psbt.outputs[idx].script_pubkey = s
        psbt.tx_modifiable_flags = 0
        psbt = SilentPaymentsPSBT.parse(psbt.serialize())
        mp.check_scripts(psbt)
        sp_out = psbt.outputs[0].script_pubkey.address(networks.NETWORKS[n["embit"]])
        log("   output     :", sp_out, "(derived from two shares, both proofs verified)")
        result["sp_output_address"] = sp_out

        # A device that sees every other nonce signs in the same pass, so both fields
        # are taken from every return, and a device already signed is not asked again.
        signing = {mp.PSBT_IN_MUSIG2_PUB_NONCE, mp.PSBT_IN_MUSIG2_PARTIAL_SIG}
        log("== round 1: nonces")
        for d in devices:
            back = SilentPaymentsPSBT.from_string(d.round(psbt.to_string(), "nonce"))
            merge_unknowns(psbt, back, signing)
        log("== round 2: partial signatures")
        for d in devices:
            if any(k[0] == mp.PSBT_IN_MUSIG2_PARTIAL_SIG and k[1:34] == d.pubkey
                   for k in psbt.inputs[0].unknown):
                log("   device %s already signed" % d.name)
                continue
            back = SilentPaymentsPSBT.from_string(d.round(psbt.to_string(), "sign"))
            merge_unknowns(psbt, back, signing)
    finally:
        for d in devices:
            d.stop()
        playwright.stop()

    # --- finalise with stock Core, relay
    v0 = to_v0(psbt)
    fin = core.j("finalizepsbt", v0.to_string())
    assert fin["complete"], "Core would not finalize"
    tx_hex = fin["hex"]
    decoded = core.j("decoderawtransaction", tx_hex)
    wit = decoded["vin"][0]["txinwitness"]
    assert len(wit) == 1 and len(wit[0]) == 128, wit
    result.update(txid=decoded["txid"], tx_hex=tx_hex, vsize=decoded["vsize"])
    log("   final tx   :", decoded["txid"], "witness: one %d-byte signature" % (len(wit[0]) // 2))

    found = recipient_finds_it(tx_hex, prev_script, scan, spend.get_public_key())
    assert found == [0], "the recipient would not find the payment: %r" % found
    log("   recipient  : scans the chain with its scan key and finds output 0")

    if args.no_broadcast:
        log("   not broadcast (--no-broadcast)")
    else:
        relay = Core(MAIN_NODE) if net == "mainnet" else core
        sent = relay.raw("sendrawtransaction", tx_hex)
        assert sent == decoded["txid"]
        log("   BROADCAST  :", sent)
        if net == "regtest":
            core.raw("generatetoaddress", 1, core.raw("getnewaddress", wallet="miner"))
        result["confirmed_block"] = wait_confirmed(net, core, sent, 0)
        log("   confirmed  :", result["confirmed_block"])
    result["psbt_final_v2"] = psbt.to_string()
    with open(os.path.join(shots, "RESULT.json"), "w") as f:
        json.dump(result, f, indent=2)
    log("\nDONE. shots and RESULT.json in", shots)


if __name__ == "__main__":
    main()
