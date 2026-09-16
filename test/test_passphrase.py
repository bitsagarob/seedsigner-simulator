import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import mainnet_reference as reference
from harness import Log, check, report
from make_qr_y4m import MNEMONIC
from test_cards_browser import press
from test_mainnet import compared, expected_xpub_string, export_xpub, wait_screen

from playwright.sync_api import sync_playwright

PASSPHRASE = "abc"
PATH = "m/84'/1'/0'"
VERSION_VPUB = bytes.fromhex("045f1cf6")
Y4M = harness.artifact("qr.y4m")


def main() -> int:
    if not os.path.exists(Y4M):
        print(f"no {Y4M}: run make_qr_y4m.py first", file=sys.stderr)
        return 2

    for name, passed, detail in reference.check_published_vectors():
        check(name, passed, detail)

    roots = {phrase: reference.root_from_mnemonic(MNEMONIC, phrase)
             for phrase in ("", PASSPHRASE)}
    expected = {phrase: expected_xpub_string(root, PATH, VERSION_VPUB)
                for phrase, root in roots.items()}
    check("the independent fingerprints differ with and without the passphrase",
          roots[""].fingerprint != roots[PASSPHRASE].fingerprint)
    check("the independent account keys differ, not just their origins",
          roots[""].derive(PATH).extended_public_key(VERSION_VPUB)
          != roots[PASSPHRASE].derive(PATH).extended_public_key(VERSION_VPUB))

    exported = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={Y4M}",
        ])
        for phrase in ("", PASSPHRASE):
            label = "with-passphrase" if phrase else "without-passphrase"
            print(f"\n{label}", flush=True)
            context = browser.new_context(
                permissions=["camera"],
                viewport={"width": 900, "height": 1000},
            )
            page = context.new_page()
            log = Log(page)
            page.goto(harness.wallet_url(firmware="smartcard"))
            window = wait_screen(log, "MainMenuScreen", 0, "the wallet to boot", 300)
            press(page, "Enter")
            window = wait_screen(log, "ScanScreen", window, "the scanner")
            window = wait_screen(log, "SeedFinalizeScreen", window, "the decoded seed", 180)
            check(f"{label}: the camera loads the mnemonic", True)

            if phrase:
                press(page, "ArrowDown")
                press(page, "Enter")
                window = wait_screen(log, "SeedAddPassphraseScreen", window,
                                     "the BIP39 passphrase keyboard")
                check("Type Passphrase opens the firmware keyboard", True)
                for key in ("Enter", "ArrowRight", "Enter", "ArrowRight", "Enter", "3"):
                    press(page, key)
                log.wait(r"display\(\) exit: SeedAddPassphraseScreen -> "
                         + re.escape(repr({"passphrase": PASSPHRASE})) + r"$",
                         120, "the keyboard to return the typed passphrase", window)
                window = wait_screen(log, "SeedReviewPassphraseScreen", window,
                                     "the passphrase review", 120)
                check("the firmware accepts the exact passphrase and reaches review", True)

            press(page, "Enter")
            wait_screen(log, "SeedOptionsScreen", window, "the finalized seed", 120)
            check(f"{label}: Done finalizes the seed", True)
            page.add_script_tag(url="jsQR.js")
            drawn = export_xpub(page, log, [], PATH, f"passphrase-{label}.png")
            exported[phrase] = drawn
            origin = re.match(r"\[([0-9a-f]{8})/", drawn or "")
            check(f"{label}: exported fingerprint matches the independent BIP39 root",
                  origin is not None and origin.group(1) == roots[phrase].fingerprint.hex(),
                  f"got {origin.group(1) if origin else None}, "
                  f"wanted {roots[phrase].fingerprint.hex()}")
            check(f"{label}: the complete origin and derived key match the reference",
                  drawn == expected[phrase], compared(drawn, expected[phrase]))
            check(f"{label}: no firmware or page exception",
                  log.seen(r"RAISED|PAGEERROR|Traceback|display\(\) enter: (?:Error|DireWarning)Screen")
                  is None)
            context.close()

        check("the passphrase export is not the independently computed bare-seed export",
              exported[PASSPHRASE] is not None and exported[PASSPHRASE] != expected[""])
        check("the wallet exported different keys, not just different fingerprints",
              all(exported.values())
              and exported[""].split("]", 1)[-1] != exported[PASSPHRASE].split("]", 1)[-1])
        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
