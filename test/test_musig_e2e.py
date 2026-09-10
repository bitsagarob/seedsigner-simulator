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
# The panel says "Ready", not a fingerprint: eight characters of hex mean
# nothing to a reader who has not been told what a fingerprint is. It keeps the
# fingerprint on hover, which is what this reads.
COSIGNERS = """
() => Array.from(document.querySelectorAll("#wallet .wal-cosigner-fp"))
        .map((e) => (String(e.title).match(/[0-9a-f]{8}/) || [""])[0])
        .filter((s) => s)
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
        # What the device handed back, so the spare nonces in it can be counted.
        open("/tmp/musig-after-first.psbt", "w").write(
            page.evaluate("() => self.WalletCoordinator.current.musig.psbt || ''"))
        card_notes(sim)
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
        # Written whatever happened. A run that ends in a device traceback is
        # the one whose log is worth most, and it was the only one not keeping
        # it.
        dump_console(sim)
        sim.stop()


def dump_console(sim):
    """Every line the device printed, to a file, unsampled."""
    try:
        path = os.path.join(SHOTS, "device-console.log")
        with open(path, "w") as fh:
            fh.write("\n".join(sim.console))
        print("  full device console: %s (%d lines)" % (path, len(sim.console)),
              flush=True)
    except Exception as why:
        print("  could not save the device console: %s" % why, flush=True)


def card_notes(sim):
    """What the device said about the card and its spare nonces.

    The pool is the point of this demo, and only a card-backed signing restocks
    it, so whether one was in use is worth saying out loud rather than reading
    off a trip count.
    """
    wanted = ("musig2:", "Card", "card", "restock", "satochip", "Satochip")
    seen = [l for l in sim.console if any(w in l for w in wanted)]
    print("  device on cards: %d lines" % len(seen), flush=True)
    for line in seen:
        print("   ", line.strip(), flush=True)
    dump_console(sim)


def spend(sim, page, label):
    """Send it back, answering the device on every trip it takes.

    The panel keeps the last transaction it sent, so a spend is finished when a
    *different* one appears. Taking any value at all made the second spend
    report the first one's transaction and no trips at all.
    """
    before = page.evaluate(STATE)["sent"]
    press(page, "Spend the balance")
    seen = 0
    said = None
    dumped = False
    waited = 0
    for _ in range(900):
        state = page.evaluate(STATE)
        if state["error"]:
            raise AssertionError("the panel said: %s\nthe device was on %s, "
                                 "and its last words:\n  %s"
                                 % (state["error"], sim.current_screen(),
                                    "\n  ".join(sim.console[-250:])))
        if state["sent"] and state["sent"] != before:
            print("%s spend broadcast %s" % (label, state["sent"]))
            shot(page, label + "-sent")
            return state
        if state["trips"] == 5 and not dumped:
            # Five trips means it is going round. Keep what the device handed
            # back so the signatures in it can be counted away from the browser.
            dumped = True
            psbt = page.evaluate(
                "() => self.WalletCoordinator.current.musig.psbt || ''")
            open("/tmp/musig-stuck.psbt", "w").write(psbt)
            print("  wrote /tmp/musig-stuck.psbt (%d chars)" % len(psbt), flush=True)
        if state["trips"] > seen:
            seen = state["trips"]
            round_ = ""
            for line in reversed(sim.console):
                if "PSBTMusig2Round:" in line:
                    round_ = line.split("PSBTMusig2Round:", 1)[1].strip()
                    break
            print("  %s spend, trip %d  seed=%d  used=%s spares=%s  device: %s"
                  % (label, seen, (seen - 1) % 2, state["used"], state["spares"],
                     round_ or "(no round yet)"), flush=True)
            # Alternate the two signers. A cold MuSig2 spend costs four
            # visits -- a nonce from each, then a signature from each -- so the
            # seeds go A, B, A, B. Sending the same one twice leaves the other
            # participant with nothing in the round and it never closes.
            answer_device(sim, page, seed=(seen - 1) % 2)
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


FRAMES = """
() => {
  const w = self.WalletCoordinator.current;
  if (!w.musig || !w.musig.psbt) return null;
  return self.WalletTutorial.specterFrames(w.musig.psbt, 280);
}
"""

CAMERA = """
() => {
  const w = self.WalletCoordinator.current;
  return {presenting: !!w.presenting,
          ownsCamera: !!self.WalletCoordinator.cameraStream(),
          canvasOnPage: document.body.contains(w.canvas),
          hidden: w.canvas.hidden,
          width: w.canvas.width, height: w.canvas.height};
}
"""


def answer_device(sim, page=None, seed=0):
    """Show the transaction to the device and bring its answer back.

    The card in the reader has to be the one holding the seed that signs this
    trip. Leaving whichever card was last used means the device finds a card
    that does not carry this seed, quietly signs from memory instead, and
    leaves no spare nonces behind -- which is the whole point of the pool.
    """
    if CARDS and page is not None and seed < 2:
        pick_card(page, seed)
    since = sim.mark()
    sim.back_to_home()
    sim.select()
    sim.wait_screen("ScanScreen", since=since, timeout=60)
    # Hold the panel's own frames up to the device, the same way every other
    # scan in this harness is fed. The panel is presenting them on its canvas
    # at the same time and the page hands that canvas to the device as its
    # camera, but that optical path does not deliver here, so the frames the
    # panel built are shown through the harness instead. What is being signed
    # is still the panel's transaction, byte for byte.
    frames = page.evaluate(FRAMES) if page is not None else None
    if not frames:
        raise AssertionError("the panel is not holding a transaction up")
    if not show_until_taken(sim, frames):
        raise AssertionError("the device never took the transaction, sat on %s"
                             % sim.current_screen())
    walk_to_qr(sim, seed=seed)


def show_until_taken(sim, frames, timeout=300):
    """Cycle the frames until the device stops scanning.

    Not scan_qr_frames with a screen to wait for: which screen a device lands on
    after taking a transaction depends on what it wants to ask, and naming one
    of them made a run that had worked look like one that failed. Leaving Scan
    is the thing that means it has the transaction.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for frame in frames:
            sim.show_qr(frame)
            time.sleep(0.55)
            if sim.current_screen() not in (None, "ScanScreen"):
                sim.clear_camera()
                return True
    sim.clear_camera()
    return False


def current_view(sim):
    """The view the device is in, from its own narration.

    Screen names are shared: SeedAddPassphraseScreen is the keyboard for a
    passphrase, a WIF and a BIP38 key alike, so pressing by screen name is
    guessing. The view says which one it is.
    """
    # The whole narration, not a window of it: rendering a code logs hundreds
    # of lines, so a fixed lookback loses the "entered this view" line and the
    # walk goes blind exactly when it matters.
    for line in reversed(sim.console):
        if "View.run enter: " in line:
            return line.split("View.run enter: ", 1)[1].strip()
    return None


def walk_to_qr(sim, seed=0):
    """Press through the review until the signed code is up.

    OPEN, and not this walk's doing. QRDisplayScreen returns as soon as it has
    started its display thread -- enter, thread start, "exit -> None", with no
    key and no "wait_for keys" line. That is normal here: the xpub export does
    exactly the same and the panel reads it fine, because what is painted stays
    on the screen until the next view paints over it.

    The difference is how long that lasts and how much has to be read. An xpub
    is one code and the view that follows takes its time. A signed transaction
    is a ur: sequence the panel has to collect several frames of, and the view
    that follows is MainMenuView, which paints at once. So the code is gone
    before enough of it has been seen, and the panel reports that nothing
    arrived while waiting for the signature.
    """
    for _ in range(60):
        screen = sim.current_screen()
        view = current_view(sim)
        if view == "MainMenuView":
            # Home means the signing flow ended without producing a code.
            # Pressing on from here walks into Power options and restarts the
            # device, which buries whatever actually went wrong.
            break
        if screen == "ScanScreen":
            # Still reading. Pressing here does nothing but waste the budget.
            time.sleep(1)
            continue
        if screen == "QRDisplayScreen" or view == "PSBTSignedQRDisplayView":
            # The device is holding its answer up: touch nothing. up() brightens
            # the xpub screen, but on the signed transaction it walks off the
            # code and home, and the panel is then left waiting for a signature
            # that is no longer on screen.
            time.sleep(1.5)
            return
        if (view == "PSBTMusig2CardOfferView"
                and screen == "SeedAddPassphraseScreen" and CARDS):
            # The card is in the reader, so this PIN can be answered: four of
            # whichever key the keyboard opened on, then KEY3, the same dance
            # the card save uses.
            print("    card PIN", flush=True)
            sim.select(4)
            sim.key3()
            for _ in range(40):
                time.sleep(0.5)
                if current_view(sim) != view:
                    break
            continue
        if view == "PSBTMusig2CardOfferView" and screen == "SeedAddPassphraseScreen":
            # "Use Card" was taken and init_satochip is asking for a PIN. With
            # no card in the reader nothing can answer it, and pressing only
            # types into the keyboard, so this is a dead end rather than a slow
            # step. Said once, plainly, instead of spinning here.
            raise AssertionError(
                "the device is asking for a card PIN, so the card offer was "
                "answered \"Use Card\". \"Keep Device On\" is the one this "
                "run needs, and KEY_DOWN is not the problem: the device "
                "accepts it. The offer is answered before it arrives. Its own "
                "log reads: LargeIconStatusScreen enters, a KEY_PRESS is "
                "accepted, the screen exits with 0, and only then is KEY_DOWN "
                "accepted. So a press from the screen before this one is "
                "landing here. Stop that leaking press, or run with "
                "MUSIG_E2E_CARDS=1 once a card in the reader stops pegging "
                "the page.\n\nthe device's last words:\n  %s"
                % "\n  ".join(sim.console[-120:]))
        if view == "PSBTMusig2CardOfferView":
            # Where the half-finished signing should live: "Use Card" first,
            # "Keep Device On" second.
            print("    press on %s / %s" % (view, screen), flush=True)
            # Let the screen's input loop start before sending anything. Keys
            # sent while it is still rendering appear to go nowhere, which is
            # the difference between "the highlight will not move" and "the
            # move was never seen". The device logs which button it exits with,
            # so this is checkable rather than a matter of opinion.
            time.sleep(3)
            if sim.current_screen() != screen:
                # A press aimed at the screen before this one arrives late and
                # answers the offer on its own. Pressing again then types into
                # whatever opened next, and what opens next is the PIN keyboard,
                # so the PIN came out one letter too long and the card refused
                # it. If the offer is already answered, leave it answered.
                continue
            if not CARDS:
                # "Keep Device On" is the second button, and without a card in
                # the reader it is the only one that can be answered.
                sim.down()
                time.sleep(1.2)
            sim.select()
            # Wait for it to land, like every other press.
            for _ in range(120):
                time.sleep(0.5)
                if current_view(sim) != view:
                    break
            continue
        if view == "PSBTSelectSeedView":
            # The seeds are the first buttons; everything below them is a way
            # of entering a key by hand. Pressing blind walks down into "Enter
            # WIF", whose keyboard is the same screen a passphrase uses, and
            # the run then goes round invalid-key warnings for ever.
            # Which cosigner signs this trip. The seeds are the first buttons,
            # in the order they were loaded, and a 2-of-3 needs two different
            # ones: always taking the first left one participant signing over
            # and over while the round never closed. No scrolling past them, a
            # ButtonListScreen wraps and going too far lands anywhere.
            if seed:
                sim.down(seed)
                time.sleep(0.8)
            sim.select()
            time.sleep(1.5)
            continue
        # One press, then wait a full minute for it to land. Pressing again
        # because a slow screen has not moved yet leaves the extra key in the
        # queue, and it is spent dismissing whatever comes next: the walk saw
        # two presses on the overview and lost the signed code to the second.
        print("    press on %s / %s" % (view, screen), flush=True)
        sim.select()
        for _ in range(120):
            time.sleep(0.5)
            if current_view(sim) != view:
                break
    raise AssertionError("no signed code, sat on %s\nthe device's last words:\n  %s"
                         % (sim.current_screen(),
                            "\n  ".join(sim.console[-250:])))


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


READER = """
() => {
  const live = document.querySelector(".cardtray--live");
  const on = document.querySelector(".cardtray-card[aria-pressed=true]");
  const all = Array.from(document.querySelectorAll(".cardtray-card"));
  return {reader: !!live, holding: on ? all.indexOf(on) : -1, cards: all.length};
}
"""


def pick_card(page, index):
    """Put this cosigner's own card in the reader, and check that it went in.

    Clicking the tray and walking on was enough during setup and not during a
    spend, where the click landed on nothing and the device then found no card,
    signed from memory and left no spare nonces. So this says what the reader
    is holding rather than assuming the click worked.
    """
    page.wait_for_selector(".cardtray-card", timeout=60000)
    for _ in range(5):
        state = page.evaluate(READER)
        # Selected is not inserted: the reader has to be live too, or the
        # device looks for a card, finds none, and signs from memory.
        if state["holding"] == index and state["reader"]:
            return
        page.evaluate(
            """(i) => document.querySelectorAll(".cardtray-card")[i].click()""",
            index)
        time.sleep(1.5)
    raise AssertionError("card %d would not go into the reader: %s"
                         % (index, page.evaluate(READER)))


def reached(sim, screen, since, timeout=45):
    """Did this screen come up? For a step the device only sometimes takes."""
    try:
        sim.wait_screen(screen, since=since, timeout=timeout)
        return True
    except Exception:
        return False


def said(sim, pattern, since, timeout=45):
    """Did the device print this? For a step it only sometimes takes."""
    try:
        sim.wait_console(pattern, since=since, timeout=timeout)
        return True
    except Exception:
        return False


def save_to_card(sim):
    """From the seed's menu: backup -> to SeedKeeper, with the PIN dance.

    A blank card wants a PIN set on it: a warning, then the new PIN twice. A
    card that has been used already just verifies the one it has, and says so
    with "Pin Correct" and no warning at all. Which of the two happens depends
    on the card in the reader, so this waits to see rather than assuming.
    """
    since = sim.mark()
    sim.down(3); sim.select()                      # backup
    sim.wait_screen("ButtonListScreen", since=since, timeout=60)
    since = sim.mark()
    sim.down(); sim.select()                       # to SeedKeeper
    # Only when the device actually asks. With the smartcard session kept
    # across Home it asks once and then reuses the card, and the keyboard the
    # PIN would have been typed into is the label keyboard further down. Typing
    # there saved the seed under the PIN and left the run waiting for a prompt
    # that had already been answered.
    if said(sim, r"prompting for", since):
        since = type_pin(sim, since)
    if reached(sim, "WarningScreen", since):
        since = sim.mark()
        sim.select()
        since = type_pin(sim, since)               # new PIN
        since = type_pin(sim, since)               # again
        sim.wait_screen("LargeIconStatusScreen", since=since, timeout=120)
        since = sim.mark()
        sim.select()
    # The label keyboard, which is always asked for, unlike the PIN.
    sim.wait_screen("SeedAddPassphraseScreen", since=since, timeout=180)
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
