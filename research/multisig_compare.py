import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research' / 'multisig-compare'
os.environ.setdefault('SIM_PORT', '8788')
os.environ.setdefault('SIM_ARTIFACT_DIR', str(OUT / 'scratch'))
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'test'))
import flow_routes as routes
import harness
import mainnet_reference as reference
import make_qr_y4m
from playwright.sync_api import sync_playwright

ACCOUNT = "m/48'/1'/0'/2'"
MNEMONICS = (make_qr_y4m.MNEMONIC, ' '.join(['abandon'] * 11 + ['about']),
             make_qr_y4m.MULTISIG_MNEMONICS[2])
OBSERVATION_FILES = ('observations-seed-1.json', 'observations-seeds-2-3.json',
                     'observations-signing.json')


class Drive(routes.Drive):
    def capture(self, action, screen, note, since):
        self.page.wait_for_timeout(1100)
        assert self.log.last_screen() == screen, (screen, self.log.last_screen())
        self.rows.append(dict(action=action, screen=screen, note=note,
                              entered=[line for line in self.log.lines[since:]
                                       if 'display() enter:' in line]))

    def save(self):
        (OUT / f'{self.firmware}-route.json').write_text(json.dumps(
            dict(rows=self.rows, checks=self.checks), indent=2) + '\n')
        (OUT / f'{self.firmware}-debug.log').write_text('\n'.join(self.log.lines) + '\n')


class Ceremony:
    def __init__(self, drives):
        self.drives = drives
        self.rows = []

    def shot(self, step, description, stock_parked=False):
        row = dict(step=step, description=description,
                   pairing='Stock stays on its seed options; no card equivalent.'
                   if stock_parked else 'Same logical action on both firmwares.')
        for firmware, d in self.drives.items():
            path = OUT / f'{firmware}-{step}.png'
            harness.save_screen(d.page, str(path))
            row[firmware + '_png'] = str(path.relative_to(ROOT))
            row[firmware + '_screen'] = d.log.last_screen()
            row[firmware + '_log_line'] = next(
                i + 1 for i in reversed(range(len(d.log.lines)))
                if 'display() enter:' in d.log.lines[i])
            d.save()
        self.rows.append(row)
        (OUT / 'captures.json').write_text(json.dumps(self.rows, indent=2) + '\n')
        print(step, row['stock_screen'], row['smartcard_screen'], flush=True)

    def keys(self, key, screen, step, description):
        for d in self.drives.values():
            d.key(key, screen, description)
        self.shot(step, description)


def select_seed(d, index):
    d.move('ArrowRight', 'Seeds')
    d.key('Enter', 'ButtonListScreen', 'Loaded seeds')
    for _ in range(index):
        d.move('ArrowDown', 'Choose seed')
    d.key('Enter', 'SeedOptionsScreen', 'Seed options')


def card(c, index, fingerprint):
    d = c.drives['smartcard']
    prefix = f'seed-{index + 1}-card-'

    def shot(name, text):
        c.shot(prefix + name, text, stock_parked=True)

    def key(key, screen, name, text):
        d.key(key, screen, text)
        shot(name, text)

    assert d.page.locator('.cardtray-kind').nth(index).inner_text() == 'SeedKeeper'
    d.page.locator('.cardtray-card').nth(index).click()
    d.page.locator('#screen').click()
    for _ in range(3):
        d.move('ArrowDown', 'Backup seed')
    key('Enter', 'ButtonListScreen', 'backup', 'Smartcard offers backup methods; stock keeps the scanned seed in memory.')
    d.move('ArrowDown', 'To SeedKeeper')
    key('Enter', 'SeedAddPassphraseScreen', 'pin', 'Smartcard asks for the inserted SeedKeeper PIN.')
    routes.pin(d, 'WarningScreen', 'Card not initialized')
    shot('blank', 'Smartcard reports that the SeedKeeper is not initialized.')
    key('Enter', 'SeedAddPassphraseScreen', 'new-pin', 'Smartcard asks for a new SeedKeeper PIN.')
    routes.pin(d, 'SeedAddPassphraseScreen', 'Confirm new PIN')
    shot('confirm-pin', 'Smartcard asks to confirm the new SeedKeeper PIN.')
    routes.pin(d, 'LargeIconStatusScreen', 'Card initialized')
    shot('initialized', 'Smartcard confirms SeedKeeper initialization.')
    key('Enter', 'SeedAddPassphraseScreen', 'label', 'Smartcard offers the seed fingerprint as its card label.')
    mark = d.log.mark()
    key('3', 'LargeIconStatusScreen', 'saved', 'Smartcard confirms that the seed was saved to the SeedKeeper.')
    letter = chr(65 + index)
    stored = d.log.wait(
        rf"\[card\] Card {letter} stored secret \d+, type 0x10 subtype 0x01, label '{fingerprint}', 84 bytes",
        30, 'card stored seed', mark)
    d.checks.append(stored.group(0))
    d.key('Enter', 'SeedOptionsScreen', 'Return to seed options')
    for _ in range(5):
        d.move('ArrowDown', 'Discard seed')
    key('Enter', 'WarningScreen', 'discard', 'Smartcard asks before removing the seed from device memory, leaving the card copy.')
    d.move('ArrowDown', 'Discard selected')
    key('Enter', 'MainMenuScreen', 'discarded', 'Smartcard has discarded the current seed from device memory.')
    d.move('ArrowRight', 'Seeds')
    d.key('Enter', 'ButtonListScreen', 'Seed list')
    if index:
        for _ in range(index):
            d.move('ArrowDown', 'Load a seed')
        d.key('Enter', 'ButtonListScreen', 'Load a seed methods')
    shot('load-method', 'Smartcard offers seed loading methods, including SeedKeeper.')
    for _ in range(3):
        d.move('ArrowDown', 'From SeedKeeper')
    key('Enter', 'SeedAddPassphraseScreen', 'read-pin', 'Smartcard asks for the PIN to restore the seed from its SeedKeeper.')
    routes.pin(d, 'ButtonListScreen', 'Stored secret list')
    shot('secrets', 'Smartcard lists the seed stored on the inserted SeedKeeper.')
    mark = d.log.mark()
    key('Enter', 'SeedFinalizeScreen', 'restored', 'Smartcard reads the seed back from its SeedKeeper for finalization.')
    d.log.wait(rf"\[card\] Card {letter} exporting secret \d+ in the clear, label '{fingerprint}'",
               30, 'card exported seed', mark)
    d.key('Enter', 'SeedOptionsScreen', 'Finalize restored seed')
    c.shot(f'seed-{index + 1}-ready', 'Both devices hold the same seed, with the smartcard copy restored from its SeedKeeper.')


def build_transaction(exports):
    from embit import bip32, descriptor, psbt, script, transaction
    keys = []
    for exported in exports:
        origin, key = exported.split(']')
        normalized = bip32.HDKey.from_base58(key).to_base58(version=bytes.fromhex('043587cf'))
        keys.append(origin.replace("'", 'h') + ']' + normalized + '/{0,1}/*')
    text = 'wsh(sortedmulti(2,' + ','.join(keys) + '))'
    desc = descriptor.Descriptor.from_string(text)
    derived = desc.derive(0, branch_index=0)
    txid = reference.sha256(b'multisig comparison fabricated UTXO, never existed')
    recipient = reference.p2wpkh_script(reference.hash160(reference.sha256(b'multisig comparison recipient')))
    tx = transaction.Transaction(version=2, vin=[transaction.TransactionInput(txid, 0, sequence=0xfffffffd)],
        vout=[transaction.TransactionOutput(90000, script.Script(recipient))])
    packet = psbt.PSBT(tx)
    inp = packet.inputs[0]
    inp.witness_utxo = transaction.TransactionOutput(100000, derived.script_pubkey())
    inp.witness_script = derived.witness_script()
    for key in derived.keys:
        inp.bip32_derivations[key.get_public_key()] = psbt.DerivationPath(key.fingerprint, key.derivation)
    assert tx.serialize() == reference.unsigned_transaction(txid, 0, [(90000, recipient)])
    expected_public = sorted(reference.root_from_mnemonic(m).derive(ACCOUNT + '/0/0').public for m in MNEMONICS)
    expected_script = b'\x52' + b''.join(b'\x21' + key for key in expected_public) + b'\x53\xae'
    assert inp.witness_script.data == expected_script
    assert inp.witness_utxo.script_pubkey.data == b'\x00\x20' + reference.sha256(expected_script)
    sighash = reference.bip143_sighash(txid, 0, expected_script, 100000, [(90000, recipient)])
    return text, packet, sighash


def run_ceremony(c):
    from mnemonic import Mnemonic
    from embit import psbt
    for mnemonic in MNEMONICS:
        print(f'BIP39 valid={Mnemonic("english").check(mnemonic)}: {mnemonic}', flush=True)
    assert all(Mnemonic('english').check(m) for m in MNEMONICS)
    wordlist = Mnemonic('english').wordlist
    roots = [reference.root_from_mnemonic(m) for m in MNEMONICS]
    for d in c.drives.values():
        d.boot()
        d.page.add_script_tag(url='jsQR.js')
    c.shot('home', 'Both devices start a fresh Testnet session.')
    for index, mnemonic in enumerate(MNEMONICS):
        routes.reel(''.join(f'{wordlist.index(word):04d}' for word in mnemonic.split()))
        c.keys('Enter', 'ScanScreen', f'seed-{index + 1}-scan', 'Both devices scan the same published seed as a numeric SeedQR.')
        for d in c.drives.values():
            d.wait('SeedFinalizeScreen', 'SeedQR decoded')
        c.shot(f'seed-{index + 1}-finalize', 'Both devices show the scanned seed fingerprint and finalization choices.')
        c.keys('Enter', 'SeedOptionsScreen', f'seed-{index + 1}-loaded', 'Both devices finalize the scanned seed into memory.')
        for d in c.drives.values():
            routes.home(d)
            select_seed(d, index)
        card(c, index, roots[index].fingerprint.hex())
        for d in c.drives.values():
            routes.home(d)
    exports = {firmware: [] for firmware in c.drives}
    for index, root in enumerate(roots):
        prefix = f'key-{index + 1}-'
        for d in c.drives.values():
            select_seed(d, index)
            d.move('ArrowDown', 'Export Xpub')
        c.keys('Enter', 'ButtonListScreen', prefix + 'signature-type', 'Choose the signature type for the account key export.')
        for d in c.drives.values():
            d.move('ArrowDown', 'Multisig')
        c.keys('Enter', 'ButtonListScreen', prefix + 'script-type', 'Choose Native Segwit for the multisig account.')
        c.keys('Enter', 'ButtonListScreen', prefix + 'qr-format', 'Choose the account key QR format.')
        for d in c.drives.values():
            d.move('ArrowDown', 'Static QR')
        c.keys('Enter', 'WarningScreen', prefix + 'privacy', 'Acknowledge the account key privacy warning.')
        c.keys('Enter', 'SeedExportXpubDetailsScreen', prefix + 'details', "Review the multisig account key at m/48'/1'/0'/2'.")
        c.keys('Enter', 'QRDisplayScreen', prefix + 'qr', 'Export the multisig account key as a static QR.')
        for firmware, d in c.drives.items():
            drawn = routes.read_qr(d.page)
            expected = routes.expected_xpub_string(root, ACCOUNT, bytes.fromhex('02575483'))
            assert drawn == expected, (drawn, expected)
            exports[firmware].append(drawn)
            d.checks.append(dict(path=ACCOUNT, exported=drawn, independent_match=True))
            d.key('Enter', 'MainMenuScreen', 'Leave account key QR')
    assert exports['stock'] == exports['smartcard']
    text, original, sighash = build_transaction(exports['stock'])
    (OUT / 'transaction.json').write_text(json.dumps(dict(
        network='Testnet', inputs='Fabricated UTXO, never existed; no chain access or broadcast.',
        descriptor=text, exported_accounts=exports, unsigned_psbt=base64.b64encode(original.serialize()).decode(),
        sighash=sighash.hex()), indent=2) + '\n')
    routes.reel(text)
    c.keys('Enter', 'ScanScreen', 'descriptor-scan', 'Scan the 2-of-3 descriptor assembled from the three exported account keys.')
    for d in c.drives.values():
        d.wait('MultisigWalletDescriptorScreen', 'Parsed 2-of-3 descriptor')
    c.shot('descriptor-review', 'Review the 2-of-3 policy and its three seed fingerprints.')
    for d in c.drives.values():
        d.key('Enter', 'MainMenuScreen', 'Accept descriptor')
    packets = {f: psbt.PSBT.parse(original.serialize()) for f in c.drives}
    evidence = {}
    for signer in range(2):
        prefix = f'sign-{signer + 1}-'
        for firmware, d in c.drives.items():
            routes.reel(base64.b64encode(packets[firmware].serialize()).decode())
            d.key('Enter', 'ScanScreen', 'Scan PSBT')
            d.wait('ButtonListScreen', 'Select signer', timeout=240)
        c.shot(prefix + 'select', 'Select a loaded seed to sign the same fabricated multisig transaction.')
        if signer:
            for d in c.drives.values():
                d.move('ArrowDown', 'Select second seed')
        for suffix, screen, description in [
            ('overview', 'PSBTOverviewScreen', 'Review the multisig transaction overview.'),
            ('full-spend', 'WarningScreen', 'Review the warning that this transaction has no change output.'),
            ('math', 'PSBTMathScreen', 'Review 100000 sats in, 90000 sats to the recipient, and a 10000 sat fee.'),
            ('recipient', 'PSBTAddressDetailsScreen', 'Review the fabricated recipient address and amount.'),
            ('approve', 'PSBTFinalizeScreen', 'Approve signing the multisig transaction.'),
            ('qr', 'QRDisplayScreen', 'Read the signed PSBT from the device animated QR.')]:
            c.keys('Enter', screen, prefix + suffix, description)
        for firmware, d in c.drives.items():
            signed, frames = routes.collect_animated_qr(d.page)
            assert signed is not None
            tx, signatures = reference.read_psbt(signed)
            assert tx == original.tx.serialize()
            assert len(signatures) == signer + 1
            for i in range(signer + 1):
                public = roots[i].derive(ACCOUNT + '/0/0').public
                signature = signatures[public]
                r, s = reference.decode_der(signature[:-1])
                assert signature[-1] == 1 and s <= reference.N // 2
                assert reference.verify_signature(public, sighash, r, s)
                assert not reference.verify_signature(public, sighash, r, s ^ 1)
            response = psbt.PSBT.parse(signed)
            packets[firmware].inputs[0].partial_sigs.update(response.inputs[0].partial_sigs)
            evidence[f'{firmware}-{signer + 1}'] = dict(
                psbt=base64.b64encode(signed).decode(), qr_frames=sorted(frames),
                signatures_verified=len(signatures), transaction_unchanged=True)
            d.checks.append(dict(signer=signer + 1, signatures_verified=len(signatures)))
            d.save()
            d.key('Enter', 'MainMenuScreen', 'Leave signed PSBT QR')
    (OUT / 'signatures.json').write_text(json.dumps(evidence, indent=2) + '\n')


def measure(page):
    rows = json.loads((OUT / 'captures.json').read_text())
    observations = {}
    for name in OBSERVATION_FILES:
        observations.update(json.loads((OUT / name).read_text()))
    signatures = json.loads((OUT / 'signatures.json').read_text())
    for signer in (1, 2):
        assert signatures[f'stock-{signer}']['psbt'] == signatures[f'smartcard-{signer}']['psbt']
    for row in rows:
        for firmware in ('stock', 'smartcard'):
            log = (OUT / f'{firmware}-debug.log').read_text()
            assert 'display() enter: ' + row[firmware + '_screen'] in log
            row[firmware + '_console_message_index'] = row.pop(firmware + '_log_line') - 1
        images = [base64.b64encode((ROOT / row[f + '_png']).read_bytes()).decode()
                  for f in ('stock', 'smartcard')]
        result = page.evaluate('''async images => {
          const decoded = await Promise.all(images.map(async data => {
            const img = new Image(); img.src = 'data:image/png;base64,' + data; await img.decode();
            const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
            const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
            return {width: img.width, height: img.height, data: ctx.getImageData(0, 0, img.width, img.height).data};
          }));
          const [a, b] = decoded;
          if (a.width !== 320 || a.height !== 240 || b.width !== 320 || b.height !== 240) throw Error('Not native device pixels');
          let changed = 0, delta = 0;
          for (let i = 0; i < a.data.length; i += 4) {
            if ([0, 1, 2, 3].some(c => a.data[i+c] !== b.data[i+c])) changed++;
            for (let c = 0; c < 3; c++) delta += Math.abs(a.data[i+c] - b.data[i+c]);
          }
          return {identical: changed === 0, changed_pixels: changed,
            difference_percent: changed * 100 / (320 * 240), mean_rgb_delta: delta / (320 * 240 * 3)};
        }''', images)
        row.update(result)
        row['difference_percent'] = round(row['difference_percent'], 3)
        row['mean_rgb_delta'] = round(row['mean_rgb_delta'], 3)
        if not row['identical']:
            row['difference'] = observations[row['step']]
            assert '\u2014' not in row['difference']
        if row['step'].endswith('-qr') and row['step'].startswith('sign-'):
            row['capture_note'] = 'Unsynchronized animation frames; identical exported PSBT payloads, not evidence of a firmware layout difference.'
        if row['step'].endswith('-scan'):
            row['capture_note'] = 'Blank camera lead-in before the QR appears; decoder success is recorded in the debug log and the following screen.'
        if row['step'] == 'sign-2-select':
            row['capture_note'] = 'Menu captured before moving selection to the second seed; signatures.json verifies the second signer.'
    (OUT / 'manifest.json').write_text(json.dumps(rows, indent=2) + '\n')
    print('\nstep | identical | stock screen | smartcard screen')
    print('--- | --- | --- | ---')
    for row in rows:
        print(f"{row['step']} | {'yes' if row['identical'] else 'no'} | {row['stock_screen']} | {row['smartcard_screen']}")
    print(f"\n{len(rows)} paired steps, {sum(r['identical'] for r in rows)} pixel-identical.")
    print('Both firmwares: three seeds loaded, three account keys exported, 2-of-3 descriptor scanned, two signatures verified from device QR output.')
    print('Smartcard: all three seeds saved to separate SeedKeepers, discarded from device memory, and restored from cards.')
    print('No ceremony steps dropped. No funding, chain queries, faucet calls, or broadcasting. Zero-change spend does not exercise descriptor-based change verification.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--measure-only', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        if args.measure_only:
            browser = p.chromium.launch()
            measure(browser.new_page())
            browser.close()
            return
        for name in ('wallet-stock.zip', 'wallet-smartcard.zip', 'pyodide-e24b45d3/pyodide.js'):
            assert harness.find_asset(name), f'Missing prebuilt asset: {name}'
        assert urlparse(harness.BASE_URL).hostname in ('127.0.0.1', 'localhost')
        with tempfile.TemporaryDirectory(dir=OUT, prefix='scratch-') as scratch:
            routes.OUT = Path(scratch)
            tempfile.tempdir = scratch
            unpacked = make_qr_y4m.vendored_libraries(harness.find_asset('wallet-smartcard.zip'))
            sys.path.append(harness.find_asset('wallet-smartcard.zip'))
            routes.reel(make_qr_y4m.SEEDQR)
            server = routes.start_server()
            browser = p.chromium.launch(args=['--use-fake-ui-for-media-stream',
                '--use-fake-device-for-media-stream',
                f'--use-file-for-fake-video-capture={routes.OUT / "flow-camera.y4m"}'])
            drives = {}
            blocked = []
            def offline(route):
                if urlparse(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
                    blocked.append(route.request.url)
                    route.abort()
                elif urlparse(route.request.url).path.startswith('/api/'):
                    blocked.append(route.request.url)
                    route.abort()
                else:
                    route.continue_()
            try:
                for firmware in ('stock', 'smartcard'):
                    context = browser.new_context(permissions=['camera'], viewport={'width': 900, 'height': 1100})
                    context.route('**/*', offline)
                    drives[firmware] = Drive(context.new_page(), firmware, 'multisig')
                run_ceremony(Ceremony(drives))
                assert not blocked, blocked
                measure(browser.new_page())
            finally:
                for d in drives.values():
                    d.save()
                browser.close()
                server.terminate()
                server.wait(timeout=10)
                shutil.rmtree(unpacked)


if __name__ == '__main__':
    main()
