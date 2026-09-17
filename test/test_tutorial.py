"""
The multisig tutorial, without the network.

Three things this can prove offline, and one it cannot. It can prove the
coordinator on the page computes the right things, by checking every value it
produces against the wallet's own embit; it can prove hands on mode works, by
pressing the buttons itself and watching the panel keep pace; and it can prove
the failure states are designed rather than accidental, by breaking Bitsaga
Signet in the three ways it can be broken and photographing what the visitor
sees. What it cannot prove is that a spend confirms on a real chain: that is
test_tutorial_live.py, which needs the network and is not in this suite.

The coordinator checks are the interesting half. Nothing in
src/web/signet-coordinator.js is shared with the wallet: it is a second,
independent implementation of BIP32 public derivation, sortedmulti, P2WSH,
bech32 and BIP174, written for the page. So the values it produces are compared
against embit, taken out of the wallet zip, which is the library the device
itself parses these with. Two implementations agreeing is worth something; one
implementation agreeing with itself is not.

The coordinator checks use three published BIP39 test vectors. The hands-on run
creates a fresh seed from image entropy. None of these should ever hold value.
"""

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report
from signet_bridge import API, serve_at

from playwright.sync_api import sync_playwright

# The working tree, served at Bitsaga Signet's own origin.
#
# Only so that the API calls are same-origin: a cross-origin fetch would need a
# preflight, and a preflight is answered by whatever is really at that host,
# which would drag the network into a test that is meant to run without one.
# The page is this checkout either way, and where it is really served from is
# test_tutorial_live.py's business, not this file's.
ORIGIN = "https://signet.bitsaga.be"

# The three exported account keys the device produces for those seeds, in the
# SLIP-132 form SeedSigner puts in the QR. Held here rather than read off a
# device because these checks are about the coordinator, not about the device;
# test_tutorial_live.py is where they come off a card for real.
EXPORTED = [
    "[73c5da0a/48'/1'/0'/2']Vpub5n95dMZrDHj6SeBgJ1oz4Fae2N2eJNuWK3VTKDb2dzGpMFLUHLmty"
    "Dfen7AaQxwQ5mZnMyXdVrkEaoMLVTH8FmVBRVWPGFYWhmtDUGehGmq",
    "[b8688df1/48'/1'/0'/2']Vpub5mXjbXRpPCwR3WFMWjmrunQ2qNotZBN9a94RbhVADFC5zj6X2gw48"
    "wJ5dFFPdHKjLiyyXZgzxzBe7jMRHxKwxf9LqenaMTYMPHMomBZhZ24",
    "[28645006/48'/1'/0'/2']Vpub5momCasstFqTyLZDFPxn7nUwMWfFTsWK4tfD1A6V1SLE1C1DQqFY8"
    "6LYMmY1Ed5LGCbK6BpysWYdu7MyP3kj9nbycwi9kLsxZAf8f2Zywv1",
]

SIGNING_SEEDS = [
    "abandon abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon about",
    "legal winner thank year wave sausage worth useful legal winner thank yellow",
]

# A made-up output to spend, so the whole PSBT is a fixed value both sides can
# be checked against. Nothing on any chain has this id.
INPUT = {"txid": "aa" * 32, "vout": 0, "value": 1000000}
FEE = 1000


def embit():
    """The wallet's own library, out of the wallet zip it ships in."""
    if "embit" in sys.modules:
        return sys.modules["embit"]
    wallet_zip = harness.find_asset("wallet-smartcard.zip")
    if not wallet_zip:
        raise SystemExit("no wallet-smartcard.zip: run build/build-wallet-zip.sh smartcard")
    target = tempfile.mkdtemp(prefix="seedsigner-sim-tutorial-")
    with zipfile.ZipFile(wallet_zip) as archive:
        archive.extractall(target, [n for n in archive.namelist() if n.startswith("embit/")])
    sys.path.insert(0, target)
    import embit as module
    return module


def wallet_by_embit():
    """The same 2 of 3, derived by embit: descriptor, both addresses, script."""
    embit()
    from embit import bip32, base58
    from embit.descriptor import Descriptor
    from embit.networks import NETWORKS

    keys = []
    for exported in EXPORTED:
        origin, _, key = exported.partition("]")
        raw = base58.decode_check(key)
        tpub = base58.encode_check(bytes.fromhex("043587cf") + raw[4:])
        keys.append(origin.replace("'", "h") + "]" + tpub + "/{0,1}/*")
    descriptor = "wsh(sortedmulti(2," + ",".join(keys) + "))"
    parsed = Descriptor.from_string(descriptor)
    return {
        "descriptor": descriptor,
        "receive": parsed.derive(0, branch_index=0).address(NETWORKS["test"]),
        "change": parsed.derive(0, branch_index=1).address(NETWORKS["test"]),
        "witness": parsed.derive(0, branch_index=0).witness_script().data.hex(),
    }


def signed_by_embit(unsigned):
    """Two of the three seeds signing the coordinator's own PSBT.

    Signatures a device would have produced, produced here instead, so the
    finishing half can be checked without a device in the loop.
    """
    embit()
    from embit import bip32
    from embit.psbt import PSBT

    out = []
    for mnemonic in SIGNING_SEEDS:
        psbt = PSBT.from_string(unsigned)
        seed = hashlib.pbkdf2_hmac("sha512", mnemonic.encode(), b"mnemonic", 2048, 64)
        signed = psbt.sign_with(bip32.HDKey.from_seed(seed))
        if signed != 1:
            raise AssertionError(f"embit signed {signed} inputs, not 1")
        out.append(str(psbt))
    return out


def ur_parts_by_the_firmware(psbt_base64, fragment, cycles=40):
    """The animated QR a SeedSigner really emits for a signed PSBT.

    Made by the firmware's own UR encoder, out of the wallet zip, so what
    ur-decode.js is asked to reassemble is what the device draws rather than
    something written to match it. Two fragment sizes because the device's QR
    density setting picks between them and the two produce different numbers of
    parts.

    Returns (parts, how many of them are the plain fragments). Everything after
    that many is a fountain code, which is all a device is still emitting by the
    time anybody has read a few frames.
    """
    wallet_zip = harness.find_asset("wallet-smartcard.zip")
    target = tempfile.mkdtemp(prefix="seedsigner-sim-ur-")
    with zipfile.ZipFile(wallet_zip) as archive:
        archive.extractall(target, [n for n in archive.namelist() if not n.endswith(".pyc")])
    sys.path.insert(0, target)
    from seedsigner.helpers.ur2.ur import UR
    from seedsigner.helpers.ur2.ur_encoder import UREncoder
    from urtypes.crypto import PSBT as UR_PSBT

    ur = UR("crypto-psbt", UR_PSBT(base64.b64decode(psbt_base64)).to_cbor())
    encoder = UREncoder(ur=ur, max_fragment_len=fragment)
    count = encoder.fountain_encoder.seq_len()
    return [encoder.next_part().upper() for _ in range(count * cycles)], count


def txid_by_embit(unsigned):
    """A segwit transaction's id never covers its witness, so the id of the
    finished transaction is the id of the unsigned one embit already holds."""
    embit()
    from embit.psbt import PSBT
    return PSBT.from_string(unsigned).tx.txid().hex()


# --- what the page is asked -------------------------------------------------

COORDINATOR = """
async ([exported, input, fee]) => {
  const C = window.SignetCoordinator;
  const wallet = await C.buildWallet(exported);
  const receive = await C.deriveAddress(wallet, 0, 0);
  const change = await C.deriveAddress(wallet, 1, 0);
  const amount = BigInt(input.value) - BigInt(fee);
  const psbt = C.toBase64(C.buildPsbt(
    { txid: input.txid, vout: input.vout, value: BigInt(input.value) },
    receive, change.scriptPubkey, amount));
  return {
    descriptor: wallet.descriptor,
    receive: receive.address,
    change: change.address,
    witness: C.hex(receive.witnessScript),
    psbt: psbt,
  };
}
"""

FINISH = """
async ([exported, input, fee, signed]) => {
  const C = window.SignetCoordinator;
  const wallet = await C.buildWallet(exported);
  const receive = await C.deriveAddress(wallet, 0, 0);
  const change = await C.deriveAddress(wallet, 1, 0);
  const signatures = {};
  for (const one of signed) Object.assign(signatures, C.partialSignatures(one));
  const final = await C.finalise(
    { txid: input.txid, vout: input.vout, value: BigInt(input.value) },
    receive, change.scriptPubkey, BigInt(input.value) - BigInt(fee), signatures);
  return { txid: final.txid, hex: final.hex, signatures: Object.keys(signatures).length };
}
"""

# The encoder has to survive its own output being read back by the decoder the
# wallet uses, for every shape of payload this tutorial holds up to the camera.
ROUND_TRIP = """
async (payloads) => {
  if (!window.jsQR) await new Promise((ok, no) => {
    const tag = document.createElement("script");
    tag.src = "jsQR.js"; tag.onload = ok; tag.onerror = no;
    document.head.appendChild(tag);
  });
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  return payloads.map((payload) => {
    const matrix = window.QREncode.matrix(payload);
    const scale = 4, border = 4, size = (matrix.length + border * 2) * scale;
    canvas.width = canvas.height = size;
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = "#000";
    for (let r = 0; r < matrix.length; r++)
      for (let c = 0; c < matrix.length; c++)
        if (matrix[r][c]) ctx.fillRect((border + c) * scale, (border + r) * scale, scale, scale);
    const image = ctx.getImageData(0, 0, size, size);
    const got = window.jsQR(image.data, size, size);
    return got && got.data === payload;
  });
}
"""


def bar_width(page):
    """How much of the progress line is drawn, in pixels: zero is not shown."""
    return page.locator("#tutorial .tut-bar i").evaluate(
        "node => node.getBoundingClientRect().width")


def panel(page, selector):
    container = "#device-say" if selector in (".tut-do", ".tut-step") else "#tutorial"
    node = page.locator(container + " " + selector)
    return node.inner_text().strip() if node.count() else ""


def wait_instruction(page, contains, timeout=90):
    """Wait for the panel to be asking for something, and say what it asks."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = panel(page, ".tut-do")
        if contains.lower() in text.lower():
            return text
        page.wait_for_timeout(200)
    raise AssertionError(f"the panel never asked for {contains!r}; it says "
                         f"{panel(page, '.tut-do')!r} on {panel(page, '.tut-step')!r}")


def next_instruction(page, after, timeout=180):
    """Wait for the panel to ask for something else, and say how long that took."""
    started = time.time()
    while time.time() - started < timeout:
        text = panel(page, ".tut-do")
        if text and text != after:
            return text, time.time() - started
        page.wait_for_timeout(100)
    raise AssertionError(f"the panel sat on {after!r} for {timeout}s")


def boot(context, log_lines, query="tutorial=1&debug=1"):
    page = context.new_page()
    log = Log(page)
    page.goto(f"{ORIGIN}/wallet.html?{query}")
    log.wait(r"display\(\) enter: MainMenuScreen", 300, "the wallet to boot")
    log_lines.append(log)
    return page, log


def doomsigner_card(browser):
    from test_tutorial_single import OfflineChain, ordered, wait

    context = browser.new_context(viewport={"width": 1000, "height": 1300},
                                  service_workers="block")
    chain = OfflineChain(context)
    page = context.new_page()
    log = Log(page)
    chain.log = log
    try:
        page.goto(f"{ORIGIN}/wallet.html?tutorial=multi&firmware=doomsigner&debug=1")
        log.wait(r"display\(\) enter: WarningScreen\b", 180, "Doomsigner build warning")
        page.wait_for_timeout(600)
        mark = log.mark()
        page.keyboard.press("Enter")
        log.wait(r"display\(\) exit: WarningScreen -> 0", 30, "acknowledge build warning", mark)
        log.wait(r"display\(\) enter: MainMenuScreen\b", 60, "Doomsigner tutorial boot", mark)
        assert page.evaluate("window.__firmware") == "doomsigner"
        assert page.evaluate("window.WalletTutorial.current.firmware") == "doomsigner"
        assert page.evaluate("window.WalletTutorial.current.id") == "multi"
        page.evaluate("""() => {
            const t = window.WalletTutorial.current;
            t.pace = text => text === 'Put a test seed on Card B'
                ? new Promise(resolve => { window.releaseCardBTitle = resolve; })
                : Promise.resolve();
        }""")
        page.locator('#tutorial button[aria-label="Play"]').click()
        page.get_by_role("button", name="Random noise instead", exact=True).click()
        stored = log.wait(
            r"\[card\] Card A stored secret (\d+), type 0x(\w+) subtype 0x(\w+), "
            r"label '([^']*)', (\d+) bytes, fingerprint ([0-9a-f]{8})",
            180, "Doomsigner to store its generated seed on Card A")
        check("Doomsigner stores a twelve-word Masterseed on Card A",
              (stored.group(2), stored.group(3), int(stored.group(5))) == ("10", "01", 84),
              stored.group(0))
        mark = next(i for i, line in enumerate(log.lines) if stored.group(0) in line)
        wait(page, "t.stepText.textContent.includes('Card B')", "Doomsigner Card A discard", 90)
        page.locator('#tutorial button[aria-label="Pause"]').click()
        page.evaluate("() => window.releaseCardBTitle()")
        wait(page, "t.paused && !t.performing", "stop at Card B")
        check("Doomsigner discards Card A's seed through its real discard menu",
              ordered(log, ["SeedOptionsScreen", "WarningScreen", "MainMenuScreen"], mark)
              and log.last_screen() == "MainMenuScreen")
        assert log.last_screen() == "MainMenuScreen", "Doomsigner advanced beyond the Card B boundary"
        check("Doomsigner reaches Card B without storing another secret or requesting coins",
              "Card B" in panel(page, ".tut-step")
              and not log.seen(r"\[card\] Card B stored secret")
              and not chain.claims and not chain.broadcasts and not chain.unexpected)
        check("Doomsigner card ceremony has no firmware or page exception",
              not log.seen(r"RAISED|PAGEERROR|Traceback|display\(\) enter: UnhandledException"))
        assert ordered(log, ["SeedOptionsScreen", "WarningScreen", "MainMenuScreen"], mark)
    except Exception:
        with open(harness.artifact("tutorial-doomsigner-failure.log"), "w") as handle:
            handle.write("\n".join(log.lines))
        page.screenshot(path=harness.artifact("tutorial-doomsigner-failure.png"), full_page=True)
        print("\n".join(log.lines[-45:]), flush=True)
        raise
    finally:
        context.close()


def main() -> int:
    expected = wallet_by_embit()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        doomsigner_card(browser)
        context = browser.new_context(viewport={"width": 1000, "height": 1300},
                                      service_workers="block")
        serve_at(context, harness.PORT, ORIGIN)
        logs = []

        # --- the coordinator, against the wallet's own library ---------------
        print("\nthe coordinator on the page, checked against embit")
        page, _ = boot(context, logs)
        got = page.evaluate(COORDINATOR, [EXPORTED, INPUT, FEE])
        check("it builds the same 2 of 3 descriptor embit builds",
              got["descriptor"] == expected["descriptor"], got["descriptor"][:70])
        check("the same first receive address",
              got["receive"] == expected["receive"], got["receive"])
        check("the same change address",
              got["change"] == expected["change"], got["change"])
        check("and the same witness script behind it",
              got["witness"] == expected["witness"], got["witness"][:40])

        unsigned = got["psbt"]
        check("its PSBT is one embit can read", txid_by_embit(unsigned) is not None)
        signed = signed_by_embit(unsigned)
        finished = page.evaluate(FINISH, [EXPORTED, INPUT, FEE, signed])
        check("it takes both signatures out of the signed PSBTs",
              finished["signatures"] == 2, str(finished["signatures"]))
        check("and finishes a transaction with the id embit computes",
              finished["txid"] == txid_by_embit(unsigned), finished["txid"])

        embit()
        from embit.transaction import Transaction
        final = Transaction.parse(bytes.fromhex(finished["hex"]))
        witness = final.vin[0].witness.items
        check("the witness is the empty item, two signatures and the script",
              len(witness) == 4 and witness[0] == b""
              and witness[3].hex() == expected["witness"],
              f"{len(witness)} items")

        payloads = page.evaluate("() => window.WalletTutorial.seeds.map(s => s.seedqr)")
        payloads.append(expected["descriptor"])
        payloads += page.evaluate("(psbt) => window.WalletTutorial.specterFrames(psbt)",
                                  unsigned)
        results = page.evaluate(ROUND_TRIP, payloads)
        check("every code the phone holds up reads back as itself, with jsQR",
              all(results), f"{sum(results)} of {len(results)}")

        # The generator that decides what went into a fountain code seeds itself
        # with a SHA-256, and ur-decode.js carries its own because it has to run
        # inside a synchronous read of a frame that is about to change. Anchored
        # on the published vector rather than only on the round trip below.
        digests = page.evaluate("""() => ["", "abc"].map(text =>
          Array.from(window.URDecode.sha256(new TextEncoder().encode(text)))
               .map(b => b.toString(16).padStart(2, "0")).join(""))""")
        check("its SHA-256 agrees with the published test vectors",
              digests == [hashlib.sha256(b"").hexdigest(), hashlib.sha256(b"abc").hexdigest()],
              digests[0][:16])

        # And the other direction: the animated QR the device really emits,
        # made by the firmware's own encoder, reassembled by ur-decode.js.
        #
        # Twice over, and the second one is the one that matters. A device
        # cycles through the plain fragments once and then emits fountain codes
        # for ever, so a reader that only understood the plain ones would work
        # exactly once, on a pass where it missed no frame at all, and never
        # again. Handing it nothing but the mixtures is the check that it really
        # decodes them.
        for fragment in (30, 120):
            emitted, count = ur_parts_by_the_firmware(unsigned, fragment)
            for label, parts in (("in order", emitted),
                                 ("from fountain codes alone", emitted[count:])):
                back = page.evaluate("""(parts) => {
                  const collector = window.URDecode.collector();
                  for (const part of parts) {
                    collector.receive(part);
                    if (collector.done()) break;
                  }
                  if (!collector.done()) return null;
                  return window.SignetCoordinator.toBase64(collector.psbt());
                }""", parts)
                check(f"it reassembles the device's own {count} part animated QR, {label}",
                      back == unsigned, f"fragment size {fragment}")
        page.close()

        # --- hands on --------------------------------------------------------
        print("\nhands on: the visitor presses, the panel keeps pace")
        page, log = boot(context, logs)
        check("tutorial=1 opens the smartcard multisig demo",
              page.locator("#tutorial h2").inner_text() == "Multisig"
              and page.locator(".cardtray-card").count() == 3
              and page.evaluate("window.__firmware") == "smartcard")
        page.locator('#tutorial button[aria-label="Play"]').click()
        page.get_by_role("button", name="Random noise instead", exact=True).click()
        page.locator('#tutorial button[aria-label="Pause"]').click()
        page.wait_for_timeout(500)
        page.locator(".tut-fold.floating > summary").click()
        page.locator(".tut-fold.floating").get_by_role(
            "button", name="I will drive", exact=True).click()
        check("the progress line is not shown in hands on mode",
              bar_width(page) == 0, f"{bar_width(page)}px")

        wait_instruction(page, "Card A into the reader")
        page.wait_for_timeout(1500)
        check("the panel waits for the visitor to insert the card",
              panel(page, ".tut-do") == "Card A into the reader"
              and log.last_screen() == "MainMenuScreen")
        page.locator(".cardtray-card").nth(0).click()
        wait_instruction(page, "Seeds")
        check("the panel moves on only once the card is in",
              panel(page, ".tut-do") == "Seeds")

        creation = log.mark()
        for instruction, keys, screen in [
            ("Seeds", ["ArrowRight", "Enter"], "ButtonListScreen"),
            ("Create a seed", ["ArrowDown"] * 4 + ["Enter"], "ButtonListScreen"),
            ("A new seed, from a photograph", ["Enter"],
             "ToolsImageEntropyLivePreviewScreen"),
            ("Take the picture", ["Enter"], "ToolsImageEntropyFinalImageScreen"),
            ("Accept it", ["ArrowRight"], "ButtonListScreen"),
            ("Twelve words", ["Enter"], "DireWarningScreen"),
            ("The device says to keep them private", ["Enter"], "SeedWordsScreen"),
            ("The twelve words it made", ["Enter"] * 3, "SeedWordsBackupTestPromptScreen"),
            ("Skip the backup check", ["ArrowDown", "ArrowDown", "Enter"],
             "SeedFinalizeScreen"),
            ("Done", ["Enter"], "SeedOptionsScreen"),
            ("Backup seed", ["ArrowDown"] * 3 + ["Enter"], "ButtonListScreen"),
        ]:
            wait_instruction(page, instruction)
            if instruction == "Take the picture":
                page.wait_for_timeout(2000)
                check("hands on waits for the visitor to take the photograph",
                      log.last_screen() == "ToolsImageEntropyLivePreviewScreen"
                      and panel(page, ".tut-do") == instruction)
            since = log.mark()
            for key in keys:
                page.keyboard.press(key)
                page.wait_for_timeout(560)
            log.wait(r"display\(\) enter: " + screen + r"\b", 120,
                     instruction, since)
            check(f"{instruction}: the firmware reaches {screen}", True)

        wait_instruction(page, "To SeedKeeper")
        check("the seed was created, not scanned or loaded from a card",
              log.seen(r"display\(\) enter: ScanScreen|\[card\].*exporting secret",
                       creation) is None)
        check("hands on has not backed up the seed before handing back",
              log.seen(r"\[card\].*stored secret", creation) is None)
        check("nothing raised while creating the seed",
              log.seen(r"View\.run RAISED|display\(\) enter: UnhandledException",
                       creation) is None)
        page.screenshot(path=harness.artifact("tutorial-hands-on.png"), full_page=True)

        # Handing back mid-step: same steps, same evidence, different driver.
        saving = log.mark()
        page.locator(".tut-fold.floating > summary").click()
        page.locator(".tut-fold.floating").get_by_role(
            "button", name="Let it drive", exact=True).click()
        stored = log.wait(
            r"\[card\] Card A stored secret (\d+), type 0x(\w+) subtype 0x(\w+), "
            r"label '([^']*)', (\d+) bytes, fingerprint ([0-9a-f]{8})",
            240, "Card A to store the generated seed", saving)
        check("handing back stores a twelve-word Masterseed on Card A",
              (stored.group(2), stored.group(3), int(stored.group(5))) == ("10", "01", 84),
              stored.group(0))
        deadline = time.time() + 240
        while time.time() < deadline and "Card B" not in panel(page, ".tut-step"):
            page.wait_for_timeout(500)
        check("handing back mid-step lets it finish the card on its own",
              "Card B" in panel(page, ".tut-step"), panel(page, ".tut-step"))
        # Every step starts its line again from nothing, so this waits for the
        # first action of the new one rather than reading the line at the
        # boundary and calling an honest zero a missing bar.
        deadline = time.time() + 90
        while time.time() < deadline and bar_width(page) == 0:
            page.wait_for_timeout(500)
        check("and the progress line comes back with it", bar_width(page) > 0,
              f"{bar_width(page)}px")
        page.close()

        # --- who sets the pace -----------------------------------------------
        #
        # Self driving used to run the whole ceremony in two minutes, which is
        # about a third of a second per instruction: too fast to read, and there
        # was nothing to do about it but reload. So the run now waits for the
        # sentence it has just put up to be read, and the panel has controls for
        # anyone that does not suit.
        print("\npacing, and the controls over it")
        page, log = boot(context, logs)
        page.locator('#tutorial button[aria-label="Play"]').click()
        page.get_by_role("button", name="Random noise instead", exact=True).click()

        instruction, _ = next_instruction(page, "")
        gaps = []
        for _ in range(3):
            instruction, waited = next_instruction(page, instruction)
            gaps.append(waited)
        check("an instruction is left up long enough to be read",
              min(gaps) > 1.0, " ".join(f"{gap:.1f}s" for gap in gaps))

        page.locator('#tutorial button[aria-label="Pause"]').click()
        # The action already in flight finishes first -- pausing happens between
        # actions and never inside one -- so what is asserted is that everything
        # then goes still and stays still, rather than that it stopped on the
        # exact instruction that was up when the button was pressed.
        still, held, bar = 0, None, -1
        deadline = time.time() + 90
        while time.time() < deadline and still < 8:
            page.wait_for_timeout(500)
            now, width = panel(page, ".tut-do"), bar_width(page)
            if (now, round(width)) == (held, round(bar)):
                still += 0.5
            else:
                still, held, bar = 0, now, width
        check("Pause stops it, and it stays stopped", still >= 8,
              f"{still}s still on {held[:50]!r}")
        check("and the progress line keeps what has actually happened", bar > 0,
              f"{bar:.0f}px")

        stepping = log.mark()
        page.locator('#tutorial button[aria-label="One step"]').click()
        stepped = log.wait(r"display\(\) enter: (\w+)", 120,
                           "one action to reach the next firmware screen", stepping).group(1)
        page.locator('#tutorial button[aria-label="Play"]').wait_for(state="visible")
        page.wait_for_timeout(500)
        step_bar = bar_width(page)
        stopped = log.mark()
        page.wait_for_timeout(8000)
        check("Step takes exactly one action and stops again",
              log.last_screen() == stepped
              and log.seen(r"display\(\) enter:", stopped) is None
              and panel(page, ".tut-do") == held,
              f"{stepped}: {panel(page, '.tut-do')[:60]}")
        check("and the progress line advances once",
              step_bar > bar and abs(bar_width(page) - step_bar) < 1,
              f"{bar:.0f}px -> {step_bar:.0f}px")
        check("and the button says it is paused again",
              page.locator('#tutorial button[aria-label="Play"]').is_visible())

        page.locator('#tutorial button[aria-label="Play"]').click()
        after, _ = next_instruction(page, held)
        check("Play carries on from there", bool(after), after[:60])

        # --- the three ways Bitsaga Signet can let it down --------------------
        #
        # On the same page and the same run: reaching the faucet means driving
        # the six card steps first, and every reading pause on the way is one
        # this file has just measured. So this run turns them off. Nothing else
        # about it changes, and test_tutorial_live.py drives the whole thing at
        # the pace a visitor gets.
        print("\nfailure states")
        page.evaluate("() => { window.WalletTutorial.current.pace = "
                      "() => Promise.resolve(); }")
        broken = {"how": "empty"}

        def faucet(route):
            if broken["how"] == "empty":
                route.fulfill(status=503, content_type="application/json",
                              body=json.dumps({"error": "The faucet is empty at the moment. "
                                                        "Rob has been told; try again shortly."}))
            elif broken["how"] == "unreachable":
                route.abort()
            else:
                # Answers nothing, which is the case a page with no timeout of
                # its own waits out for ever. The coordinator gives up on it
                # after twenty seconds, which is what this is here to prove.
                time.sleep(25)
                try:
                    route.abort()
                except Exception:                 # noqa: BLE001 - already gone
                    pass

        context.route(f"{API}/**", faucet)

        deadline = time.time() + 300
        while time.time() < deadline and "descriptor" not in panel(page, ".tut-caption"):
            page.wait_for_timeout(100)
        check("the descriptor transfer is captioned, with a direction",
              "Phone" in panel(page, ".tut-arrow")
              and "device" in panel(page, ".tut-arrow"), panel(page, ".tut-arrow"))
        check("and says what moved",
              panel(page, ".tut-caption") == "The 2 of 3 descriptor",
              panel(page, ".tut-caption"))
        log.wait(r"display\(\) enter: MultisigWalletDescriptorScreen\b", 240,
                 "the device to read the descriptor")
        check("the wallet really reads the descriptor being transferred", True)

        first = True
        for how, expect, name in [
            ("empty", "faucet is empty", "faucet-unavailable"),
            ("unreachable", "not reachable", "network-unreachable"),
            ("silent", "did not answer in time", "step-timed-out"),
        ]:
            # The break goes in before the retry, or the retry re-runs the
            # previous failure and this reads the previous message back.
            broken["how"] = how
            if not first:
                page.locator("#tutorial button", has_text="Try again").click()
            first = False
            deadline = time.time() + 300
            while time.time() < deadline:
                if page.locator("#tutorial .tut-verdict[data-state=bad]").count():
                    break
                page.wait_for_timeout(500)
            said = panel(page, ".tut-verdict")
            check(f"{name}: the panel says so in words, in red", expect in said, said)
            check(f"{name}: and offers a way out",
                  page.locator("#tutorial button", has_text="Try again").count() == 1)
            page.screenshot(path=harness.artifact(f"tutorial-fail-{name}.png"), full_page=True)

        check("nothing was left half-transferred on the phone",
              panel(page, ".tut-arrow") == "", panel(page, ".tut-arrow"))

        # --- a narrow phone ----------------------------------------------------
        print("\nat 360px")
        page.set_viewport_size({"width": 360, "height": 780})
        page.locator(".tut-fold.floating > summary").click()
        page.wait_for_timeout(500)
        width = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
        check("nothing pushes the page sideways with the details open",
              width[0] <= width[1], f"{width[0]}px of content in {width[1]}px")
        page.screenshot(path=harness.artifact("tutorial-360-running.png"), full_page=True)
        page.close()

        # The picker lives inside the wallet drawer on ordinary pages; only the
        # tutorial supported by the selected firmware is offered.
        for query, label in [
            ("wallet=1", "Single sig"),
            ("wallet=1&firmware=stock", "Single sig"),
            ("wallet=1&firmware=smartcard", "Multisig"),
            ("wallet=1&firmware=doomsigner", "Multisig"),
            ("wallet=1&firmware=stock&tutorial=offer", "Single sig"),
            ("wallet=1&firmware=smartcard&tutorial=offer", "Multisig"),
        ]:
            resting = context.new_page()
            logs.append(Log(resting))
            resting.set_viewport_size({"width": 360, "height": 780})
            resting.goto(f"{ORIGIN}/wallet.html?{query}&debug=1")
            resting.locator("#wallet-button").wait_for(state="visible")
            picker = resting.locator("#wallet #start-tutorial")
            check(f"{query}: no tutorial or picker is visible before opening the drawer",
                  resting.locator("#tutorial").count() == 0
                  and not resting.locator("#start-tutorial").is_visible())
            resting.locator("#wallet-button").click()
            picker.wait_for(state="visible")
            check(f"{query}: exactly one picker, inside the open wallet drawer",
                  resting.locator("#start-tutorial").count() == 1
                  and picker.is_visible() and resting.locator("#wallet").is_visible()
                  and resting.locator("#tutorial").count() == 0)
            buttons = picker.get_by_role("button")
            expected_labels = ["Single sig", "Multisig"] if label == "Multisig" else [label]
            check(f"{query}: supported tutorials are offered once each",
                  buttons.all_text_contents() == expected_labels
                  and picker.get_by_role("button", name=label, exact=True).is_visible(),
                  repr(buttons.all_text_contents()))
            rest_width = resting.evaluate(
                "() => [document.documentElement.scrollWidth, window.innerWidth]")
            check(f"{query}: the open drawer fits a narrow phone",
                  rest_width[0] <= rest_width[1], f"{rest_width[0]}px in {rest_width[1]}px")
            if query == "wallet=1&firmware=smartcard&tutorial=offer":
                resting.screenshot(path=harness.artifact("tutorial-360-resting.png"),
                                   full_page=True)
            resting.get_by_role("button", name="Close the simulator wallet", exact=True).click()
            picker.wait_for(state="hidden")
            check(f"{query}: closing the drawer hides the picker again",
                  not picker.is_visible())
            resting.close()

        for log in logs:
            errors = [line for line in log.lines if line.startswith("PAGEERROR")]
            check("no page errors", not errors, "; ".join(errors[:2]))

        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
