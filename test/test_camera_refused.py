#!/usr/bin/env python3
"""What the device says when the browser refuses the camera.

Every other test here hands the page a camera: the shared harness launches
Chromium with --use-fake-ui-for-media-stream, grants the camera permission and
substitutes a canvas for the lens, so getUserMedia always succeeds. The path a
visitor takes when they answer "no" to the prompt was therefore never run once,
and what it showed was the firmware's screen for a real device with a loose
ribbon cable:

    Hardware Error
    Cannot access camera
    Disconnect power and check for a loose camera connection.

There is no ribbon cable in a browser and no power to disconnect. So this one
uses a plain browser with no camera and no permission, which is what a refusal
actually looks like, and checks the device says something true.

    python3 test/test_camera_refused.py
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 8795
URL = ("http://127.0.0.1:%d/wallet.html"
       "?firmware=doomsigner&debug=1&wallet=1" % PORT)
SHOTS = "/home/rob/.cache/tmp/musig-e2e"

# Screen text is drawn, never logged, so the check is on the line the browser
# version of the screen prints. If the firmware's own screen runs instead, that
# line is absent, which is the failure this exists to catch.
RAN = "camera: the browser refused it"


def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(SHOTS, exist_ok=True)
    served = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "serve.py"), "--port", str(PORT),
         "src/web", "src/shims", "build/out",
         "/home/rob/.cache/tmp/musig-simroot",
         "/home/rob/apps/bitsaga/webapp/seedsigner-simulator"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    said = []
    try:
        with sync_playwright() as pw:
            # A camera that exists, and a prompt nobody answers yes to. Both
            # halves matter: --use-fake-device-for-media-stream gives the page a
            # camera to ask for, and leaving out --use-fake-ui-for-media-stream
            # means the request is refused. Without the device the page takes a
            # different path entirely, the one for hardware that is not there.
            browser = pw.chromium.launch(
                headless=True, args=["--use-fake-device-for-media-stream"])
            page = browser.new_context(viewport={"width": 1100, "height": 1200},
                                       service_workers="block").new_page()
            page.on("console", lambda m: said.append(m.text))
            page.goto(URL, wait_until="domcontentloaded", timeout=120_000)

            for _ in range(120):
                time.sleep(1)
                if any("MainMenuScreen" in line for line in said):
                    break
            # Dismiss the dev-build warning, then take Scan, the first item.
            for _ in range(3):
                page.keyboard.press("Enter")
                time.sleep(1.2)

            for _ in range(60):
                time.sleep(1)
                if any("CameraConnectionErrorView" in line for line in said):
                    break
            time.sleep(1.5)
            shot = os.path.join(SHOTS, "camera-refused.png")
            page.screenshot(path=shot)
            print("wrote", shot)
            browser.close()
    finally:
        served.terminate()

    console = "\n".join(said)
    if "CameraConnectionErrorView" not in console:
        raise AssertionError(
            "the camera error screen never came up, so nothing was checked\n"
            "the device's last words:\n  %s" % "\n  ".join(said[-30:]))
    if RAN not in console:
        raise AssertionError(
            "the firmware's own camera screen ran, the one that tells a browser "
            "to disconnect power and check for a loose camera connection\n"
            "the device's last words:\n  %s" % "\n  ".join(said[-30:]))

    print("the device says the browser refused, not that a cable is loose")
    return 0


if __name__ == "__main__":
    sys.exit(main())
