#!/usr/bin/env python3
"""The info sheet in both modes, so the difference can be seen rather than trusted."""
import os
import sys
import time

sys.path.insert(0, "/home/rob/apps/bitsaga/services/signet/test")

SHOTS = "/home/rob/.cache/tmp/coordinator-shots"
MODES = [("smartcard", "about-stock"), ("doomsigner-musig", "about-musig")]


def main():
    from simdrive import Sim

    os.makedirs(SHOTS, exist_ok=True)
    for firmware, name in MODES:
        url = ("http://127.0.0.1:8792/wallet.html?firmware=%s&debug=1&wallet=1"
               % firmware)
        sim = Sim(headless=True, url=url)
        sim.start()
        try:
            sim.open(timeout=300)
            page = sim.page
            page.wait_for_selector(".wal-openrow button", timeout=120000)
            page.locator(".wal-openrow button").first.click()
            page.wait_for_selector("#wallet:not([hidden])", timeout=30000)
            page.locator(".wal-about-open").click()
            time.sleep(0.6)
            page.locator(".wal-about").screenshot(
                path=os.path.join(SHOTS, name + ".png"))
            listed = page.locator(".wal-about li a").all_inner_texts()
            print("%-18s %s" % (firmware, ", ".join(listed) or "(none)"))
        finally:
            sim.stop()


if __name__ == "__main__":
    main()
