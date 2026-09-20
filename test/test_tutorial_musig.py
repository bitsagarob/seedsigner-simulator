import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report
from playwright.sync_api import sync_playwright
import test_tutorial_single
from test_tutorial_single import (OfflineChain, ORIGIN, PhaseShots, wait, control, quiet,
                                  load_embit)
from musig_reference import check_artifact, check_published_vectors


TRACE_QR = """
  const wrap = () => {
    if (!window.QREncode || window.QREncode.__traced) return false;
    const real = window.QREncode.matrix;
    window.QREncode.matrix = function (text) {
      console.log("PAINT " + Math.round(performance.now()) + " "
                  + String(text).slice(0, 16));
      return real.apply(this, arguments);
    };
    window.QREncode.__traced = true;
    return true;
  };
  if (!wrap()) { const t = setInterval(() => { if (wrap()) clearInterval(t); }, 50); }
"""


def coordinator_zip():
    data = io.BytesIO()
    with zipfile.ZipFile(harness.find_asset("wallet-doomsigner.zip")) as source:
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                if name.startswith("embit/"):
                    target.writestr(name, source.read(name))
    return data.getvalue()


def main():
    test_tutorial_single.SHOTS = PhaseShots("musig-doomsigner")
    load_embit()
    assert all(ok for _, ok, _ in check_published_vectors())
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(service_workers="block", viewport={"width": 1000, "height": 1300})
        chain = OfflineChain(context)
        archive = coordinator_zip()
        context.route("**/wallet-embit.zip", lambda route: route.fulfill(body=archive, content_type="application/zip"))
        page = context.new_page()
        # SIM_TRACE_QR=1 logs every payload the phone paints, with a clock. It is
        # here for one open bug: this walkthrough has failed once in seventeen
        # runs with the firmware's own decoder raising "Segment total changed
        # unexpectedly" the moment the PSBT scan opened, which can only happen if
        # two rounds' frames reach one scan. The rounds carry very different
        # totals -- p1of5 early, p1of19 later -- so the trace names the round a
        # stray frame came from. Off by default: it is a line per frame.
        if os.environ.get("SIM_TRACE_QR"):
            page.add_init_script(TRACE_QR)
        log = Log(page)
        chain.log = log
        try:
            page.goto(f"{ORIGIN}/wallet.html?firmware=doomsigner&tutorial=musig&debug=1&e2e=1")
            log.wait(r"display\(\) enter: WarningScreen\b", 180, "DoomSigner build warning")
            page.wait_for_timeout(1000)
            mark = log.mark()
            page.keyboard.press("Enter")
            log.wait(r"display\(\) exit: WarningScreen -> 0", 30, "acknowledged warning", mark)
            log.wait(r"display\(\) enter: MainMenuScreen\b", 90, "home", mark)
            assert page.evaluate("window.__firmware") == "doomsigner"
            assert page.evaluate("WalletTutorial.current.id") == "musig"
            page.evaluate("""() => {
                const t = WalletTutorial.current;
                t.pace = text => text === 'Build the MuSig2 2 of 3 wallet'
                    ? new Promise(resolve => { window.releaseBuild = resolve; }) : Promise.resolve();
            }""")
            control(page, "Play")
            page.get_by_role("button", name="Random noise instead", exact=True).click()
            wait(page, "t.stepText.textContent === 'Build the MuSig2 2 of 3 wallet'", "three card keys", 600)
            control(page, "Pause")
            page.evaluate("window.releaseBuild()")
            quiet(page, log)
            page.evaluate("() => { WalletTutorial.current.pace = () => Promise.resolve(); }")
            control(page, "Back")
            wait(page, "t.paused", "Back pauses")
            check("MuSig Back retains three exported keys at the safe home boundary", page.evaluate("WalletTutorial.current.state.keys.length") == 3 and log.last_screen() == "MainMenuScreen")
            page.evaluate("() => { WalletTutorial.current.pace = () => Promise.resolve(); }")
            control(page, "Play")
            wait(page, "t.finished", "MuSig walkthrough completion", 600)
            state = page.evaluate("() => JSON.parse(JSON.stringify(WalletTutorial.current.state))")
            assert len(chain.claims) == len(chain.broadcasts) == 1
            assert chain.broadcasts[0]["hex"] == state["session"]["txhex"]
            for name, ok, detail in check_artifact(state["session"]):
                check(name, ok, detail)
            for stage in ["nonce signed=0 waiting=1", "signed signed=1 waiting=0"]:
                check("firmware narrates " + stage, bool(log.seen("PSBTMusig2Round: stage=" + stage)))
            check("both cards actually release their nonce", bool(log.seen("Card A released MuSig2 nonce")) and bool(log.seen("Card B released MuSig2 nonce")))
            check("exactly three optical MuSig rounds", len(state["rounds"]) == 3)
            check("no external endpoint or firmware exception", not chain.unexpected and not log.seen(r"RAISED|PAGEERROR|Traceback|UnhandledException"))
            assert page.locator('#tutorial button[aria-label="Back"]').is_disabled()
            control(page, "Begin again")
            page.wait_for_load_state()
            page.wait_for_function("window.WalletTutorial && WalletTutorial.current && WalletTutorial.current.id === 'musig'", timeout=300000)
            check("Begin again preserves MuSig/DoomSigner and clears session", page.evaluate("window.__firmware === 'doomsigner' && WalletTutorial.current.firmware === 'doomsigner' && !WalletTutorial.current.state"))
        finally:
            with open(harness.artifact("tutorial-musig.log"), "w") as handle:
                handle.write("\n".join(log.lines))
            browser.close()
    return report()


if __name__ == "__main__":
    sys.exit(main())
