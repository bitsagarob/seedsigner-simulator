import base64
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import mainnet_reference as reference
from harness import Log, check, report
from signet_bridge import API, serve_at

from playwright.sync_api import sync_playwright

ORIGIN = API.rsplit("/api", 1)[0]
PATH = "m/84'/1'/0'"
TPUB = bytes.fromhex("043587cf")
VALUE = 1000000
FEE = 1000
CURRENT = "window.WalletTutorial.current"


def load_embit():
    wallet_zip = harness.find_asset("wallet-stock.zip")
    if not wallet_zip:
        raise AssertionError("wallet-stock.zip is required; do not rebuild shared assets")
    sys.path.insert(0, wallet_zip)


class OfflineChain:
    def __init__(self, context):
        self.claims = []
        self.broadcasts = []
        self.proofs = []
        self.transactions = {}
        self.unexpected = []
        self.log = None
        self.hold_first_claim = False
        self.pending_claim = None
        context.route("**/*", self.block)
        serve_at(context, harness.PORT, ORIGIN)
        context.route(f"{API}/**", self.api)
        context.route("**/mt.js*", lambda route: route.fulfill(status=200, body=""))
        context.route("**/mt.php*", lambda route: route.fulfill(status=200, body=""))

    def release_claim(self):
        assert self.pending_claim is not None
        route, body = self.pending_claim
        self.pending_claim = None
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def block(self, route):
        self.unexpected.append(route.request.url)
        route.abort()

    def api(self, route):
        from embit import script
        from embit.transaction import Transaction

        request = route.request
        url = urlsplit(request.url)
        mark = self.log.mark() if self.log else 0
        status = 200
        if url.path == "/api/status" and request.method == "GET":
            body = {"height": 1234, "last_block_age_seconds": 1, "block_seconds": 30}
        elif url.path == "/api/claim" and request.method == "POST":
            address = json.loads(request.post_data)["address"]
            output = script.address_to_scriptpubkey(address).data
            raw = reference.unsigned_transaction(
                reference.sha256(f"offline tutorial funding {len(self.claims)}".encode()),
                0, [(12345, b"\x6a"), (VALUE, output)])
            txid = reference.double_sha256(raw)[::-1].hex()
            self.transactions[txid] = raw.hex()
            self.claims.append({"address": address, "txid": txid, "vout": 1,
                                "value": VALUE, "script": output, "mark": mark})
            body = {"ok": True, "txid": txid}
            if self.hold_first_claim and len(self.claims) == 1:
                self.pending_claim = (route, body)
                return
        elif url.path == "/api/broadcast" and request.method == "POST":
            raw = json.loads(request.post_data)["tx"]
            txid = Transaction.parse(bytes.fromhex(raw)).txid().hex()
            self.transactions[txid] = raw
            self.broadcasts.append({"txid": txid, "hex": raw, "mark": mark})
            body = {"ok": True, "txid": txid}
        elif url.path == "/api/tx-proof" and request.method == "GET":
            txid = parse_qs(url.query).get("txid", [""])[0]
            if txid in self.transactions:
                self.proofs.append({"txid": txid, "mark": mark})
                body = {"txid": txid, "tx": self.transactions[txid], "height": 1234}
            else:
                self.unexpected.append(request.url)
                status, body = 404, {"error": "unknown offline transaction"}
        else:
            self.unexpected.append(request.method + " " + request.url)
            status, body = 503, {"error": "no real API is allowed in this test"}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def wait(page, predicate, what, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        bad = page.locator('#tutorial .tut-verdict[data-state="bad"]')
        if bad.count():
            raise AssertionError(f"{what}: {bad.inner_text()}")
        if page.evaluate("() => { const t = " + CURRENT + "; return !!(" + predicate + "); }"):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {what}: " + str(page.evaluate(
        "() => { const t = " + CURRENT + "; return [t.at, t.atAction, "
        "t.stepText.textContent, t.doText.textContent, t.currentScreen()]; }")))


def evidence(page):
    return page.evaluate("""() => {
      const t = window.WalletTutorial.current;
      return JSON.parse(JSON.stringify({state: t.state, finished: t.finished},
        (key, value) => typeof value === 'bigint' ? value.toString()
          : value instanceof Uint8Array ? Array.from(value) : value));
    }""")


def control(page, name):
    page.locator(f'#tutorial button[aria-label="{name}"]').click()


def quiet(page, log):
    wait(page, "t.paused && !t.performing", "the in-flight action to finish")
    previous = None
    stable = time.monotonic()
    deadline = stable + 30
    while time.monotonic() < deadline:
        now = page.evaluate("() => { const t = " + CURRENT + "; return "
                            "[t.at, t.atAction, t.fraction, t.currentScreen(), t.doText.textContent]; }")
        if now != previous:
            previous, stable = now, time.monotonic()
        elif time.monotonic() - stable >= 2:
            mark = log.mark()
            page.wait_for_timeout(2000)
            assert not log.seen(r"display\(\) enter:", mark), "Pause still drives firmware screens"
            assert now == page.evaluate("() => { const t = " + CURRENT + "; return "
                                       "[t.at, t.atAction, t.fraction, t.currentScreen(), t.doText.textContent]; }")
            return now
        page.wait_for_timeout(100)
    raise AssertionError("Pause never settled")


def step_until(page, predicate):
    for _ in range(16):
        if page.evaluate("() => { const t = " + CURRENT + "; return !!(" + predicate + "); }"):
            return
        control(page, "One step")
        wait(page, "t.paused && !t.stepOnce && !t.performing", "One step to stop again")
    raise AssertionError("One step did not reach " + predicate)


def boot(context, chain, query, page=None, log=None):
    if page is None:
        page = context.new_page()
        log = Log(page)
        chain.log = log
        page.goto(f"{ORIGIN}/wallet.html?wallet=1&debug=1&{query}")
    page.wait_for_function("window.WalletTutorial && window.WalletTutorial.current", timeout=60000)
    log.wait(r"display\(\) enter: MainMenuScreen\b", 180, "stock firmware boot")
    assert page.evaluate("window.__firmware") == "stock"
    assert page.evaluate(CURRENT + ".id") == "single"
    assert page.locator(".cardtray-card").count() == 0
    check("registry offers single on stock and multisig on all three firmwares",
          page.evaluate("() => Object.fromEntries(Object.entries(window.WalletTutorial.registry)"
                        ".map(([id, entry]) => [id, entry.firmwares]))")
          == {"single": ["stock"], "multi": ["smartcard", "doomsigner", "stock"]})
    page.evaluate("() => { const t = " + CURRENT + "; "
                  "t.pace = () => Promise.resolve(); t.beat = () => Promise.resolve(); }")
    return page, log


def start(page):
    control(page, "Play")
    page.get_by_role("button", name="Random noise instead", exact=True).click()


def mnemonic_from_qr(seedqr):
    from embit.wordlists.bip39 import WORDLIST

    assert re.fullmatch(r"\d{48}", seedqr), "not a twelve-word optical Standard SeedQR"
    indices = [int(seedqr[i:i + 4]) for i in range(0, 48, 4)]
    assert all(index < 2048 for index in indices)
    bits = "".join(f"{index:011b}" for index in indices)
    entropy = int(bits[:128], 2).to_bytes(16, "big")
    assert int(bits[128:], 2) == reference.sha256(entropy)[0] >> 4, "invalid BIP39 checksum"
    return " ".join(WORDLIST[index] for index in indices)


def ordered(log, screens, start=0, end=None):
    position = 0
    for line in log.lines[start:end]:
        if re.search(r"display\(\) enter: " + screens[position] + r"\b", line):
            position += 1
            if position == len(screens):
                return True
    return False


def prove_round(label, mnemonic, round_, claim, broadcast, proofs):
    from embit.descriptor import Descriptor
    from embit.networks import NETWORKS
    from embit.psbt import PSBT
    from embit.transaction import Transaction

    phrase = "abc" if round_["passphrase"] else ""
    root = reference.root_from_mnemonic(mnemonic, phrase)
    alternate = reference.root_from_mnemonic(mnemonic, "" if phrase else "abc")
    tpub = root.derive(PATH).extended_public_key(TPUB)
    account = round_["account"]
    check(f"{label}: optical account fingerprint, path and complete tpub match independent BIP39/BIP32",
          account == {"fingerprint": root.fingerprint.hex(), "path": "/84h/1h/0h", "tpub": tpub},
          account.get("fingerprint", ""))
    check(f"{label}: the other passphrase cannot produce this account",
          tpub != alternate.derive(PATH).extended_public_key(TPUB)
          and root.fingerprint != alternate.fingerprint)
    descriptor = f"wpkh([{root.fingerprint.hex()}/84h/1h/0h]{tpub}/{{0,1}}/*)"
    check(f"{label}: descriptor is built from the independent account",
          round_["wallet"]["descriptor"] == descriptor)
    parsed = Descriptor.from_string(descriptor)
    index = round_["index"]
    assert index == 0, "single walkthrough should start at address zero"
    for branch, name in ((0, "receive"), (1, "change")):
        leaf = parsed.derive(index, branch_index=branch)
        address = leaf.address(NETWORKS["test"])
        expected_script = reference.p2wpkh_script(reference.hash160(
            root.derive(f"{PATH}/{branch}/{index}").public))
        check(f"{label}: {name} address and script match independent derivation",
              round_[name]["address"] == address
              and bytes(round_[name]["scriptPubkey"]) == expected_script
              and leaf.script_pubkey().data == expected_script)
    check(f"{label}: fabricated faucet really pays the claimed independent receive address",
          claim["address"] == parsed.derive(index, branch_index=0).address(NETWORKS["test"])
          and claim["script"] == bytes(round_["receive"]["scriptPubkey"]))
    check(f"{label}: spend selects the matching faucet output, not output zero",
          round_["input"]["txid"] == claim["txid"]
          and round_["input"]["vout"] == claim["vout"] == 1
          and int(round_["input"]["value"]) == VALUE)
    outputs = [(VALUE - FEE, parsed.derive(index, branch_index=1).script_pubkey().data)]
    expected_tx = reference.unsigned_transaction(bytes.fromhex(claim["txid"]), 1, outputs)
    unsigned = PSBT.from_string(round_["psbt"])
    signed_tx, partials = reference.read_psbt(base64.b64decode(round_["signed"]))
    check(f"{label}: QR round-trip signs exactly the independently constructed spend",
          unsigned.tx.serialize() == signed_tx == expected_tx)
    raw = bytes.fromhex(broadcast["hex"])
    tx = Transaction.parse(raw)
    expected_id = reference.double_sha256(expected_tx)[::-1].hex()
    check(f"{label}: captured POST contains the actual final transaction and independent txid",
          broadcast["hex"] == round_["spend"]["hex"]
          and broadcast["txid"] == round_["spend"]["txid"] == expected_id)
    assert len(tx.vin) == 1 and len(tx.vin[0].witness.items) == 2, "not a single P2WPKH witness"
    signature, public = tx.vin[0].witness.items
    key = root.derive(f"{PATH}/0/{index}").public
    check(f"{label}: final witness carries the device's optical PSBT signature and reference key",
          public == key and partials == {key: signature})
    check(f"{label}: witness-free transaction is unchanged by finalisation",
          tx.version == 2 and tx.locktime == 0
          and tx.vin[0].txid.hex() == claim["txid"] and tx.vin[0].vout == 1
          and tx.vin[0].sequence == 0xFFFFFFFD and tx.vin[0].script_sig.data == b""
          and [(out.value, out.script_pubkey.data) for out in tx.vout] == outputs)
    assert signature[-1] == 1, "signature must commit to SIGHASH_ALL"
    r, s = reference.decode_der(signature[:-1])
    script_code = b"\x76\xa9\x14" + reference.hash160(key) + b"\x88\xac"
    digest = reference.bip143_sighash(bytes.fromhex(claim["txid"]), 1, script_code, VALUE, outputs)
    check(f"{label}: independent BIP143/ECDSA verifies the actual low-S signature",
          s <= reference.N // 2 and reference.verify_signature(key, digest, r, s))
    changed = reference.bip143_sighash(bytes.fromhex(claim["txid"]), 1, script_code,
                                     VALUE, [(VALUE - FEE - 1, outputs[0][1])])
    check(f"{label}: verifier rejects a changed signature, amount and passphrase key",
          not reference.verify_signature(key, digest, r, s ^ 1)
          and not reference.verify_signature(key, changed, r, s)
          and not reference.verify_signature(alternate.derive(f"{PATH}/0/{index}").public,
                                             digest, r, s))
    check(f"{label}: funding and spend each requested an offline confirmation",
          round_["confirmed"] is True
          and sum(item["txid"] == claim["txid"] for item in proofs) >= 2
          and any(item["txid"] == expected_id for item in proofs))


def prove_run(page, log, chain, label, expected_phrases):
    wait(page, "t.finished && t.state.rounds.every(r => r.confirmed)", "all rounds confirmed", 240)
    got = evidence(page)
    rounds = got["state"]["rounds"]
    assert [r["passphrase"] for r in rounds] == expected_phrases
    assert len(chain.claims) == len(chain.broadcasts) == len(rounds), "duplicate or missing network work"
    mnemonic = mnemonic_from_qr(got["state"]["seedqr"])
    check(f"{label}: firmware creates, backs up and exports the seed through real screens",
          ordered(log, ["ToolsImageEntropyLivePreviewScreen", "ToolsImageEntropyFinalImageScreen",
                        "SeedWordsScreen", "SeedFinalizeScreen", "SeedOptionsScreen",
                        "SeedTranscribeSeedQRWholeQRScreen", "SeedExportXpubDetailsScreen",
                        "QRDisplayScreen"]))
    first_scan = next((i for i, line in enumerate(log.lines)
                       if "display() enter: ScanScreen" in line), len(log.lines))
    check(f"{label}: initial seed is generated, not injected through Scan",
          ordered(log, ["ToolsImageEntropyLivePreviewScreen", "SeedFinalizeScreen"], end=first_scan))
    for i, round_ in enumerate(rounds):
        start_mark = 0 if i == 0 else chain.broadcasts[i - 1]["mark"]
        check(f"{label} round {i + 1}: real firmware reviews and approves before broadcast",
              ordered(log, ["ScanScreen", "PSBTOverviewScreen", "PSBTFinalizeScreen", "QRDisplayScreen"],
                      start_mark, chain.broadcasts[i]["mark"]))
        prove_round(f"{label} round {i + 1}", mnemonic, round_, chain.claims[i],
                    chain.broadcasts[i], chain.proofs)
    phrase_returns = [line for line in log.lines
                      if "display() exit: SeedAddPassphraseScreen -> {'passphrase': 'abc'}" in line]
    check(f"{label}: firmware keyboard returns abc exactly once per passphrase round",
          len(phrase_returns) == sum(expected_phrases))
    check(f"{label}: no firmware exception, page error or unhandled real endpoint",
          not log.seen(r"RAISED|PAGEERROR|Traceback|display\(\) enter: UnhandledException")
          and not chain.unexpected, repr(chain.unexpected))
    return got


def late_passphrase(page, log, chain):
    wait(page, "t.state && t.state.rounds[0].claimPending", "first faucet request")
    deadline = time.monotonic() + 10
    while chain.pending_claim is None and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert chain.pending_claim is not None, "first claim response was not intercepted"
    control(page, "Pause")
    quiet(page, log)
    pending = evidence(page)["state"]
    check("Back is disabled while the real faucet request is held pending",
          page.locator('#tutorial button[aria-label="Back"]').is_disabled()
          and pending["rounds"][0]["claimPending"]
          and not pending["rounds"][0].get("funding")
          and len(chain.claims) == 1 and not chain.broadcasts)
    assert page.locator('#tutorial button[aria-label="Back"]').is_disabled()
    chain.release_claim()
    wait(page, "!t.state.rounds[0].claimPending && t.state.rounds[0].funding",
         "held faucet response to settle")
    step_until(page, "t.state.rounds[0].psbt")
    before = evidence(page)["state"]
    assert before["rounds"][0]["passphrase"] is False
    assert log.last_screen() == "MainMenuScreen"
    check("unchecked: no passphrase keyboard before the original spend is prepared",
          not log.seen(r"display\(\) enter: SeedAddPassphraseScreen"))
    page.locator("#tutorial-passphrase").check()
    scheduled = evidence(page)["state"]
    check("late tick preserves the prepared original wallet and schedules one extra round",
          len(scheduled["rounds"]) == 2 and scheduled["rounds"][0] == before["rounds"][0]
          and scheduled["activeRound"] == scheduled["rounds"][0])
    page.locator("#tutorial-passphrase").uncheck()
    check("unticking does not destroy the original wallet or queued passphrase round",
          evidence(page)["state"] == scheduled)
    page.locator("#tutorial-passphrase").check()
    check("ticking again does not duplicate the queued round",
          evidence(page)["state"] == scheduled)
    back = page.locator('#tutorial button[aria-label="Back"]')
    assert back.is_enabled(), "Back should be safe at the prepared-spend home screen"
    mark = log.mark()
    back.click()
    quiet(page, log)
    check("Back lands paused without changing firmware, seed, account, UTXO or PSBT",
          not log.seen(r"display\(\) enter:", mark)
          and evidence(page)["state"] == scheduled
          and page.locator('#tutorial button[aria-label="Play"]').is_visible())
    control(page, "Play")
    wait(page, "t.state.rounds[1].account", "passphrase account export")
    control(page, "Pause")
    quiet(page, log)
    step_until(page, "t.state.rounds[1].psbt")
    passphrase_state = evidence(page)["state"]
    assert passphrase_state["rounds"][0]["confirmed"]
    assert passphrase_state["activeRound"] == passphrase_state["rounds"][1]
    assert log.last_screen() == "MainMenuScreen"
    assert back.is_enabled(), "Back must work on the active passphrase round before signing"
    mark = log.mark()
    back.click()
    quiet(page, log)
    check("Back after passphrase wallet build preserves both rounds and active seed exactly",
          evidence(page)["state"] == passphrase_state
          and not log.seen(r"display\(\) enter:", mark)
          and page.locator('#tutorial button[aria-label="Play"]').is_visible())
    control(page, "Play")
    got = prove_run(page, log, chain, "unchecked then late tick", [False, True])
    first_id = chain.broadcasts[0]["txid"]
    confirmed_at = next(item["mark"] for item in chain.proofs if item["txid"] == first_id)
    check("late round only discards and reloads after original spend confirmation",
          ordered(log, ["SeedOptionsScreen", "WarningScreen", "MainMenuScreen", "ScanScreen",
                        "SeedFinalizeScreen", "SeedAddPassphraseScreen", "SeedReviewPassphraseScreen",
                        "SeedOptionsScreen", "SeedExportXpubDetailsScreen"],
                  confirmed_at, chain.claims[1]["mark"]))
    a, b = got["state"]["rounds"]
    check("same optical seed produces two independently proved wallets and spends",
          a["account"]["tpub"] != b["account"]["tpub"]
          and a["receive"]["address"] != b["receive"]["address"]
          and a["spend"]["txid"] != b["spend"]["txid"])


def pending_finalize(page, log):
    log.wait(r"display\(\) enter: SeedWordsScreen\b", 90, "seed words before finalization")
    control(page, "Pause")
    quiet(page, log)
    step_until(page, "t.currentScreen() === 'SeedFinalizeScreen'")
    step_until(page, "t.steps[t.at].title === 'Finish loading the seed'")
    assert log.last_screen() == "SeedFinalizeScreen"
    assert not log.seen(r"display\(\) enter: SeedOptionsScreen")
    page.locator(".tut-fold.floating > summary").click()
    page.locator(".tut-fold.floating").get_by_role("button", name="I will drive", exact=True).click()
    wait(page, "t.mode === 'hands' && t.waitingForHands && t.doText.textContent === 'Done'",
         "hands mode awaiting Done on pending seed")
    mark = log.mark()
    page.wait_for_timeout(1500)
    assert log.last_screen() == "SeedFinalizeScreen"
    assert not log.seen(r"display\(\) enter:", mark)
    page.locator("#tutorial-passphrase").check()
    wait(page, "t.waitingForHands && t.steps[t.at].title === 'Add a passphrase'",
         "pending seed passphrase inserted in hands mode")
    state = evidence(page)["state"]
    check("pending-finalize tick inserts the passphrase before Done, not a second round",
          len(state["rounds"]) == 1 and state["rounds"][0]["passphrase"]
          and log.last_screen() == "SeedFinalizeScreen"
          and not log.seen(r"display\(\) enter: SeedOptionsScreen", mark))
    page.locator(".tut-fold.floating > summary").click()
    page.locator(".tut-fold.floating").get_by_role("button", name="Let it drive", exact=True).click()


def restart(page, log):
    mark = log.mark()
    with page.expect_navigation():
        control(page, "Begin again")
    params = parse_qs(urlsplit(page.url).query)
    page.wait_for_function("window.WalletTutorial && window.WalletTutorial.current", timeout=60000)
    log.wait(r"display\(\) enter: MainMenuScreen\b", 180, "fresh firmware after restart", mark)
    check("Begin again preserves single/stock/passphrase and asks for entropy again",
          all(params.get(key) == [value] for key, value in
              (("tutorial", "single"), ("firmware", "stock"), ("passphrase", "1"), ("mode", "play")))
          and page.locator("#tutorial-passphrase").is_checked()
          and page.get_by_role("button", name="Random noise instead", exact=True).is_visible())
    check("Begin again clears all previous seed, wallet and spend state",
          page.evaluate("() => { const t = " + CURRENT + "; return "
                        "!t.finished && !t.state && t.mode === 'idle'; }")
          and log.last_screen() == "MainMenuScreen")


def main():
    load_embit()
    for name, passed, detail in reference.check_published_vectors():
        check(name, passed, detail)
        assert passed, name
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scenario in ("prechecked", "late", "early", "pending-finalize"):
            context = browser.new_context(viewport={"width": 1000, "height": 1300},
                                          service_workers="block")
            chain = OfflineChain(context)
            chain.hold_first_claim = scenario == "late"
            query = "tutorial=single" if scenario != "prechecked" else "firmware=stock"
            page = None
            try:
                if scenario == "prechecked":
                    page = context.new_page()
                    log = Log(page)
                    chain.log = log
                    page.goto(f"{ORIGIN}/wallet.html?wallet=1&debug=1&{query}")
                    page.locator("#wallet-button").wait_for(state="visible")
                    check("stock picker is hidden until the wallet drawer opens",
                          not page.locator("#start-tutorial").is_visible()
                          and page.locator("#tutorial").count() == 0)
                    page.locator("#wallet-button").click()
                    picker = page.locator("#wallet #start-tutorial")
                    picker.wait_for(state="visible")
                    check("stock drawer offers Single sig, Multisig and an unchecked passphrase",
                          picker.get_by_role("button").all_text_contents() == ["Single sig", "Multisig"]
                          and not picker.get_by_role("checkbox").is_checked())
                    picker.get_by_role("checkbox").check()
                    log.lines.clear()
                    with page.expect_navigation():
                        picker.get_by_role("button", name="Single sig", exact=True).click()
                    params = parse_qs(urlsplit(page.url).query)
                    assert all(params.get(key) == [value] for key, value in
                               (("tutorial", "single"), ("firmware", "stock"), ("passphrase", "1")))
                page, log = boot(context, chain, query, page,
                                 log if page is not None else None)
                check(f"{scenario}: actual single tutorial and stock firmware mounted",
                      page.evaluate(CURRENT + ".firmware") == "stock"
                      and page.locator("#tutorial-passphrase").is_checked() == (scenario == "prechecked"))
                start(page)
                if scenario == "late":
                    late_passphrase(page, log, chain)
                else:
                    if scenario == "early":
                        log.wait(r"display\(\) enter: ToolsImageEntropyLivePreviewScreen\b", 90,
                                 "early tick while seed generation is in progress")
                        control(page, "Pause")
                        quiet(page, log)
                        assert not log.seen(r"display\(\) enter: SeedFinalizeScreen")
                        page.locator("#tutorial-passphrase").check()
                        control(page, "Play")
                    if scenario == "pending-finalize":
                        pending_finalize(page, log)
                    prove_run(page, log, chain, scenario, [True])
                    check(f"{scenario}: passphrase is inserted before backup/export without discard/reload",
                          ordered(log, ["SeedFinalizeScreen", "SeedAddPassphraseScreen",
                                        "SeedReviewPassphraseScreen", "SeedTranscribeSeedQRWholeQRScreen",
                                        "SeedExportXpubDetailsScreen"]))
                    if scenario == "prechecked":
                        restart(page, log)
            except Exception:
                if page and not page.is_closed():
                    with open(harness.artifact(f"tutorial-single-{scenario}-failure.log"), "w") as handle:
                        handle.write("\n".join(log.lines))
                    page.screenshot(path=harness.artifact(f"tutorial-single-{scenario}-failure.png"),
                                    full_page=True)
                    print("\n".join(log.lines[-45:]), flush=True)
                raise
            finally:
                context.close()
        browser.close()
    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
