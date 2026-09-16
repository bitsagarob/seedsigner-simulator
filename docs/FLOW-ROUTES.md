# Flow routes, verified by driving

Every key sequence in this document was driven in Chromium against this
simulator on port 8786, and every landing screen below is the screen class the
wallet itself announced in its debug log (`?debug=1`, the line
`display() enter: <ScreenName>`), captured at the moment the key was accepted.
Nothing here is reasoned out from source; where a flow does not exist the
absence was established by driving the menus and photographing them, not by
reading code.

The driving script is `research/flow_routes.py` (not part of the suite; the
SUITE list in `test/run.py` is untouched). It presses the keys, waits for the
announced screen, refuses to record a step whose landing screen differs from
the one below, saves a screenshot of the 320x240 device canvas per press, and
writes its own evidence next to the screenshots:

- `test/artifacts/flow-<firmware>-<flow>-NNN-<ScreenName>.png` — one image per
  press, in order
- `test/artifacts/flow-<firmware>-<flow>.json` — the run record: every step,
  the exact log lines between presses, and the verification results
- `test/artifacts/flow-<firmware>-<flow>.log` — the full wallet debug log

Reproduce any single run with (see `AGENTS.md` for the interpreter, browser
path and port this lane uses):

    SIM_PORT=8786 <venv python> research/flow_routes.py --firmware stock --flow single

(`stock` / `smartcard` / `doomsigner`; flows `image`, `passphrase`, `single`,
`multi`, `sign`, `cards`, `menus`.) Every exported key in this document was
read back off the device's QR screen with jsQR and compared against an
independent BIP39/BIP32 implementation (`test/mainnet_reference.py`); the PSBT
signature was verified offline against a BIP143 sighash. The seeds, passphrase
and transaction are published test vectors and a fabricated spend that never
existed; nothing holds value.

## Reading the sequences

| key | what it is |
| --- | --- |
| Enter | the device's select key (`KEY_PRESS`), centre of the arrow pad |
| ↓ ↑ → ← | the arrow pad |
| 3 | KEY3 (saves/accepts on keyboards) |

The main menu is a 2x2 grid — Scan top-left (selected on boot), Seeds top-right,
Tools bottom-left, Settings bottom-right. "from MainMenu" below always starts
there. Where a step is "(no key)", the wallet moved on its own and the script
waited for the announced screen instead of pressing anything.

Three firmwares were driven: `stock` (SeedSigner 0.8.7), `smartcard`
(3rdIteration fork, "ShieldSigner" on the page) and `doomsigner` (our fork).

## Firmware differences — the short version

1. **DoomSigner boots into a warning, not the menu.** Every DoomSigner run
   opens on `WarningScreen` ("Unhardened Build — Not secure!") and needs one
   Enter ("I Understand") before `MainMenuScreen`. Stock and smartcard boot
   straight to `MainMenuScreen`.
2. **DoomSigner has no `SeedFinalizeScreen` after creating a seed.** On the
   photo-entropy flow, stock and smartcard end the words backup prompt on
   `SeedFinalizeScreen` (one more Enter to `SeedOptionsScreen`); DoomSigner
   lands on `SeedOptionsScreen` directly.
3. **DoomSigner inserts an extra warning when signing.** A full-spend PSBT
   raises two `WarningScreen`s in a row (fee-unusually-large, then no change
   output); stock and smartcard raise only the no-change one. One extra Enter.
4. **The passphrase review screen defaults differ.** Stock's
   `SeedReviewPassphraseScreen` opens focused on "Edit passphrase" and needs ↓
   before Enter to reach "Done". Both forks open focused on "Done" — an
   extra Enter on stock is the single most mis-pressed key in this document.
5. **DoomSigner re-asks one menu question more.** Its finalized
   `SeedOptionsScreen` carries an extra "Silent payments" entry, so "Discard
   seed" is 6 ↓ instead of 5. Everything else in its menus sits at the stock
   positions unless noted.
6. **Smartcard menus are not stock-only.** The fork has them, and so does
   DoomSigner: the full SeedKeeper save/read round trip was driven on both.
   Stock has no card anything: its Tools list (photographed:
   `flow-stock-menus-052..061`) is New seed / New seed / Calc 12th-24th word /
   Address explorer / Verify address, with no Smartcard entry, and its
   `Load a Seed` and finalized-seed menus (photographed) carry no card items.
7. **The card PIN is remembered across one flow on DoomSigner.** Reading a
   secret back off a just-entered card re-uses the cached PIN (straight to the
   secret list); smartcard asks for the PIN again. Same card, different
   keyboard count.

---

## 1. New seed from image entropy, ending on `SeedOptionsScreen`

Route: MainMenu → Tools → New seed (camera). Verified on all three firmwares.
The fake camera was fed a noisy still; a flat grey feed fails the fork's own
entropy-quality check (a warning the wallet is entitled to raise — not a bug,
and not worked around).

Common to all three, from `MainMenuScreen`:

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | ↓ | `MainMenuScreen` | Tools |
| 2 | Enter | `ButtonListScreen` | Tools menu, New seed (camera) selected |
| 3 | Enter | `ToolsImageEntropyLivePreviewScreen` | camera opens, live preview |
|   | (wait ~2s) | | let frames accumulate so the entropy check passes |
| 4 | Enter | `ToolsImageEntropyFinalImageScreen` | takes the still, shows it back |
| 5 | → | `ButtonListScreen` | accept image; mnemonic-length list, 12 words |
| 6 | Enter | `DireWarningScreen` | "keep these words private" |
| 7 | Enter | `SeedWordsScreen` | words 1–4 |
| 8 | Enter | `SeedWordsScreen` | words 5–8 |
| 9 | Enter | `SeedWordsScreen` | words 9–12 |
| 10 | Enter | `SeedWordsBackupTestPromptScreen` | Verify / Review / Skip prompt |
| 11 | ↓ | same | move toward Skip |
| 12 | ↓ | same | Skip focused |

Then the firmwares part ways — **this is difference #2**:

| firmware | #12 key | #13 lands on | #14 |
|----------|---------|--------------|-----|
| stock | Enter | `SeedFinalizeScreen` | Enter → `SeedOptionsScreen` |
| smartcard | Enter | `SeedFinalizeScreen` | Enter → `SeedOptionsScreen` |
| doomsigner | Enter | **`SeedOptionsScreen` directly** | done |

(`SeedWordsBackupTestPromptScreen` has two buttons on stock — Verify/Skip — and
three on the forks — Verify/Review/Skip. ↓ ↓ lands on Skip in both cases;
stock clamps at the bottom, the forks pass Review on the way.)

Evidence: `flow-stock-image`, `flow-smartcard-image`, `flow-doomsigner-image`.

Stock's separate Tools → New seed (dice) route was not driven; the camera route
above is the one asked about. On stock the camera route needed the entropy
feed; on the forks the same press order works.

## 2. Load a seed by scanning a SeedQR

Identical on all three firmwares, from `MainMenuScreen` (Scan is already
selected):

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | Enter | `ScanScreen` | camera path opens |
|   | (no key) | `SeedFinalizeScreen` | decoder reads the SeedQR (the numeric SeedQR of the published test vector "army van defense …"; fingerprint `b2269592` shown on screen) |
| 2 | Enter | `SeedOptionsScreen` | Done finalizes the seed |

Evidence: every flow run starts with this scan (`flow-*-single`,
`flow-*-passphrase`, `flow-*-multi`, `flow-*-sign`, `flow-*-cards`); the
suite's own `scan_seedqr` runs anchor the same screen per firmware.

## 3. Add a BIP39 passphrase and read the new fingerprint

The passphrase can only be attached while the seed is still **pending**, i.e.
immediately after the scanner finalizes it on `SeedFinalizeScreen`. On all
three firmwares, a seed that has already been finalized (Done pressed) has **no
passphrase entry anywhere**: the finalized `SeedOptionsScreen` was walked with
10 ↓ presses on each firmware and photographed (`flow-*-menus-027..036`) —
stock: Scan transaction / Export xpub / Address explorer / Backup seed /
Discard seed; the forks add BIP-85 (and Silent payments on DoomSigner) and no
passphrase item. **The verified route below therefore rescans the same SeedQR
and adds the passphrase before pressing Done.**

From `MainMenuScreen` (works identically on all three):

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | Enter | `ScanScreen` | camera opens on the SeedQR |
|   | (no key) | `SeedFinalizeScreen` | seed decoded, not yet finalized |
| 2 | ↓ | `SeedFinalizeScreen` | Add/type passphrase (BIP39) |
| 3 | Enter | `SeedAddPassphraseScreen` | the firmware's on-screen keyboard |
| 4 | Enter, →, Enter, →, Enter | `SeedAddPassphraseScreen` | types `a`, `b`, `c` (Enter picks the highlighted letter, → moves) |
| 5 | 3 | `SeedReviewPassphraseScreen` | KEY3 saves; review shows `abc` and `b2269592 -> 93c58305` |
| 6 | ↓ **(stock only)**, Enter | `SeedOptionsScreen` | Done. **Stock's review opens on "Edit passphrase"; the forks open on "Done" (difference #4).** |

The new fingerprint is on the review screen itself: `93c58305`. Each run then
exported the account key (flow 4) and the QR content matched the independently
derived root for mnemonic+"abc" — fingerprint and key, all three firmwares.

Evidence: `flow-stock-passphrase`, `flow-smartcard-passphrase`,
`flow-doomsigner-passphrase` (steps 6–17 and the `checks` block in the JSON,
which records the exported origin `[93c58305/84'/1'/0']`).

## 4. Export a single-sig account key, native segwit `m/84'/1'/0'`

Identical on all three firmwares, from `MainMenuScreen` with the seed already
finalized (any of: flow 1, flow 2 + Done, or restored from card):

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | → | `MainMenuScreen` | Seeds |
| 2 | Enter | `ButtonListScreen` | loaded-seed list |
| 3 | Enter | `SeedOptionsScreen` | the only loaded seed |
| 4 | ↓ | `SeedOptionsScreen` | Export xpub |
| 5 | Enter | `ButtonListScreen` | sig type: Single Sig (default) |
| 6 | Enter | `ButtonListScreen` | script: Native Segwit (default) |
| 7 | Enter | `ButtonListScreen` | QR format: animated (default) |
| 8 | ↓ | same | Static QR |
| 9 | Enter | `WarningScreen` | xpub privacy warning |
| 10 | Enter | `SeedExportXpubDetailsScreen` | the account key, origin `[b2269592/84'/1'/0']` |
| 11 | Enter | `QRDisplayScreen` | the xpub as a static QR |

Verified: the QR read back matched the independent derivation of
`m/84'/1'/0'` (zpub, origin `b2269592`) on all three firmwares; with the
passphrase the same route matched the `93c58305` root. Evidence:
`flow-*-single`, `flow-*-passphrase` (final steps).

Note for demo authors: stock's single-sig export ends on a "Verify Address"
offering if you continue past the QR; the runs above stop at the QR screen.

## 5. Export a multisig account key, `m/48'/1'/0'/2'`

Identical on all three firmwares; same route as flow 4 with one extra ↓:

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1–4 | → , Enter, Enter, ↓ | `SeedOptionsScreen` | to Export xpub (as flow 4) |
| 5 | Enter | `ButtonListScreen` | sig type: Single Sig (default) |
| 6 | ↓ | same | **Multisig** |
| 7 | Enter | `ButtonListScreen` | script: Native Segwit (default) |
| 8 | Enter | `ButtonListScreen` | QR format: animated (default) |
| 9 | ↓ | same | Static QR |
| 10 | Enter | `WarningScreen` | xpub privacy warning |
| 11 | Enter | `SeedExportXpubDetailsScreen` | the account key, origin `[b2269592/48'/1'/0'/2']` |
| 12 | Enter | `QRDisplayScreen` | the xpub as a static QR |

Verified against the independent derivation of `m/48'/1'/0'/2'` on all three
firmwares. Evidence: `flow-*-multi`.

## 6. Scan an unsigned PSBT and sign it

From `MainMenuScreen` with a loaded seed whose fingerprint matches the PSBT's
input. The transaction is the fabricated single-input single-output PSBT from
the script (100000 → 90000 sats, full spend, no change). Stock and smartcard:

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | Enter | `ScanScreen` | camera on the PSBT QR |
|   | (no key) | `ButtonListScreen` | "which seed should sign" — the matching seed, no `?` |
| 2 | Enter | `PSBTOverviewScreen` | transaction overview |
| 3 | Enter | `WarningScreen` | no change output — full spend |
| 4 | Enter | `PSBTMathScreen` | 100000 in, 90000 out, 10000 fee |
| 5 | Enter | `PSBTAddressDetailsScreen` | recipient address |
| 6 | Enter | `PSBTFinalizeScreen` | Approve transaction |
| 7 | Enter | `QRDisplayScreen` | **the signed PSBT as an animated QR** |

**DoomSigner differs (difference #3):** between steps 3 and 4 there is one
more `WarningScreen` — "Caution — Review Carefully! The fee is an unusually
large share of this transaction." → one extra Enter. The full DoomSigner order
is: overview → Enter → *fee warning* → Enter → *no-change warning* → Enter →
math → recipient → approve → QR.

Verification: the animated QR was collected off the screen with the wallet's
own UR decoder and the signature inside it was checked offline — it verifies
under `m/84'/1'/0'/0/0` against the BIP143 sighash of exactly the transaction
that went in, is low-S, SIGHASH_ALL, and refuses with one bit flipped. All
three firmwares. Evidence: `flow-*-sign` (the `checks` block carries the
signature hex and verification summary).

## 7. Smartcard: put a seed on a SeedKeeper card, and read it back

Established first, because the prompt assumed otherwise: **this flow is not
smartcard-only.** It was driven end to end on both the smartcard fork and
DoomSigner. Stock cannot do it at all (difference #6: no card menus, no tray;
established by driving and photographing stock's Tools and seed menus, plus
the suite's own firmware test, which asserts the tray is absent under stock).

Setup, once per run, before the device route: click Card A in the on-page card
tray (a page interaction, not a device key; the run then clicks the screen once
to take focus back so the next keypress reaches the device). The tray's
default card is a blank SeedKeeper.

With a scanned-and-finalized seed on `SeedOptionsScreen`:

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | ↓ ↓ ↓ | `SeedOptionsScreen` | Backup seed |
| 2 | Enter | `ButtonListScreen` | backup menu |
| 3 | ↓ | same | To SeedKeeper |
| 4 | Enter | `SeedAddPassphraseScreen` | PIN keyboard (blank card) |
| 5 | Enter ×4, 3 | `WarningScreen` | short PIN tried; card reports "not initialized" |
| 6 | Enter | `SeedAddPassphraseScreen` | choose new PIN |
| 7 | Enter ×4, 3 | `SeedAddPassphraseScreen` | new PIN `aaaa` |
| 8 | Enter ×4, 3 | `LargeIconStatusScreen` | confirm; card set up |
| 9 | Enter | `SeedAddPassphraseScreen` | secret label, prefilled `b2269592` |
| 10 | 3 | `LargeIconStatusScreen` | save; card logs "stored secret … type 0x10 subtype 0x01, label 'b2269592', 84 bytes" |
| 11 | Enter | `SeedOptionsScreen` | back to the seed |

Reading it back (seed discarded first so the card is the only source): from
`SeedOptionsScreen`, Discard seed — ↓ ×5 (smartcard) / **↓ ×6 (DoomSigner,
difference #5)** — Enter → `WarningScreen` (discard confirmation) → ↓ → Enter
→ `MainMenuScreen`. Then:

| # | key | lands on | what it is |
|---|-----|----------|------------|
| 1 | → | `MainMenuScreen` | Seeds |
| 2 | Enter | `ButtonListScreen` | Load a Seed (no seeds loaded) |
| 3 | ↓ ↓ ↓ | same | From SeedKeeper |
| 4 | Enter | **firmware differs** | see below |
|   | smartcard: Enter ×4, 3 | `ButtonListScreen` | PIN asked again (cached PIN off) |
|   | doomsigner: (nothing) | `ButtonListScreen` | same-card PIN reused, straight to the list |
| 5 | Enter | `SeedFinalizeScreen` | the card's secret exported "in the clear, label 'b2269592'" |
| 6 | Enter | `SeedOptionsScreen` | seed restored |

Verification: the restored seed's `SeedOptionsScreen` exported an account key
that matched the independent derivation of the same seed (smartcard and
doomsigner runs; the card log lines are quoted in the run JSONs). Evidence:
`flow-smartcard-cards`, `flow-doomsigner-cards`.

The Satochip equivalents (Initialise with Seed, Load as Descriptor) were **not**
driven for this document; the suite's `cards_seed` covers that card. Only the
SeedKeeper round trip asked for here was driven.

---

## What was driven, and what was not

Driven in a browser, log-asserted per press, screenshotted per press, on the
firmwares listed:

| flow | stock | smartcard | doomsigner |
| --- | --- | --- | --- |
| 1 image entropy → `SeedOptionsScreen` | ✔ | ✔ | ✔ |
| 2 scan SeedQR → `SeedFinalizeScreen` | ✔ | ✔ | ✔ |
| 3 passphrase + new fingerprint | ✔ | ✔ | ✔ |
| 4 export `m/84'/1'/0'` | ✔ | ✔ | ✔ |
| 5 export `m/48'/1'/0'/2'` | ✔ | ✔ | ✔ |
| 6 scan + sign PSBT → signed QR | ✔ | ✔ | ✔ |
| 7 SeedKeeper save + read back | — (no menus) | ✔ | ✔ |
| menu walks (absence evidence) | ✔ | ✔ | ✔ |

Not driven, and why:

- **Stock, flow 7**: no card code exists in that firmware. Established by
  driving its Tools and seed menus (10 ↓ on each, photographed
  `flow-stock-menus-052..061`, `flow-stock-menus-005`), which contain no
  Smartcard/SeedKeeper entries, and by `test_firmware.py`, which asserts the
  tray is absent under stock.
- **Direct passphrase entry on an already-finalized seed**: no such route
  exists on any of the three firmwares. Established by walking each finalized
  `SeedOptionsScreen` with 10 ↓ presses and photographing every entry (stock
  `flow-stock-menus-031`, smartcard `flow-smartcard-menus-031`,
  doomsigner `flow-doomsigner-menus-034`): Scan transaction / Export xpub /
  Address explorer / Backup seed / Discard seed, plus fork-only BIP-85 and
  Silent payments. The verified alternative is in flow 3: rescan the SeedQR and
  add the passphrase before pressing Done.
- Everything else above was driven to the screen named, on every firmware it
  appears on.
