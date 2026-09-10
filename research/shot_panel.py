#!/usr/bin/env python3
"""Photograph the coordinator panel, and its info sheet."""
import os
import sys
import time

sys.path.insert(0, "$SIGNET_TEST_DIR")

URL = ("http://127.0.0.1:8792/wallet.html"
       "?firmware=doomsigner-musig&debug=1&network=regtest&wallet=1")
SHOTS = "$TMP/coordinator-shots"


def main():
    from simdrive import Sim

    os.makedirs(SHOTS, exist_ok=True)
    sim = Sim(headless=True, url=URL)
    sim.start()
    try:
        sim.open(timeout=300)
        page = sim.page
        page.wait_for_selector(".wal-openrow button", timeout=120000)
        page.locator(".wal-openrow button").first.click()
        page.wait_for_selector("#wallet:not([hidden])", timeout=30000)
        time.sleep(1.5)
        page.locator("#wallet").screenshot(path=os.path.join(SHOTS, "panel.png"))
        print("panel photographed")

        page.locator(".wal-about-open").click()
        time.sleep(0.6)
        page.locator("#wallet").screenshot(path=os.path.join(SHOTS, "about.png"))
        print("info sheet photographed")
        print("expanded:", page.locator(".wal-about-open").get_attribute("aria-expanded"))
    finally:
        sim.stop()


if __name__ == "__main__":
    main()
