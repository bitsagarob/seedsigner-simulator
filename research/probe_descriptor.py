#!/usr/bin/env python3
"""Can the device register a musig() wallet with no new device code?

Scans the descriptor at the device and reports which screen answers. If it is
accepted, the coordinator needs no new firmware: the address comes back through
flows SeedSigner already has.
"""
import os
import sys
import time

sys.path.insert(0, "/home/rob/.cache/tmp/ss-sp-spend-nno_t15g")
sys.path.insert(0, "/home/rob/apps/seedsigner-sp/src")
sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")

URL = ("http://127.0.0.1:8791/wallet.html"
       "?firmware=doomsigner-musig&debug=1&network=regtest")
SHOTS = "/home/rob/.cache/tmp/musig-probe"


def main():
    from simdrive import Sim

    descriptor = open("/tmp/desc-clean.txt").read().strip()
    os.makedirs(SHOTS, exist_ok=True)
    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        sim.open(timeout=300)
        print("firmware:", sim.page.evaluate("() => window.__firmware"))

        since = sim.mark()
        sim.select()
        sim.wait_screen("ScanScreen", since=since, timeout=60)
        print("scanning a %d character descriptor" % len(descriptor))
        sim.scan_qr_frames([descriptor], since=since, timeout=180,
                           expect_screen=None)
        time.sleep(3)

        sim.select()          # OK, back to the main menu
        time.sleep(1.5)
        sim.back_to_home()
        sim.down()            # Scan -> Tools
        sim.select()
        time.sleep(1.5)
        sim.screen_png(os.path.join(SHOTS, "tools"))
        # new seed, new seed, passwords, smartcard, calc -> [address explorer]
        sim.down(5)
        sim.select()
        time.sleep(1.5)

        seen = []
        for step in range(8):
            sim.screen_png(os.path.join(SHOTS, "%02d" % step))
            label = sim.current_screen() or "?"
            if not seen or label != seen[-1]:
                seen.append(label)
                print("  screen:", label)
            sim.select()
            time.sleep(1.2)
        print("\nscreens:", " -> ".join(seen))
    finally:
        sim.stop()


if __name__ == "__main__":
    main()
