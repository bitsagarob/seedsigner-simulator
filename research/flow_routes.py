import argparse
import json
import os
from pathlib import Path
import re
import random
import shutil
import sys
import tempfile
import base64
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'test'))
import harness
from harness import Log
from playwright.sync_api import sync_playwright
from run import start_server
import mainnet_reference as reference
import make_qr_y4m
from test_mainnet import expected_xpub_string, read_qr, collect_animated_qr

OUT = ROOT / 'test' / 'artifacts'


class Drive:
    def __init__(self, page, firmware, flow):
        self.page = page
        self.log = Log(page)
        self.firmware = firmware
        self.flow = flow
        self.rows = []
        self.checks = []
        self.prefix = f'flow-{firmware}-{flow}'

    def capture(self, action, screen, note, since):
        self.page.wait_for_timeout(1100)
        assert self.log.last_screen() == screen, (screen, self.log.last_screen())
        number = len(self.rows)
        shot = f'{self.prefix}-{number:03d}-{screen}.png'
        self.page.locator('#screen').screenshot(path=str(OUT / shot))
        entered = [line for line in self.log.lines[since:]
                   if 'display() enter:' in line]
        self.rows.append(dict(step=number, action=action, screen=screen,
                              note=note, screenshot=shot, entered=entered))
        self.save()
        print(f'{self.prefix} {number:03d} {action} -> {screen} ({note})', flush=True)

    def save(self):
        (OUT / f'{self.prefix}.json').write_text(json.dumps(
            dict(firmware=self.firmware, flow=self.flow, rows=self.rows, checks=self.checks), indent=2))
        (OUT / f'{self.prefix}.log').write_text('\n'.join(self.log.lines))

    def boot(self):
        self.page.goto(harness.wallet_url(firmware=self.firmware))
        self.log.wait(r'display\(\) enter: (MainMenuScreen|WarningScreen)', 300, 'boot')
        if self.log.last_screen() == 'WarningScreen':
            self.capture('Open page', 'WarningScreen', 'Boot warning before main menu', 0)
            self.key('Enter', 'MainMenuScreen', 'Acknowledge boot warning', timeout=120)
        self.capture('Main menu ready (no key)', 'MainMenuScreen', 'Scan selected; fresh Testnet session', 0)
        assert self.page.locator('#network').get_attribute('title') == 'Bitcoin network: Testnet'

    def key(self, key, screen, note='', transition=True, timeout=120):
        since = self.log.mark()
        self.page.keyboard.press(key)
        if transition:
            self.log.wait(r'display\(\) enter: ' + screen + r'\b', timeout, note or screen, since)
        self.capture(key, screen, note, since)

    def move(self, key, note=''):
        self.key(key, self.log.last_screen(), note, False)

    def wait(self, screen, note='', timeout=180):
        since = self.log.mark()
        self.log.wait(r'display\(\) enter: ' + screen + r'\b', timeout, note or screen, max(0, since - 10))
        self.capture('Wait (no key)', screen, note, since)


def entropy_reel():
    rng = random.Random(20260916)
    width, height = 640, 480
    with (OUT / 'flow-camera.y4m').open('wb') as out:
        out.write(b'YUV4MPEG2 W640 H480 F25:1 Ip A1:1 C420\n')
        for _ in range(100):
            out.write(b'FRAME\n')
            out.write(bytes(rng.randrange(16, 236) for _ in range(width * height)))
            out.write(bytes([128]) * (width * height // 2))


def image(d):
    d.move('ArrowDown', 'Tools')
    d.key('Enter', 'ButtonListScreen', 'Tools: New seed selected')
    d.key('Enter', 'ToolsImageEntropyLivePreviewScreen', 'Camera entropy preview')
    d.page.wait_for_timeout(2000)
    d.key('Enter', 'ToolsImageEntropyFinalImageScreen', 'Captured image')
    d.key('ArrowRight', 'ButtonListScreen', 'Accept image; word count')
    d.key('Enter', 'DireWarningScreen', '12 words; privacy warning')
    d.key('Enter', 'SeedWordsScreen', 'Words 1–4')
    d.key('Enter', 'SeedWordsScreen', 'Words 5–8')
    d.key('Enter', 'SeedWordsScreen', 'Words 9–12')
    d.key('Enter', 'SeedWordsBackupTestPromptScreen', 'Backup check')
    d.move('ArrowDown', 'Skip' if d.firmware == 'stock' else 'Review')
    d.move('ArrowDown', 'Still Skip (clamped at bottom)' if d.firmware == 'stock' else 'Skip')
    if d.firmware == 'doomsigner':
        d.key('Enter', 'SeedOptionsScreen', 'Skip backup check; no finalize screen')
    else:
        d.key('Enter', 'SeedFinalizeScreen', 'Skip backup check')
        d.key('Enter', 'SeedOptionsScreen', 'Done')
    d.checks.append('Image-entropy seed finalized to SeedOptionsScreen')


def reel(payload):
    make_qr_y4m.LEAD_IN_FRAMES = 150
    make_qr_y4m.write_y4m(str(OUT / 'flow-camera.next.y4m'), payload)
    os.replace(OUT / 'flow-camera.next.y4m', OUT / 'flow-camera.y4m')


def scan(d, finalize=True):
    reel(make_qr_y4m.SEEDQR)
    d.key('Enter', 'ScanScreen', 'Camera pointed at numeric SeedQR')
    d.wait('SeedFinalizeScreen', 'Camera decodes the published test seed')
    if finalize:
        d.key('Enter', 'SeedOptionsScreen', 'Done')


def home(d):
    d.move('ArrowUp', 'Back arrow')
    d.key('Enter', 'MainMenuScreen', 'Return to main menu; seed retained')


def open_seed(d):
    d.move('ArrowRight', 'Seeds')
    d.key('Enter', 'ButtonListScreen', 'Loaded seed list')
    d.key('Enter', 'SeedOptionsScreen', 'Choose the only loaded seed')


def export(d, multi=False, phrase=''):
    d.move('ArrowDown', 'Export Xpub')
    d.key('Enter', 'ButtonListScreen', 'Signature type; Single Sig selected')
    if multi:
        d.move('ArrowDown', 'Multisig')
    d.key('Enter', 'ButtonListScreen', 'Script type; Native Segwit selected')
    d.key('Enter', 'ButtonListScreen', 'QR format; animated selected')
    d.move('ArrowDown', 'Static QR')
    d.key('Enter', 'WarningScreen', 'Xpub privacy warning')
    d.key('Enter', 'SeedExportXpubDetailsScreen', 'Account key details')
    d.key('Enter', 'QRDisplayScreen', 'Export account key as QR')
    d.page.add_script_tag(url='jsQR.js')
    drawn = read_qr(d.page)
    path = "m/48'/1'/0'/2'" if multi else "m/84'/1'/0'"
    version = bytes.fromhex('02575483' if multi else '045f1cf6')
    root = reference.root_from_mnemonic(make_qr_y4m.MNEMONIC, phrase)
    expected = expected_xpub_string(root, path, version)
    assert drawn == expected, (drawn, expected)
    d.checks.append(dict(path=path, fingerprint=root.fingerprint.hex(), exported=drawn))


def passphrase(d):
    scan(d)
    home(d)
    scan(d, False)
    d.move('ArrowDown', 'Add/type BIP39 passphrase')
    d.key('Enter', 'SeedAddPassphraseScreen', 'Passphrase keyboard')
    for key, note in [('Enter', 'a'), ('ArrowRight', 'b selected'), ('Enter', 'ab'),
                      ('ArrowRight', 'c selected'), ('Enter', 'abc')]:
        d.move(key, note)
    since = d.log.mark()
    d.key('3', 'SeedReviewPassphraseScreen', 'Save abc; review changed fingerprint')
    d.log.wait(r"display\(\) exit: SeedAddPassphraseScreen -> " + re.escape(repr({'passphrase': 'abc'})),
               10, 'exact passphrase', since)
    if d.firmware == 'stock':
        d.move('ArrowDown', 'Done, not Edit passphrase')
    d.key('Enter', 'SeedOptionsScreen', 'Done with passphrase')
    export(d, phrase='abc')


def signing(d):
    scan(d)
    home(d)
    root = reference.root_from_mnemonic(make_qr_y4m.MNEMONIC)
    path = "m/84'/1'/0'/0/0"
    key = root.derive(path)
    txid = reference.sha256(b'flow routes fabricated UTXO; never existed')
    recipient = reference.hash160(reference.sha256(b'flow routes fabricated recipient'))
    outputs = [(90000, reference.p2wpkh_script(recipient))]
    script = reference.p2wpkh_script(reference.hash160(key.public))
    psbt = reference.build_psbt(txid, 0, 100000, script, outputs,
                                key.public, root.fingerprint, path)
    reel(base64.b64encode(psbt).decode())
    d.key('Enter', 'ScanScreen', 'Scan fabricated unsigned single-sig PSBT')
    d.wait('ButtonListScreen', 'Choose matching loaded seed')
    d.key('Enter', 'PSBTOverviewScreen', 'Transaction overview')
    if d.firmware == 'doomsigner':
        d.key('Enter', 'WarningScreen', 'Review carefully: fee unusually large share')
    d.key('Enter', 'WarningScreen', 'Full spend: no change output')
    d.key('Enter', 'PSBTMathScreen', '100000 sats in, 90000 out, 10000 fee')
    d.key('Enter', 'PSBTAddressDetailsScreen', 'Recipient address')
    d.key('Enter', 'PSBTFinalizeScreen', 'Approve transaction')
    d.key('Enter', 'QRDisplayScreen', 'Signed PSBT QR')
    d.page.add_script_tag(url='jsQR.js')
    signed, frames = collect_animated_qr(d.page)
    assert signed is not None
    returned_tx, signatures = reference.read_psbt(signed)
    assert returned_tx == reference.unsigned_transaction(txid, 0, outputs)
    assert len(signatures) == 1 and key.public in signatures
    sig = signatures[key.public]
    r, s = reference.decode_der(sig[:-1])
    sighash = reference.bip143_sighash(txid, 0, b'\x76\xa9\x14' + reference.hash160(key.public) + b'\x88\xac', 100000, outputs)
    assert sig[-1] == 1 and s <= reference.N // 2
    assert reference.verify_signature(key.public, sighash, r, s)
    assert not reference.verify_signature(key.public, sighash, r, s ^ 1)
    d.checks.append(dict(signature=sig.hex(), path=path, frames=len(frames),
                         verified='Independent BIP143/ECDSA verifier; original transaction unchanged'))


def pin(d, screen, note):
    for i in range(4):
        d.move('Enter', 'PIN ' + 'a' * (i + 1))
    d.key('3', screen, note)


def cards(d):
    scan(d)
    home(d)
    open_seed(d)
    assert d.page.locator('.cardtray-kind').nth(0).inner_text() == 'SeedKeeper'
    d.page.locator('.cardtray-card').nth(0).click()
    d.page.locator('#screen').click()
    d.capture('Click Card A in tray, then screen to remove focus', 'SeedOptionsScreen', 'Blank SeedKeeper inserted; screen click sends no device key', d.log.mark())
    for i in range(3):
        d.move('ArrowDown', 'Backup seed' if i == 2 else 'Move toward Backup seed')
    d.key('Enter', 'ButtonListScreen', 'Backup seed menu')
    d.move('ArrowDown', 'To SeedKeeper')
    d.key('Enter', 'SeedAddPassphraseScreen', 'PIN prompt for blank card')
    pin(d, 'WarningScreen', 'Card not initialized')
    d.key('Enter', 'SeedAddPassphraseScreen', 'Choose new PIN')
    pin(d, 'SeedAddPassphraseScreen', 'Confirm new PIN')
    pin(d, 'LargeIconStatusScreen', 'Card initialized')
    d.key('Enter', 'SeedAddPassphraseScreen', 'Secret label defaults to b2269592')
    mark = d.log.mark()
    d.key('3', 'LargeIconStatusScreen', 'Seed saved to card')
    stored = d.log.wait(r"\[card\] Card A stored secret (\d+), type 0x10 subtype 0x01, label 'b2269592', 84 bytes", 30, 'card stored masterseed', mark)
    d.checks.append(stored.group(0))
    d.key('Enter', 'SeedOptionsScreen', 'Return to seed options')
    discard_steps = 6 if d.firmware == 'doomsigner' else 5
    for i in range(discard_steps):
        d.move('ArrowDown', 'Discard seed' if i == discard_steps - 1 else 'Move toward Discard seed')
    d.key('Enter', 'WarningScreen', 'Discard confirmation')
    d.move('ArrowDown', 'Discard selected')
    d.key('Enter', 'MainMenuScreen', 'Discarded; wallet holds no seed')
    d.move('ArrowRight', 'Seeds')
    d.key('Enter', 'ButtonListScreen', 'Load a seed; none loaded')
    for i in range(3):
        d.move('ArrowDown', 'From SeedKeeper' if i == 2 else 'Move toward From SeedKeeper')
    mark = d.log.mark()
    if d.firmware == 'doomsigner':
        d.key('Enter', 'ButtonListScreen', 'Stored secret list; same-card PIN reused')
        d.log.wait('Same card, using existing PIN, already loaded', 10, 'cached PIN reused', mark)
    else:
        d.key('Enter', 'SeedAddPassphraseScreen', 'PIN to read Card A')
        pin(d, 'ButtonListScreen', 'Stored secret list')
    d.key('Enter', 'SeedFinalizeScreen', 'Read b2269592 off card')
    d.log.wait(r"\[card\] Card A exporting secret \d+ in the clear, label 'b2269592'", 30, 'card export', mark)
    d.key('Enter', 'SeedOptionsScreen', 'Finalize restored seed')
    export(d)


def menus(d):
    d.move('ArrowRight', 'Seeds')
    d.key('Enter', 'ButtonListScreen', 'Load a seed menu; no seed loaded')
    for i in range(8):
        d.move('ArrowDown', f'Inspect Load a seed menu, Down {i + 1}')
    for i in range(12):
        d.move('ArrowUp', f'Climb to back arrow, Up {i + 1}')
    d.key('Enter', 'MainMenuScreen', 'Back to main menu')
    scan(d)
    for i in range(10):
        d.move('ArrowDown', f'Inspect finalized seed options, Down {i + 1}')
    for i in range(12):
        d.move('ArrowUp', f'Climb to back arrow, Up {i + 1}')
    d.key('Enter', 'MainMenuScreen', 'Back to main menu')
    d.move('ArrowDown', 'Tools')
    d.key('Enter', 'ButtonListScreen', 'Tools menu')
    for i in range(10):
        d.move('ArrowDown', f'Inspect Tools menu, Down {i + 1}')
    d.checks.append(dict(card_tray_count=d.page.locator('.cardtray-card').count()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--firmware', default='stock', choices=['stock', 'smartcard', 'doomsigner'])
    parser.add_argument('--flow', default='image', choices=['image', 'scan', 'passphrase', 'single', 'multi', 'sign', 'cards', 'menus'])
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    tempfile.tempdir = str(OUT)
    unpacked = make_qr_y4m.vendored_libraries(harness.find_asset('wallet-smartcard.zip'))
    if args.flow == 'image':
        entropy_reel()
    else:
        reel(make_qr_y4m.SEEDQR)
    server = start_server()
    try:
        with sync_playwright() as p:
            launch_args = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream']
            launch_args.append(f'--use-file-for-fake-video-capture={OUT / "flow-camera.y4m"}')
            browser = p.chromium.launch(args=launch_args)
            context = browser.new_context(permissions=['camera'], viewport={'width': 900, 'height': 1100})
            d = Drive(context.new_page(), args.firmware, args.flow)
            try:
                d.boot()
                if args.flow in ('single', 'multi'):
                    scan(d)
                    home(d)
                    open_seed(d)
                    export(d, multi=args.flow == 'multi')
                else:
                    {'image': image, 'scan': scan, 'passphrase': passphrase,
                     'sign': signing, 'cards': cards, 'menus': menus}[args.flow](d)
                d.checks.append('PASS')
            finally:
                d.save()
                browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(unpacked)


if __name__ == '__main__':
    main()
