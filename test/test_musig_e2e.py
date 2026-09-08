#!/usr/bin/env python3
"""A MuSig2 2-of-3, created and spent twice, entirely in the browser.

Nothing outside the page: no Bitcoin Core, no node. Three seeds live on the
simulated device, two of them with their own SeedKeeper, and the coordinator in
the page builds the wallet, takes money from the faucet and spends it back.

The point is the last two numbers it prints. The first spend pays what MuSig2
costs air-gapped; the second spends the nonces the device left behind, and
should cost fewer trips. A device that ignored a pooled nonce would still
produce a valid transaction, so the run also requires the coordinator to have
matched the nonce on chain against the one it issued.

    python3 test/test_musig_e2e.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")
sys.path.insert(0, HERE)

# The one browser origin the signet API allows, served from this working tree.
URL = ("https://bitsaga.be/wallet.html"
       "?firmware=doomsigner-musig&debug=1&wallet=1")
LOCAL = 8792
SHOTS = "/home/rob/.cache/tmp/musig-e2e"

# The published BIP39 test vectors, as the digits a SeedQR carries. The first
# two get a card each and are the pair that spends through the key path; the
# third is the fallback cosigner and never signs here.
SEEDS = [
    ("cosigner 1", "000000000000000000000000000000000000000000000003", True),
    ("cosigner 2", "204720472047204720472047204720472047204720472037", True),
    ("cosigner 3", "101920151790203919831533203119191019201517902040", False),
]

CLICK = """
(label) => {
  const b = Array.from(document.querySelectorAll("#wallet button"))
    .find((x) => x.textContent.trim() === label);
  if (!b) return false;
  b.click();
  return true;
}
"""
BUTTONS = """
() => Array.from(document.querySelectorAll("#wallet button"))
        .map((b) => b.textContent.trim())
"""
COSIGNERS = """
() => Array.from(document.querySelectorAll("#wallet .wal-cosigner-fp"))
        .map((e) => e.textContent.trim()).filter((s) => /^[0-9a-f]{8}$/.test(s))
"""
STATE = """
() => {
  const w = self.WalletCoordinator.current;
  const m = w.musig || {};
  return {address: m.address || "", total: m.total || 0, trips: m.trips || 0,
          used: m.used || 0, spares: m.spares || 0, sent: m.sent || "",
          busy: m.busy || "", error: w.error || ""};
}
"""

shots = 0


def shot(page, name):
    global shots
    shots += 1
    page.locator("#wallet").screenshot(
        path=os.path.join(SHOTS, "%02d-%s.png" % (shots, name)))


def main():
    from simdrive import Sim
    from signet_bridge import serve_site_at_real_origin

    os.makedirs(SHOTS, exist_ok=True)
    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        serve_site_at_real_origin(sim.page.context, LOCAL)
        sim.open(timeout=300)
        page = sim.page

        for at, (name, digits, with_card) in enumerate(SEEDS):
            load_seed(sim, digits)
            if with_card:
                pick_card(page, at)
                save_to_card(sim)
            export_key(sim)
            print("%s exported%s" % (name, " (card %d)" % (at + 1) if with_card else ""))
            if at == 0:
                open_panel(page)
                enter_musig(page)
                shot(page, "cosigner-1")
            wait_until(lambda: len(page.evaluate(COSIGNERS)) >= at + 1,
                       "cosigner %d in the panel" % (at + 1))

        print("cosigners:", ", ".join(page.evaluate(COSIGNERS)))
        shot(page, "cosigners")

        page.evaluate(CLICK, "Create the wallet")
        wait_until(lambda: page.evaluate(STATE)["address"], "the wallet")
        state = page.evaluate(STATE)
        print("address:", state["address"])
        shot(page, "wallet")

        page.evaluate(CLICK, "Get test bitcoin")
        wait_until(lambda: page.evaluate(STATE)["total"], "the faucet", 240)
        print("funded:", page.evaluate(STATE)["total"], "sats")
        shot(page, "funded")

        first = spend(sim, page, "first")
        second = spend(sim, page, "second")

        print("\n" + "=" * 58)
        print("first spend   %d trips" % first["trips"])
        print("second spend  %d trips, %d with a nonce made in advance"
              % (second["trips"], second["used"]))
        ok = second["used"] > 0
        print("\n%s" % ("the second spend used a nonce made before it existed"
                        if ok else "THE POOLED NONCE WAS NOT USED"))
        return 0 if ok else 1
    finally:
        sim.stop()


def spend(sim, page, label):
    """Send it back, answering the device on every trip it takes."""
    page.evaluate(CLICK, "Send it back to the faucet")
    seen = 0
    for _ in range(900):
        state = page.evaluate(STATE)
        if state["error"]:
            raise AssertionError("the panel said: " + state["error"])
        if state["sent"]:
            print("%s spend broadcast %s" % (label, state["sent"]))
            shot(page, label + "-sent")
            return state
        if state["trips"] > seen:
            seen = state["trips"]
            print("  %s spend, trip %d" % (label, seen))
            answer_device(sim)
        time.sleep(1)
    raise AssertionError("the %s spend never finished" % label)


def answer_device(sim):
    """Scan what the panel is showing, walk the review, hand the answer back."""
    since = sim.mark()
    sim.back_to_home()
    sim.select()
    sim.wait_screen("ScanScreen", since=since, timeout=60)
    # The panel is already presenting; the device's camera reads the canvas.
    for _ in range(40):
        if sim.current_screen() not in (None, "ScanScreen"):
            break
        time.sleep(1)
    walk_to_qr(sim)


def walk_to_qr(sim):
    """Press through the review until the signed code is up."""
    for _ in range(30):
        screen = sim.current_screen()
        if screen == "QRDisplayScreen":
            sim.up(6)
            time.sleep(1.5)
            return
        if screen == "SeedAddPassphraseScreen":
            sim.select(4)
            sim.key3()
            for _ in range(20):
                time.sleep(0.5)
                if sim.current_screen() != "SeedAddPassphraseScreen":
                    break
            continue
        sim.select()
        time.sleep(1.3)
    raise AssertionError("no signed code, sat on %s" % sim.current_screen())


def load_seed(sim, digits):
    since = sim.mark()
    sim.back_to_home()
    sim.select()
    sim.wait_screen("ScanScreen", since=since, timeout=60)
    sim.scan_qr_frames([digits], expect_screen="SeedFinalizeScreen",
                       since=since, timeout=240)
    time.sleep(1.5)


def pick_card(page, index):
    """Put this cosigner's own card in the reader."""
    page.wait_for_selector(".cardtray-card", timeout=60000)
    page.locator(".cardtray-card").nth(index).click()
    time.sleep(1)


def save_to_card(sim):
    """Seed -> backup -> to SeedKeeper, with the blank card's PIN dance."""
    since = sim.mark()
    sim.select()                                   # Done
    sim.wait_screen("SeedOptionsScreen", since=since, timeout=60)
    since = sim.mark()
    sim.down(3); sim.select()                      # backup
    sim.wait_screen("ButtonListScreen", since=since, timeout=60)
    since = sim.mark()
    sim.down(); sim.select()                       # to SeedKeeper
    since = type_pin(sim, since)
    sim.wait_screen("WarningScreen", since=since, timeout=180)
    since = sim.mark()
    sim.select()
    since = type_pin(sim, since)                   # new PIN
    since = type_pin(sim, since)                   # again
    sim.wait_screen("LargeIconStatusScreen", since=since, timeout=120)
    since = sim.mark()
    sim.select()
    sim.wait_screen("SeedAddPassphraseScreen", since=since, timeout=120)
    sim.key3()                                     # accept the offered label
    time.sleep(2)
    sim.back_to_home()


def type_pin(sim, since):
    """Four of whichever key the keyboard opened on, then KEY3."""
    sim.wait_screen("SeedAddPassphraseScreen", since=since, timeout=120)
    mark = sim.mark()
    sim.select(4)
    sim.key3()
    return mark


def export_key(sim):
    """Seeds -> the seed -> Export Xpub -> Single sig -> Native Segwit -> Static."""
    since = sim.mark()
    sim.back_to_home()
    sim.down(2); sim.select()                      # Seeds
    sim.wait_screen("ButtonListScreen", since=since, timeout=60)
    sim.select(); time.sleep(1.2)                  # the seed just loaded
    since = sim.mark()
    sim.down(); sim.select()                       # Export Xpub
    sim.wait_screen("ButtonListScreen", since=since, timeout=60)
    sim.select(); time.sleep(1.2)                  # Single sig
    sim.select(); time.sleep(1.2)                  # Native Segwit
    since = sim.mark()
    sim.down(); sim.select()                       # Static
    for _ in range(8):
        try:
            sim.wait_screen("QRDisplayScreen", since=since, timeout=4)
            break
        except Exception:
            sim.select()
    else:
        raise AssertionError("no export code, sat on %s" % sim.current_screen())
    sim.up(6)
    time.sleep(1.5)


def open_panel(page):
    page.wait_for_selector(".wal-openrow button", timeout=120000)
    page.locator(".wal-openrow button").first.click()
    page.wait_for_selector("#wallet:not([hidden])", timeout=30000)


def enter_musig(page):
    for _ in range(120):
        time.sleep(1)
        names = page.evaluate(BUTTONS)
        if "Done" in names:
            page.evaluate(CLICK, "Done")
            time.sleep(1)
            names = page.evaluate(BUTTONS)
        if "MuSig2" in names:
            page.evaluate(CLICK, "MuSig2")
            time.sleep(1)
            return
    raise AssertionError("the panel never offered MuSig2: %s" % names)


def wait_until(test, what, seconds=120):
    for _ in range(seconds):
        if test():
            return
        time.sleep(1)
    raise AssertionError("waited for " + what)


if __name__ == "__main__":
    sys.exit(main())
