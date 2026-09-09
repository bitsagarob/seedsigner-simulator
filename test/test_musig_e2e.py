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
import faulthandler
import json
import os
import signal
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

# Each of the first two cosigners keeps its own SeedKeeper. Off by default: a
# card in the reader puts the page's main thread into a spin that never ends,
# which starves every read of the page and looks like a hang rather than a
# failure. Chased separately; the spend is what this test is for.
CARDS = os.environ.get("MUSIG_E2E_CARDS") == "1"

# The published BIP39 test vectors, as the digits a SeedQR carries. The first
# two get a card each and are the pair that spends through the key path; the
# third is the fallback cosigner and never signs here.
SEEDS = [
    ("cosigner 1", "000000000000000000000000000000000000000000000003", True),
    ("cosigner 2", "204720472047204720472047204720472047204720472037", True),
    ("cosigner 3", "101920151790203919831533203119191019201517902040", False),
]

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

# If a spend does not get to its first trip, ask the page to make the same call
# the panel makes and say what happens to it. A promise that never settles
# leaves no trace otherwise.
STUCK = """
async () => {
  const w = self.WalletCoordinator.current;
  const C = self.EmbitCoordinator;
  const out = {haveC: !!C, haveSpendStart: !!(C && C.spendStart),
               coins: (w.musig.coins || []).length,
               descriptor: !!w.musig.descriptor};
  if (!out.haveSpendStart) return out;
  const coin = (w.musig.coins || [])[0];
  const started = performance.now();
  try {
    const state = await Promise.race([
      C.spendStart({descriptor: w.musig.descriptor, branch: 0, index: 0,
                    utxo: {txid: coin.txid, vout: coin.vout, value: coin.value},
                    destination: C.hex(new Uint8Array([0, 20].concat(
                      Array.from({length: 20}, () => 0)))),
                    amount: coin.value - 1000, pool: {}}),
      new Promise((_, no) => setTimeout(() => no(new Error("no answer in 60s")),
                                        60000)),
    ]);
    out.ms = Math.round(performance.now() - started);
    out.waiting = state.waiting_for.length;
  } catch (why) {
    out.ms = Math.round(performance.now() - started);
    out.threw = String(why && why.message);
  }
  return out;
}
"""


shots = 0


def step(sim, what):
    """Say where the run is. A stall that prints nothing is undebuggable."""
    print("  [%s] %s" % (sim.current_screen() or "?", what), flush=True)


def shot(page, name):
    """The whole page, device and coordinator together.

    Not locator("#wallet").screenshot: that waits for the element to hold
    still, and the panel redraws on every read of the device's screen. A
    picture is never worth failing the run over, so a failure is reported and
    stepped over.
    """
    global shots
    shots += 1
    where = os.path.join(SHOTS, "%02d-%s.png" % (shots, name))
    try:
        # animations="allow": the default waits for the page to hold still, and
        # the device paints its screen for ever.
        page.screenshot(path=where, animations="allow", timeout=15000)
    except Exception as why:
        print("  (no picture of %s: %s)" % (name, why), flush=True)


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
            step(sim, "%s: loading the seed" % name)
            load_seed(sim, digits)
            if CARDS and with_card:
                step(sim, "%s: card %d into the reader" % (name, at + 1))
                pick_card(page, at)
            to_seed_options(sim)
            if CARDS and with_card:
                step(sim, "%s: saving to the card" % name)
                save_to_card(sim)
                to_seed_options(sim)
            step(sim, "%s: exporting the key" % name)
            export_key(sim)
            print("%s exported%s"
                  % (name, " (card %d)" % (at + 1) if CARDS and with_card else ""))
            if at == 0:
                open_panel(page)
                enter_musig(page)
                shot(page, "cosigner-1")
            wait_until(lambda: len(page.evaluate(COSIGNERS)) >= at + 1,
                       "cosigner %d in the panel" % (at + 1))

        print("cosigners:", ", ".join(page.evaluate(COSIGNERS)))
        shot(page, "cosigners")

        press(page, "Create the wallet")
        wait_until(lambda: page.evaluate(STATE)["address"], "the wallet")
        state = page.evaluate(STATE)
        print("address:", state["address"])
        shot(page, "wallet")

        fund(page)
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
    press(page, "Spend it back into the wallet")
    seen = 0
    said = None
    waited = 0
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
            print("  %s spend, trip %d" % (label, seen), flush=True)
            answer_device(sim)
        if said != state["busy"]:
            said = state["busy"]
            print("  [%s] %s" % (sim.current_screen(), said or "(nothing)"),
                  flush=True)
        waited += 1
        if waited == 45 and not state["trips"]:
            print("  no trip yet; asking the page directly: %s"
                  % page.evaluate(STUCK), flush=True)
        time.sleep(1)
    raise AssertionError("the %s spend never finished" % label)


def answer_device(sim):
    """Scan what the panel is showing, walk the review, hand the answer back."""
    since = sim.mark()
    sim.back_to_home()
    sim.select()
    sim.wait_screen("ScanScreen", since=since, timeout=60)
    # The panel is already presenting; the device's camera reads the canvas. A
    # transaction is many frames and the camera only sees one at a time, so
    # this is minutes rather than seconds.
    for _ in range(300):
        if sim.current_screen() not in (None, "ScanScreen"):
            break
        time.sleep(1)
    else:
        raise AssertionError("the device never finished reading the transaction")
    walk_to_qr(sim)


def walk_to_qr(sim):
    """Press through the review until the signed code is up."""
    for _ in range(60):
        screen = sim.current_screen()
        if screen == "ScanScreen":
            # Still reading. Pressing here does nothing but waste the budget.
            time.sleep(1)
            continue
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


def to_seed_options(sim):
    """Reach the seed's own menu, pressing back past whatever is in front of it.

    Never through the main menu: its second item is Tools, not Seeds, and one
    press too many there opens the entropy camera and stays there.
    """
    for _ in range(10):
        if sim.current_screen() == "SeedOptionsScreen":
            return
        since = sim.mark()
        if sim.current_screen() == "SeedFinalizeScreen":
            sim.select()                           # Done
        else:
            sim.key1()                             # back
        try:
            sim.wait_screen("SeedOptionsScreen", since=since, timeout=6)
            return
        except Exception:
            continue
    raise AssertionError("could not reach the seed's menu from %s"
                         % sim.current_screen())


def pick_card(page, index):
    """Put this cosigner's own card in the reader."""
    page.wait_for_selector(".cardtray-card", timeout=60000)
    page.locator(".cardtray-card").nth(index).click()
    time.sleep(1)


def save_to_card(sim):
    """From the seed's menu: backup -> to SeedKeeper, with the PIN dance."""
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
    time.sleep(2.5)


def type_pin(sim, since):
    """Four of whichever key the keyboard opened on, then KEY3."""
    sim.wait_screen("SeedAddPassphraseScreen", since=since, timeout=120)
    mark = sim.mark()
    sim.select(4)
    sim.key3()
    return mark


def export_key(sim):
    """From the seed's menu: Export Xpub -> Single sig -> Native Segwit -> Static."""
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
    print("  ... waiting for the panel's open button", flush=True)
    page.wait_for_selector(".wal-openrow button", timeout=120000)
    print("  ... clicking it", flush=True)
    page.locator(".wal-openrow button").first.click()
    page.wait_for_selector("#wallet:not([hidden])", timeout=30000)
    print("  ... the panel is open", flush=True)


PRESS = """
(label) => {
  const button = Array.from(document.querySelectorAll("#wallet button"))
    .find((b) => b.textContent.trim() === label);
  if (!button) return false;
  // On a timer, so this call returns before the handler runs. A button here
  // starts a read loop that watches the device, and neither way of pressing it
  // from outside survives that: a real click waits for the page to go quiet,
  // which it never does, and clicking inside the evaluate runs the handler in
  // the middle of the call, after which nothing else comes back.
  setTimeout(() => button.click(), 0);
  return true;
}
"""


def press(page, label):
    """Press a button in the panel by what it says."""
    return page.evaluate(PRESS, label)


def enter_musig(page):
    """Into the MuSig2 view, past the verify prompt if there is one.

    The prompt is not always drawn, so its Done button is dismissed when it is
    there and never waited for.
    """
    names = []
    for round_ in range(180):
        time.sleep(1)
        names = page.locator("#wallet button").all_inner_texts()
        names = [one.strip() for one in names]
        print("  ... panel shows %s" % names, flush=True)
        if "MuSig2" in names:
            press(page, "MuSig2")
            time.sleep(1)
            print("  ... in the MuSig2 view", flush=True)
            return
        if "Done" in names:
            press(page, "Done")
    raise AssertionError("the panel never offered MuSig2: %s" % names)


def fund(page):
    """Ask the faucet, and keep asking if the panel did not take the press.

    The button is drawn by the same render that draws the address, so the first
    press can land before it is there. What the panel is doing is printed on
    the way out, because a bare "no money" says nothing about which half failed.
    """
    for attempt in range(5):
        press(page, "Get test bitcoin")
        for _ in range(60):
            time.sleep(1)
            state = page.evaluate(STATE)
            if state["total"]:
                return
            if state["error"]:
                raise AssertionError("the faucet: " + state["error"])
        print("  ... still nothing, %s" % page.evaluate(STATE), flush=True)
    raise AssertionError("no money arrived: %s" % page.evaluate(STATE))


def wait_until(test, what, seconds=120):
    for _ in range(seconds):
        if test():
            return
        time.sleep(1)
    raise AssertionError("waited for " + what)


if __name__ == "__main__":
    # kill -USR1 <pid> prints where this is. A browser test that stops saying
    # anything is otherwise only guessable at.
    faulthandler.register(signal.SIGUSR1)
    sys.exit(main())
