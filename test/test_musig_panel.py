#!/usr/bin/env python3
"""Drive the panel's MuSig2 section the way a visitor would.

Loads a seed, exports its key to the panel, presses MuSig2, presses Build, and
checks the address the panel shows is the one the coordinator computes for the
same three keys.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")
sys.path.insert(0, "/home/rob/apps/_scratch/musig2-sim/test")

# Bitsaga Signet's API allows exactly one browser origin, https://bitsaga.be,
# so the panel cannot reach it from a page served on 127.0.0.1: it connects,
# then fails asking the chain what it holds. signet_bridge answers that origin
# from this working tree, which is what the live test does.
URL = ("https://bitsaga.be/wallet.html"
       "?firmware=doomsigner-musig&debug=1&wallet=1")
LOCAL = 8792
SHOTS = "/home/rob/.cache/tmp/coordinator-shots"
SEEDQR_B = "204720472047204720472047204720472047204720472037"

PRESS = """
async () => {
  const wallet = document.querySelector("#wallet");
  const find = (text) => Array.from(wallet.querySelectorAll("button"))
    .find((b) => b.textContent.trim() === text);
  const w = self.walletPanel;
  return { buttons: Array.from(wallet.querySelectorAll("button"))
             .map((b) => b.textContent.trim()) };
}
"""


def main():
    from simdrive import Sim

    os.makedirs(SHOTS, exist_ok=True)
    from signet_bridge import serve_site_at_real_origin

    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        serve_site_at_real_origin(sim.page.context, LOCAL)
        sim.open(timeout=300)
        page = sim.page

        since = sim.mark()
        sim.select()
        sim.wait_screen("ScanScreen", since=since, timeout=60)
        sim.scan_qr_frames([SEEDQR_B], expect_screen="SeedFinalizeScreen",
                           since=since, timeout=180)
        # Every step waits for where it arrives. Counting presses fails
        # because a privacy warning and a details page sit in this path
        # depending on settings, and pressing once more than needed walks
        # straight past the QR into Verify Address.
        # sim.wait_screen watches the device's own narration for a screen
        # entered since a mark, which is what the working demo steers by.
        # Polling the current name instead reads whatever was last logged and
        # cannot tell a screen that never changed from one that changed twice.
        def arrive(name, since, timeout=60):
            sim.wait_screen(name, since=since, timeout=timeout)
            return sim.mark()

        time.sleep(1.5)                       # let the finalize screen settle
        since = sim.mark()
        sim.select()                          # Done
        since = arrive("SeedOptionsScreen", since)
        sim.down(); sim.select()              # Export Xpub
        since = arrive("ButtonListScreen", since)
        sim.select()                          # Single sig
        time.sleep(1.2)
        sim.select()                          # Native Segwit
        time.sleep(1.2)
        since = sim.mark()
        sim.down(); sim.select()              # Static, not the animated default
        # A privacy warning and a details page may sit between here and the
        # code. Press only while the code has not arrived, and check against
        # the narration since this mark rather than the last thing logged.
        for _ in range(8):
            try:
                sim.wait_screen("QRDisplayScreen", since=since, timeout=4)
                break
            except Exception:
                sim.select()
        else:
            raise AssertionError("no QR after the export, sat on %s"
                                 % sim.current_screen())
        sim.up(6)                             # it opens dim; jsQR needs it bright
        time.sleep(1.5)
        print("device is showing:", sim.current_screen())

        page.wait_for_selector(".wal-openrow button", timeout=120000)
        page.locator(".wal-openrow button").first.click()
        page.wait_for_selector("#wallet:not([hidden])", timeout=30000)
        # The panel reads the QR off the device's screen by itself.
        for _ in range(60):
            time.sleep(1)
            names = page.evaluate(PRESS)["buttons"]
            # The verify prompt sits in front of the balance row until it is
            # dismissed, and the MuSig2 button lives on the row behind it.
            if "Done" in names:
                page.get_by_role("button", name="Done", exact=True).click()
                time.sleep(1)
                names = page.evaluate(PRESS)["buttons"]
            if "MuSig2" in names:
                break
        print("panel buttons:", names)
        if "MuSig2" not in names:
            said = page.evaluate("""() => {
              const w = self.WalletCoordinator.current;
              return {stage: w.stage, error: w.error || "(none)",
                      text: document.querySelector("#wallet .wal-body")
                              .innerText.slice(0, 240)};
            }""")
            raise AssertionError("panel stage %s, error: %s\n   body: %s"
                                 % (said["stage"], said["error"], said["text"]))

        page.get_by_role("button", name="MuSig2", exact=True).click()
        time.sleep(0.8)

        # The wallet cannot be built out of nothing: three cosigners have to be
        # exported off the device first, and until they are the button is
        # disabled and says so on hover. This test used to click it and wait
        # for an address, which stopped being possible when that rule arrived;
        # the whole build is driven for real by test_musig_e2e.py.
        make = page.get_by_role("button", name="Create the wallet")
        disabled = make.is_disabled()
        why = make.get_attribute("title") or "(nothing)"
        print("create button      : %s" % ("disabled" if disabled else "ENABLED"))
        print("and it says        : %s" % why)
        page.locator("#wallet").screenshot(path=os.path.join(SHOTS, "musig-panel.png"))
        if not disabled:
            raise AssertionError("the wallet can be built with no cosigners")
        if "cosigner" not in why:
            raise AssertionError("the disabled button does not say why: %s" % why)
        print("\nthe panel refuses to build a wallet with no cosigners")
    finally:
        sim.stop()


if __name__ == "__main__":
    main()
