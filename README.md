# SeedSigner simulator

[![try it live](https://img.shields.io/badge/try%20it-live-f7931a?style=flat-square)](https://bitsaga.be/seedsigner-simulator/)
[![smartcard fork](https://img.shields.io/badge/smartcard%20fork-SeSi--0.8.7%2BShSi--B11-blue?style=flat-square)](UPSTREAM)
[![stock](https://img.shields.io/badge/stock-0.8.7-blue?style=flat-square)](UPSTREAM)
[![reproducible-build](https://img.shields.io/github/actions/workflow/status/bitsagarob/seedsigner-simulator/reproducible-build.yml?branch=main&label=reproducible%20build&style=flat-square)](https://github.com/bitsagarob/seedsigner-simulator/actions/workflows/reproducible-build.yml)
[![tests](https://img.shields.io/github/actions/workflow/status/bitsagarob/seedsigner-simulator/test.yml?branch=main&label=tests&style=flat-square)](https://github.com/bitsagarob/seedsigner-simulator/actions/workflows/test.yml)
[![upstream tests](https://img.shields.io/github/actions/workflow/status/bitsagarob/seedsigner-simulator/upstream-tests.yml?branch=main&label=upstream%20tests&style=flat-square)](https://github.com/bitsagarob/seedsigner-simulator/actions/workflows/upstream-tests.yml)
[![release](https://img.shields.io/github/v/release/bitsagarob/seedsigner-simulator?style=flat-square)](https://github.com/bitsagarob/seedsigner-simulator/releases/latest)
[![licence](https://img.shields.io/github/license/bitsagarob/seedsigner-simulator?style=flat-square)](LICENSE)

Real [SeedSigner](https://seedsigner.com) firmware, the actual Python off the
device, running in a browser tab. The screen is a canvas, the buttons are your
keyboard, the camera is your webcam.

> ### Insecure by design
>
> **Never type in a seed phrase you rely on.** Use a published test seed.
>
> Nothing makes this safe: not running it offline, not a private window, not a
> clean laptop. See [below](#why-it-cannot-be-made-safe).

![The simulator running the wallet's home screen](docs/img/device.png)

## Why it cannot be made safe

A SeedSigner is a signing device because of what surrounds the software. None of
that is here. Only the software is.

| A real SeedSigner | This page |
| --- | --- |
| No wifi, no bluetooth, no port | A computer that has all three |
| Runs one program and nothing else | Runs beside extensions, other tabs, devtools, every process on the machine |
| Loses everything at power-off | A JavaScript heap, copied by the garbage collector into swap and hibernation files |
| A real smartcard holds the key | A JavaScript object pretending to be a smartcard |
| A screen only you see | A canvas any script or screen recorder can read |

**Offline does not help.** It changes one row and leaves the rest. The seed is
still typed into a general-purpose computer with an OS, a clipboard, a swap file
and a network connection it will use again later. Anything there that can read
memory or the screen can read the seed. Having no network is a property of the
page, not of the computer, and a tab is a sandbox against other websites, not
against the machine it runs on.

**Mainnet works, which is the dangerous part.** The page boots on Testnet, but
Mainnet is still in Settings, deriving real keys and signing real transactions
(`test/test_mainnet.py` checks them against BIP32, BIP143 and ECDSA). Treat
everything it shows you as public: seeds, passphrases, xpubs, descriptors,
signatures.

**If you already entered a real seed here**, move the funds to a seed generated
on a device you trust. [SECURITY.md](SECURITY.md#if-you-entered-a-real-seed-phrase-here).

Good for learning the menus, rehearsing a flow, teaching multisig, testing a
fork's screens. Anything involving money belongs on hardware.

## Three firmwares

| On the page | What it is | URL |
| --- | --- | --- |
| **SeedSigner** | Stock, what a plain SeedSigner runs. The default | `?firmware=stock` |
| **ShieldSigner** | [3rdIteration/seedsigner](https://github.com/3rdIteration/seedsigner), adds SeedKeeper and Satochip | `?firmware=smartcard` |
| **Doomsigner** | Our fork of that fork: BIP-352 silent payments, and DOOM in front | `?firmware=doomsigner` |

The **Firmware** control under the device switches. DOOM is off on the first two
unless `?doom` is on the URL; on Doomsigner it always boots into the game, and
five taps on the top side button reach the wallet.

## Try it

```sh
git clone https://github.com/bitsagarob/seedsigner-simulator.git
cd seedsigner-simulator
./build/fetch-assets.sh                 # Pyodide, pinned and hash-checked (~26 MB, once)
./build/build-wallet-zip.sh smartcard   # wallet-smartcard.zip, from the pinned commit
./build/build-wallet-zip.sh stock       # wallet-stock.zip, from the pinned commit
python3 test/serve.py --port 8770 src/web src/shims build/out
```

Then open <http://127.0.0.1:8770/>. Use `test/serve.py`, not
`python3 -m http.server`: without the two isolation headers it sends, the wallet
never starts. Nothing fetched is committed, so what you run is provably the
pinned commit, and both steps verify what they download.

Arrow keys move, Enter selects, `1` `2` `3` are the side buttons, and the drawn
buttons work too. The screen is not one of them: a SeedSigner has no touchscreen.

To run your own fork, `SS_REPO` and `SS_COMMIT` override the pin for one build
([CONTRIBUTING.md](CONTRIBUTING.md#running-your-own-fork-of-seedsigner-in-it)).

## What you can verify

- **It is the firmware, not a re-creation.** `wallet-<firmware>.zip` holds that
  firmware's upstream Python tree and its own `Controller.start()` runs it.
  Menus, seed handling, PSBT parsing, QR encoders: all theirs, unmodified.
- **Nothing patches the wallet.** Hardware is replaced from outside, by
  [`src/shims/`](src/shims). Even Testnet-at-boot is a value in the
  `settings.json` the device reads, not an edit.
- **Pinned to release tags, not branch tips** ([`UPSTREAM`](UPSTREAM), one
  section per firmware). The fork's tag is the one the official pi0-smartcard
  device image is built from, so this and that device run the same code.
- **Rebuild and compare.** `build/build-wallet-zip.sh` reproduces a zip byte for
  byte. CI re-derives the hashes on every push on a clean runner and GitHub signs
  the result:
  `gh attestation verify wallet-smartcard.zip --repo bitsagarob/seedsigner-simulator`.
- **Upstream's own tests run against our pins**
  ([`upstream-tests.yml`](.github/workflows/upstream-tests.yml)): 949 from the
  fork's suite, all of stock's. The 50 skipped want a physical card reader.
- **The webcam really is the camera.** Same `DecodeQR`, same SeedQR /
  CompactSeedQR / PSBT / UR parsing; only the decoder is the browser's.
- **One host.** No backend, and the CSP names one other origin,
  `signet.bitsaga.be`, for the faucet and read-only lookups.

## The multisig tutorial

A guided 2 of 3 done in the page, behind a URL since the resting page is a plain
SeedSigner: `?tutorial=offer` shows the button, `?tutorial=1` starts it.
ShieldSigner only, the flow being about SeedKeeper cards.

Three published test seeds go onto three cards with the real PIN ceremony, a 2 of
3 is built from the keys read back, the faucet on **Bitsaga Signet** pays it, and
two cards sign a spend that confirms. **Those coins are not real bitcoin.** Press
play and it narrates itself, or take over and press the buttons.

The coordinator is drawn as a phone beside the device, because that is what it
is: [`signet-coordinator.js`](src/web/signet-coordinator.js) builds the
descriptor, addresses, PSBT and final transaction in the browser, and
`test/test_tutorial.py` checks every value against the wallet's own embit. The QR
transfers are real modules read off real pixels by the wallet's own decoder, so
your webcam is never involved.

Holding three keys on one device is fine for a demo and wrong for real funds.

## What works, and what does not

Cards are the fork's, so under stock there is no card tray at all.

**Works, any firmware.** The full menu tree, seed loading by QR or by hand,
passphrases, xpub export, PSBT signing, SeedQR backup, settings, every QR screen.

**Works, ShieldSigner and Doomsigner.** Three card slots, each a **SeedKeeper** or
a **Satochip**, both PIN-checked. Seed onto a SeedKeeper and back off. A 2 of 3
descriptor onto a SeedKeeper. Satochip holds a real BIP32 master key and
authentikey, and pysatochip verifies every answer it signs.

**Does not, any firmware.** No microSD, so settings reset on reload and firmware
update is gone. Nothing on a background thread: no spinner, no scrolling text, no
pulsing border, and thread-based work such as brute-force address verification
never completes (camera preview and animated QR are pumped by hand). No timing,
so no wipe timer, screensaver or battery reading.

**Does not, the fork.** Two upstream bugs, neither worked around here:
*Initialise with Seed* on Satochip raises on success
(`ToolsSatochipImportSeedView` unpacks three values from a one-value return), and
reading a multisig descriptor back off a card dies on
`name 'SEEDKEEPER_DIC_TYPE' is not defined`, because `smartcard_views.py` imports
that name beside three modules no published pysatochip has and swallows the whole
`ImportError`. Saving works: `test_cards.py` reads the 448 characters back at
APDU level. Also absent: card-to-card secret copy (refused rather than done in
the clear), card signing, PIN change, 2FA, factory reset. Cards forget on reload,
deliberately.

## How it works

The wallet's Python runs under [Pyodide](https://pyodide.org) (CPython on
WebAssembly) in a Web Worker. Four hardware seams are replaced, three under stock.

| Seam | Replaced by | Why |
| --- | --- | --- |
| Display | [`browser_display.py`](src/shims/browser_display.py) | Swaps the panel driver under SeedSigner's unmodified `Renderer`; RGB frames go to a canvas. |
| Buttons | [`wallet-worker.js`](src/web/wallet-worker.js) | The worker is blocked in the wallet's main loop and can never answer a `postMessage`, so keys cross on a `SharedArrayBuffer`. |
| Camera + QR | [`browser_camera.py`](src/shims/browser_camera.py) + [`wallet-camera.js`](src/web/wallet-camera.js) | pyzbar has no WebAssembly build, so the browser decodes and hands bytes to the unmodified decoder. |
| Smartcard | [`src/smartcard/`](src/smartcard) | Browsers have no smartcard API, so simulated cards answer real APDUs and pysatochip runs unchanged. |

A fifth, [`browser_qr.py`](src/shims/browser_qr.py), draws the QR screens, whose
drawing lives in a thread this environment cannot run.

That one constraint, a permanently blocked worker, explains most of the
architecture. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the long version,
including why `BarcodeDetector` is never trusted with a payload.

## Self-hosting

Static files. Two things trip up every first attempt
([docs/SELF-HOSTING.md](docs/SELF-HOSTING.md)):

1. `Cross-Origin-Opener-Policy: same-origin` and
   `Cross-Origin-Embedder-Policy: require-corp` are mandatory, or
   `SharedArrayBuffer` does not exist and the wallet never starts.
2. The camera needs `https` or `localhost`. Plain `http` on a LAN has no camera
   API to ask.

## Development

`python3 test/run.py` builds what is missing and runs everything in a real
browser. [CONTRIBUTING.md](CONTRIBUTING.md) covers the rest,
[test/README.md](test/README.md) says what each test proves, and `?debug=1` traces
every screen, thread and keypress. Security reports: [SECURITY.md](SECURITY.md).

## Licence and credits

MIT, see [LICENSE](LICENSE). Almost none of this code was written here: the
wallet is upstream [SeedSigner](https://github.com/SeedSigner/seedsigner) (MIT,
Copyright (c) 2021 SeedSigner), the smartcard build is pinned to
[3rdIteration/seedsigner](https://github.com/3rdIteration/seedsigner), and the
browser side rests on [Pyodide](https://pyodide.org) and
[jsQR](https://github.com/cozmo/jsQR). [THIRD-PARTY.md](THIRD-PARTY.md) lists
every dependency and how to check it.

Independent project, not affiliated with or endorsed by SeedSigner. Running it
proves nothing about a real device.
