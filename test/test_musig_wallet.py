#!/usr/bin/env python3
"""Build a 2-of-3 MuSig2 wallet in the page, from three seeds on the device.

The panel collects a cosigner every time the device exports a key, so this
loads each published test vector in turn, exports it, and requires the address
the page ends up with to be the one the coordinator computes headlessly for the
same three keys.

    python3 test/test_musig_wallet.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")
sys.path.insert(0, HERE)

# The page at the one browser origin Bitsaga Signet's API allows, served from
# this working tree. Anywhere else and the panel connects and then fails asking
# the chain what it holds.
URL = ("https://bitsaga.be/wallet.html"
       "?firmware=doomsigner&debug=1&wallet=1")
LOCAL = 8792
SHOTS = "/home/rob/.cache/tmp/musig-wallet-shots"

# The three published BIP39 test vectors, as the digits a SeedQR carries.
SEEDS = [
    ("abandon x11 about", "000000000000000000000000000000000000000000000003"),
    ("zoo x11 wrong", "204720472047204720472047204720472047204720472037"),
    ("legal winner ... yellow", "101920151790203919831533203119191019201517902040"),
]

# Clicked through the DOM. A click re-renders the panel, which detaches the
# element Playwright is holding, and it waits for a stability that never comes.
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

CHECK = """
import json, sys
sys.path.insert(0, '/home/rob/apps/_scratch/embit-musig/src')
sys.path.insert(0, '%s/../src/web/extras')
import coordinator
keys = json.load(open('/tmp/musig-wallet-keys.json'))
print(coordinator.musig_address(coordinator.musig_wallet(keys), 0, 0)['address'])
""" % HERE


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

        for at, (name, digits) in enumerate(SEEDS):
            load_and_export(sim, digits)
            print("exported cosigner %d (%s)" % (at + 1, name))
            if at == 0:
                open_panel(page)
                enter_musig(page)
            wait_for(page, COSIGNERS, at + 1,
                     "cosigner %d to reach the panel" % (at + 1))

        fps = page.evaluate(COSIGNERS)
        print("cosigners:", ", ".join(fps))
        page.locator("#wallet").screenshot(path=os.path.join(SHOTS, "cosigners.png"))

        page.evaluate(CLICK, "Create the wallet")
        for _ in range(60):
            time.sleep(1)
            shown = page.locator("#wallet .wal-mono").all_inner_texts()
            if shown:
                break
        page.locator("#wallet").screenshot(path=os.path.join(SHOTS, "wallet.png"))

        keys = page.evaluate("() => self.WalletCoordinator.current.musig.keys")
        json.dump(keys, open("/tmp/musig-wallet-keys.json", "w"))
        want = subprocess.run([sys.executable, "-c", CHECK],
                              capture_output=True, text=True, check=True).stdout.strip()
        print("panel      :", shown[0] if shown else "(none)")
        print("coordinator:", want)
        ok = bool(shown) and shown[0] == want
        print("\n%s" % ("the page built the wallet the coordinator computes"
                        if ok else "MISMATCH"))
        return 0 if ok else 1
    finally:
        sim.stop()


def load_and_export(sim, digits):
    """Scan a SeedQR, then Export Xpub -> Single sig -> Native Segwit -> Static."""
    since = sim.mark()
    sim.back_to_home()
    sim.select()
    sim.wait_screen("ScanScreen", since=since, timeout=60)
    sim.scan_qr_frames([digits], expect_screen="SeedFinalizeScreen",
                       since=since, timeout=240)
    time.sleep(1.5)
    since = sim.mark()
    sim.select()                                   # Done
    sim.wait_screen("SeedOptionsScreen", since=since, timeout=60)
    since = sim.mark()
    sim.down(); sim.select()                       # Export Xpub
    sim.wait_screen("ButtonListScreen", since=since, timeout=60)
    sim.select(); time.sleep(1.2)                  # Single sig
    sim.select(); time.sleep(1.2)                  # Native Segwit
    since = sim.mark()
    sim.down(); sim.select()                       # Static, not animated
    for _ in range(8):
        try:
            sim.wait_screen("QRDisplayScreen", since=since, timeout=4)
            break
        except Exception:
            sim.select()
    else:
        raise AssertionError("no QR after the export, sat on %s"
                             % sim.current_screen())
    sim.up(6)                                      # it opens dim
    time.sleep(1.5)


def open_panel(page):
    page.wait_for_selector(".wal-openrow button", timeout=120000)
    page.locator(".wal-openrow button").first.click()
    page.wait_for_selector("#wallet:not([hidden])", timeout=30000)


def enter_musig(page):
    """Past the verify prompt, which sits in front of the balance row."""
    for _ in range(90):
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


def wait_for(page, script, count, what):
    for _ in range(90):
        if len(page.evaluate(script)) >= count:
            return
        time.sleep(1)
    raise AssertionError("waited for " + what)


if __name__ == "__main__":
    sys.exit(main())
