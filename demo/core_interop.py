#!/usr/bin/env python3
"""Does an unmodified Bitcoin Core interoperate with our MuSig2 PSBTs?

Not the demo any more. Core signs alongside the simulated device, on regtest,
and this is what keeps the wire format honest: if our BIP-373 fields drift,
Core stops being able to sign and this says so.

The demo people can follow lives in the page. This needs a regtest node.

    python3 demo/core_interop.py
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, "/home/rob/.cache/tmp/ss-sp-spend-nno_t15g")
sys.path.insert(0, "/home/rob/apps/_scratch/embit-musig/src")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "web"))
sys.path.insert(0, "/home/rob/apps/doomsigner/src")
sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")

from embit import bip32, bip39, networks


import coordinator

# One pool per wallet. The name is only a key in the pool's store; it never
# reaches a PSBT or the chain.
RD = "/home/rob/.cache/tmp/musig-regtest"
NET = networks.NETWORKS["regtest"]
ACCT = "m/86h/1h/0h"
MNEMONICS = {
    "A": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
    "B": "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong",
    "C": "legal winner thank year wave sausage worth useful legal winner thank yellow",
}
SEEDQR_B = "204720472047204720472047204720472047204720472037"
URL = ("http://127.0.0.1:8791/wallet.html"
       "?firmware=doomsigner-musig&debug=1&network=regtest")


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


def coordinator_setup():
    roots = {n: bip32.HDKey.from_seed(bip39.mnemonic_to_seed(MNEMONICS[n])) for n in "ABC"}
    acct = {n: roots[n].derive(ACCT) for n in "ABC"}
    orig = {n: "[%s/86h/1h/0h]" % roots[n].my_fingerprint.hex() for n in "ABC"}
    pub = {n: orig[n] + acct[n].to_public().to_string(version=NET["xpub"]) for n in "ABC"}
    prv = {n: orig[n] + acct[n].to_string(version=NET["xprv"]) for n in "ABC"}

    def desc(a_priv=False):
        A = prv["A"] if a_priv else pub["A"]
        return "tr(musig(%s,%s)/0/*,{pk(musig(%s,%s)/0/*),pk(musig(%s,%s)/0/*)})" % (
            A, pub["B"], A, pub["C"], pub["B"], pub["C"])

    wallet("dw", "true"); wallet("da", "false"); wallet("miner", "false")
    d_watch = imp("dw", desc()); imp("da", desc(a_priv=True))
    return d_watch


def core_rounds(psbt):
    """Let Core take every round it can before the device is visited.

    walletprocesspsbt does one round per call: it publishes a nonce and stops,
    even when every other nonce is already there. Calling it until the PSBT
    stops changing is what lets the device arrive to a transaction that only
    needs its signature.
    """
    for _ in range(3):
        after = j("walletprocesspsbt", psbt, "true", "DEFAULT", "true",
                  wallet="da")["psbt"]
        if after == psbt:
            return psbt
        psbt = after
    return psbt


def fund_one(d_watch):
    """Pay the wallet, and hand back the coin to spend and where to send it."""
    addr = j("deriveaddresses", d_watch, json.dumps([0, 0]))[0]
    maddr = cli("getnewaddress", wallet="miner")
    while float(j("getbalances", wallet="miner")["mine"]["trusted"]) < 1:
        cli("generatetoaddress", 101, maddr)
    txid = cli("sendtoaddress", addr, "0.5", wallet="miner")
    cli("generatetoaddress", 1, maddr)
    rawtx = j("gettransaction", txid, "true", "true", wallet="miner")["hex"]
    out = [o for o in j("decoderawtransaction", rawtx)["vout"]
           if o["scriptPubKey"].get("address") == addr][0]
    dest = cli("getnewaddress", "", "bech32m", wallet="miner")
    script = j("getaddressinfo", dest, wallet="miner")["scriptPubKey"]
    utxo = {"txid": txid, "vout": out["n"], "value": int(round(out["value"] * 1e8))}
    return addr, script, utxo, 49990000


def type_pin(sim, since):
    """Answer whichever PIN prompt is next.

    The keyboard opens on the first key of the lowercase set, so four presses
    type four of that character and KEY3 saves. Four is the shortest PIN a
    Satochip accepts, and which character it is does not matter as long as every
    prompt gets the same one. Lifted from test_cards_seed.py, which is where
    this sequence is already proven.
    """
    sim.wait_screen("SeedAddPassphraseScreen", since=since, timeout=120)
    mark = sim.mark()
    sim.select(4)
    sim.key3()
    return mark


def save_seed_to_card(sim, shot):
    """Put the seed on the simulated SeedKeeper.

    Not decoration. The nonce vault lives on the card: only a card can promise a
    secret nonce is released once, so only a card-backed session leaves spare
    nonces behind, and without those there is no pool and no one-trip signing.
    A seed the card is not holding gets the Wrong Card screen and the in-memory
    session, which is the two-trip path this demo exists to beat.

    The sequence is test_cards_seedkeeper.py's, which is where it is proven.
    """
    since = sim.mark()
    sim.wait_screen("SeedOptionsScreen", since=since, timeout=180)
    since = sim.mark()
    sim.down(3)                  # scan psbt, xpub, explorer -> [backup]
    sim.select()
    sim.wait_screen("ButtonListScreen", since=since, timeout=120)
    since = sim.mark()
    sim.down()                   # view words -> [to SeedKeeper]
    sim.select()

    # A blank card is asked for its PIN, told it has none, then given one twice
    # over. Upstream's order, not ours.
    since = type_pin(sim, since)
    sim.wait_screen("WarningScreen", since=since, timeout=180)
    since = sim.mark()
    sim.select()
    since = type_pin(sim, since)          # new PIN
    since = type_pin(sim, since)          # and again, to confirm
    sim.wait_screen("LargeIconStatusScreen", since=since, timeout=120)
    since = sim.mark()
    shot("02-card-initialised")
    sim.select()

    # The label the secret is filed under. The keyboard opens with the seed's own
    # fingerprint already in it, so KEY3 accepts what the wallet offered.
    sim.wait_screen("SeedAddPassphraseScreen", since=since, timeout=120)
    sim.key3()
    time.sleep(2.0)
    shot("03-seed-on-card")
    sim.back_to_home()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/home/rob/.cache/tmp/musig-shots")
    ap.add_argument("--headless", default="1")
    args = ap.parse_args()
    os.makedirs(args.shots, exist_ok=True)
    shot = lambda name: sim.screen_png(os.path.join(args.shots, name))

    from simdrive import Sim
    from sp_psbt import specter_frames
    from accept import read_ur_psbt

    print("== coordinator")
    d_watch = coordinator_setup()

    sim = Sim(headless=args.headless == "1", url=URL)
    sim.start()
    try:
        sim.open(timeout=300)
        loaded = sim.page.evaluate("() => window.__firmware")
        print("== simulator: firmware =", loaded)
        assert loaded == "doomsigner-musig", "the demo firmware did not load"

        print("== loading the seed")
        since = sim.mark()
        sim.select()                                   # Home -> Scan
        sim.wait_screen("ScanScreen", since=since, timeout=60)
        sim.scan_qr_frames([SEEDQR_B], expect_screen="SeedFinalizeScreen",
                           since=since, timeout=120)
        shot("01-seed")
        sim.select()                                   # Finalize
        time.sleep(1.5)

        print("== putting the seed on the SeedKeeper")
        save_seed_to_card(sim, shot)

        def device_pass(psbt_b64, tag):
            """One trip to the device: PSBT in as QR, PSBT back off the screen."""
            since = sim.mark()
            sim.select()
            sim.wait_screen("ScanScreen", since=since, timeout=60)
            sim.scan_qr_frames(specter_frames(psbt_b64), since=since, timeout=300,
                               expect_screen=None)
            time.sleep(1.5)
            walked = 0
            seen = []
            while walked < 30:
                # Shoot first and read the name afterwards. The canvas runs
                # ahead of the console line current_screen() reads, so deciding
                # anything on the name read before the shot acts one screen
                # late: that is what pressed Continue on the QR screen and
                # dismissed it back to the home menu.
                tmp = os.path.join(args.shots, "_tmp")
                sim.screen_png(tmp)
                label = sim.current_screen() or ""
                if label != (seen[-1] if seen else None):
                    seen.append(label)
                    os.replace(tmp + ".png", os.path.join(
                        args.shots, "%s-%02d-%s.png" % (tag, walked, label)))
                else:
                    os.remove(tmp + ".png")
                if label == "QRDisplayScreen":
                    break
                if label in ("ScanScreen", "MainMenuScreen") and walked:
                    raise AssertionError(
                        "the walk went past the QR screen and landed on %s: %s"
                        % (label, " ".join(seen)))
                if label == "SeedAddPassphraseScreen":
                    # The card's PIN keyboard, reached by accepting the Card
                    # Signing offer on the screen before. Pressing Continue
                    # through it types nothing, the card never opens, and
                    # signing falls back to the in-memory session.
                    sim.select(4)
                    sim.key3()
                    # The card takes a moment. Pressing on a stale screen name
                    # is what walks past the QR screen and into Scan.
                    for _ in range(20):
                        time.sleep(0.5)
                        if sim.current_screen() != "SeedAddPassphraseScreen":
                            break
                else:
                    sim.select()
                walked += 1
                time.sleep(1.3)
            print("   screens:", " ".join(seen))

            # SeedSigner opens its QR at a low brightness, and jsQR cannot read
            # dark grey on black. The screen offers Brighter for exactly this,
            # so use it rather than reaching into the canvas.
            sim.up(6)
            time.sleep(0.8)
            back = read_ur_psbt(sim, timeout=300)
            sim.back_to_home()
            return back

        def spend(label, tag, pool):
            """One whole spend, and the trips it cost.

            The decisions are the coordinator's: what to build, what to put in,
            what came back, whether it is finished. This drives Core and the
            device and counts.
            """
            print("\n== %s" % label)
            addr, dest, utxo, amount = fund_one(d_watch)
            state = coordinator.spend_start({
                "descriptor": d_watch.split("#")[0], "branch": 0, "index": 0,
                "utxo": utxo, "destination": dest, "amount": amount, "pool": pool,
            })
            for one in state["issued"]:
                print("   pooled nonce   : %s.. for %s.." %
                      (one["id"][:16], one["participant"][:12]))
            for who in state["waiting_for"]:
                print("   no spare for   : %s.. (that signer costs two trips)" % who[:12])

            psbt = core_rounds(state["psbt"])

            trips = 0
            used = False
            while not state.get("done"):
                trips += 1
                print("   trip %d to the device" % trips)
                psbt = device_pass(psbt, "%s-t%d" % (tag, trips))
                state = coordinator.spend_returned(state, psbt)
                for who in state["verified"]:
                    print("   VERIFIED       : %s.. published the pooled nonce" % who[:12])
                used = used or bool(state["verified"])
                for miss in state["ignored"]:
                    raise AssertionError("%s.. ignored its pooled nonce: %s"
                                         % (miss["participant"][:12], miss["why"]))
                print("   pool now       : %s" % ", ".join(
                    "%s..=%d" % (k[:12], len(v)) for k, v in state["pool"].items()))
                if not state.get("done"):
                    psbt = core_rounds(state["psbt"])
                    # Whoever signs last finishes it. The device does that when
                    # it is last; when Core is, asking the device again is a
                    # trip for nothing and it refuses an input already signed.
                    fin = j("finalizepsbt", psbt)
                    if fin["complete"]:
                        state["done"] = True
                        state["txhex"] = fin["hex"]
                assert trips < 4, "the device was visited %d times" % trips

            txid = cli("sendrawtransaction", state["txhex"])
            d = j("decoderawtransaction", state["txhex"])
            wit = d["vin"][0]["txinwitness"]
            assert len(wit) == 1 and len(wit[0]) // 2 == 64
            print("   BROADCAST      :", txid)
            print("   witness        : one %d-byte signature, vsize %d"
                  % (len(wit[0]) // 2, d["vsize"]))
            print("   TRIPS          : %d" % trips)
            cli("generatetoaddress", 1, cli("getnewaddress", wallet="miner"))
            return trips, txid, state["pool"], used

        first, txid1, pool, _ = spend("SPEND ONE: no pool yet", "s1", {})
        second, txid2, pool, verified = spend("SPEND TWO: the pool is stocked",
                                              "s2", pool)

        print("\n" + "=" * 62)
        print("spend one  %d trip%s   %s" % (first, "" if first == 1 else "s", txid1))
        print("spend two  %d trip%s   %s" % (second, "" if second == 1 else "s", txid2))
        assert verified, ("spend two did not use its pooled nonce, so the "
                          "coordinator wrote a field the device ignored")
        print("\nDONE: the device signed with a nonce made before this")
        print("transaction existed, and published that exact nonce.")
        # The trip count does not fall here, and saying so is the point. Core is
        # the other signer and publishes its nonce the moment the PSBT is built,
        # so the device never waits for anybody and one trip is all it ever
        # needed. The saving appears with two air-gapped signers, which is four
        # trips without a pool and two with one.
        print("Trips did not fall: the other signer is Core, which never makes")
        print("the device wait. Two devices is what turns four trips into two.")

    finally:
        sim.stop()


if __name__ == "__main__":
    main()
