# MuSig2 harness and browser demo assessment

## Result and limits

The local harness can now boot this checkout without contacting the public
internet, but **a complete offline MuSig2 signing demonstration is not implemented**.
Changing the browser origin does not replace the chain API. The observed offline
run stopped at the wallet panel's initial address scan, before MuSig2 entry.
There was no successful signing round, broadcast, second spend, or nonce-pool
proof in this run.

The unblocked harness requests faucet funding and broadcasts transactions. Source
configuration targets Bitsaga Signet, not mainnet: the default URL selects no
mainnet override, the coordinator uses test-network prefixes, and its API base is
`https://signet.bitsaga.be/api` (`src/web/signet-coordinator.js:31-44`,
`src/web/extras/coordinator.py:19`). Testnet prefixes alone cannot distinguish
signet from testnet or authenticate the server's chain. No live chain-identity
check was performed. Because the requested constraint prohibits touching a real
chain, only a run with external requests blocked was performed. No faucet or
broadcast request was sent to the service.

## What the harness actually requires

| Dependency | Actual role | Required or hardcoded? |
| --- | --- | --- |
| Page origin `https://bitsaga.be` | Browser-visible origin, not the source of the page's bytes | Hardcoded default, now configurable; documented as the API's allowed origin, not necessary for wallet computation |
| Loopback static server, originally port 8792 | Supplies this checkout's page, JS, Python shims and built assets | Server required; port arbitrary; harness does not start it |
| `test/signet_bridge.py` | Playwright intercepts the chosen page origin and fulfills requests from the loopback server, adding isolation headers | In this repository, not the external dependency |
| External `simdrive.py` | `Sim` driver: Chromium lifecycle, console oracle, button presses and canvas-based fake camera | Driver currently required; directory is configurable; not shipped by this repository |
| Signet HTTP API | Status, address/UTXO scans, faucet and broadcast | Required by the existing flow, not supplied by the static server or origin bridge |
| Prebuilt assets | Doomsigner firmware, embit coordinator zip and Pyodide plus packages | Required locally for no-download operation; not rebuilt by this work |
| Playwright/Chromium | Runs automation, intercepts requests and supplies fake camera | Required for harness, not for a visitor's eventual demo |

`test/signet_bridge.py:67-84` explains the serving trick: `context.route()` catches
requests to the page origin, `route.fetch()` fetches the same path from
`http://127.0.0.1:<port>`, then `route.fulfill()` returns those bytes to the browser.
The `/seedsigner-simulator/` deployment prefix is stripped. No public DNS, TLS
connection to bitsaga.be, hosts-file entry, or certificate is needed for those
intercepted page requests. The separate signet.bitsaga.be origin was previously
left untouched. The bridge does not start a server or implement an API.

Use `test/serve.py`, not `python -m http.server`. It overlays `src/web`,
`src/shims`, and `build/out` and emits COOP/COEP headers. SharedArrayBuffer and the
worker's input channels require cross-origin isolation. Loopback is a secure
context; public hosting needs HTTPS as well as those headers.

The old external driver directory is under `~/apps/bitsaga/services/signet/test`
for this machine's account. `HERE` precedes it on `sys.path`, so this repo's
`signet_bridge.py` wins; the external directory supplies only `simdrive` here.
Inspection also found two less obvious driver dependencies: its own hardcoded
wallet zip for QR rendering and automatic selection of a cached Chromium.
The local harness now points QR rendering at `find_asset("wallet-doomsigner.zip")`
when available, and lets Playwright select Chromium when
`PLAYWRIGHT_BROWSERS_PATH` is set. The external module was not edited.

## Local harness configuration

`test/test_musig_e2e.py` remains gitignored and outside `test/run.py`'s SUITE.
Its local edits are deliberately not force-added to Git. This report is tracked;
a clone does not acquire the ignored harness or the external driver from this
commit. These settings describe the edited harness on this box:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SIM_URL` | `https://bitsaga.be` | Page base URL; harness appends `/wallet.html?firmware=doomsigner&debug=1&wallet=1` and derives interception origin from it |
| `SIM_PORT` | `8792` | Existing loopback static server port |
| `MUSIG_E2E_SIMDRIVE` | Original absolute external test-directory path | Directory containing `simdrive.py`, not the filename |
| `SIM_ARTIFACT_DIR` | Original `~/.cache/tmp/musig-e2e` location for this account | Screenshots, console and diagnostic PSBTs |
| `MUSIG_E2E_OFFLINE` | unset | Set `1` to abort browser requests outside the intercepted page origin; does not mock a chain |
| `MUSIG_E2E_HEADLESS` | `1` | Set `0` for a visible Chromium window, with a working graphical display |
| `MUSIG_E2E_CARDS` | unset | Existing opt-in card path; not exercised in this run |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright default | Set to the provisioned browser directory from AGENTS.md |
| `SIM_ASSETS` | `build/out`, `src/web` | Shared harness asset lookup, including QR renderer's firmware zip |

Existing origin, external-directory, port, screenshot-directory and headless
defaults are retained. `SIM_URL` is a base URL, not a complete wallet URL with a
query. The bridge always serves local files even when the base looks public.
There is no deployed-site mode in this harness.

Run from the repository root, in two terminals. First:

```sh
"$HOME/apps/_scratch/.venv-sstest/bin/python" test/serve.py --port 8785 src/web src/shims build/out
```

Then, safely offline (the artifact directory must exist before setting TMPDIR):

```sh
mkdir -p test/artifacts
PLAYWRIGHT_BROWSERS_PATH="$HOME/apps/_scratch/.ms-playwright" \
SIM_PORT=8785 \
SIM_ARTIFACT_DIR="$PWD/test/artifacts" \
TMPDIR="$PWD/test/artifacts" \
MUSIG_E2E_OFFLINE=1 \
"$HOME/apps/_scratch/.venv-sstest/bin/python" -u test/test_musig_e2e.py
```

Set `SIM_URL=http://127.0.0.1:8785` to use a loopback page origin, and
`MUSIG_E2E_SIMDRIVE=/path/to/external/test` to use another driver checkout.
Neither change redirects the chain API. `MUSIG_E2E_HEADLESS=0` permits watching
locally if a graphical display is available; headful mode was not tested here.
Stop the static server with Ctrl-C after the run. Do not remove the offline flag
merely to obtain a passing result: the default unblocked flow has real test-chain
side effects.

### Observed run

Run on this worktree with the interpreter/browser from AGENTS.md, port 8785,
cards off, original bitsaga.be page origin, and external requests blocked.
The process exited **1**, not a timeout imposed by the runner. Decisive output:

```text
page: https://bitsaga.be/wallet.html?firmware=doomsigner&debug=1&wallet=1; assets: http://127.0.0.1:8785
  [MainMenuScreen] cosigner 1: loading the seed
  [SeedOptionsScreen] cosigner 1: exporting the key
cosigner 1 exported
OFFLINE blocked: GET https://signet.bitsaga.be/api/status
OFFLINE blocked: POST https://signet.bitsaga.be/api/scan
AssertionError: the panel never offered MuSig2: ['i', 'Close', 'Try again']
```

The device console was saved as `test/artifacts/device-console.log` (264 lines).
This proves local boot and the first seed/export route, not correctness of the
exported key or MuSig2 signing. The immediate blocker is the panel's initial
`/api/scan` dependency. Status also attempted a network request. Later required
services would be claim, scan and broadcast. The blocked run says nothing about
whether today's public service is healthy or its CORS headers are unchanged.

## What belongs in a visitor's browser

The browser already has two separate Python runtimes: the signer worker and a
lazy embit coordinator worker. `extras/embit-coordinator.js:18-78` starts the
latter; `extras/coordinator-worker.js:29-59` loads local Pyodide,
`wallet-embit.zip` and `extras/coordinator.py`. Key export, descriptor/address
construction, PSBT preparation, nonce-round coordination, signature collection,
transaction serialization, QR rendering/decoding and tutorial presentation can
all happen in the tab. Private signing inputs need not be sent to a backend.
This is a simulation, not secure custody; use only public test seeds and never
fund those keys with anything valuable.

Static hosting must provide all assets and isolation headers. A live demo also
needs infrastructure outside this repo: a working test-chain node/indexer,
faucet with test coins, and an HTTP API reachable by visitors. The present flow
uses `GET /api/status`, `POST /api/scan`, `POST /api/claim`, and
`POST /api/broadcast`. A demo claiming confirmation additionally needs
`GET /api/tx-proof?txid=...`. No node is needed on the visitor's machine, but a
node is needed somewhere for an actual chain spend.

There is no chainless success backend here. A deliberately simulated demo could
instead implement a local-in-tab ledger with fabricated UTXOs, correctly remove
spent outpoints and expose replacement outputs for spend two. It must label
funding, broadcast and confirmation as simulated. Independent signature and
transaction checks would still be needed; returning invented txids is not
proof that MuSig2 worked.

## Origin restriction: options and evidence

Repository comments say only `https://bitsaga.be` is allowed. This is not an
inherent MuSig2 limitation, nor proof of today's server configuration. In
particular, `docs/SIGNET-API.md` says broadcast is missing, while
`test/test_wallet_live.py:76-82` explicitly says broadcast and scan were added.
Do not commission a duplicate endpoint based solely on the older document.
Current service reachability, CORS preflight, rate limits, chain identity and
Taproot acceptance need a separately authorized deployment check.

1. **Host at the already allowed origin.** Simplest live-demo option if the
   documented CORS policy and API contracts still hold. New frontend origin is
   unnecessary. Deployment and service validation are still external work.
2. **Add a specific demo origin to the API allowlist.** Backend/deployment change;
   allow the needed methods and JSON preflights, not arbitrary credentialed
   origins. Static hosting isolation and CSP need checking too.
3. **Use a same-origin HTTP gateway we operate.** Backend/deployment work with
   fixed upstream endpoints, validation, timeouts and faucet/broadcast limits.
   Browser API selection must be wired deliberately: currently only localhost
   with an `e2e` query parameter switches to same-origin `/api`
   (`signet-coordinator.js:37-44`); the static test server has no API handlers.
4. **Make an explicitly simulated offline demo.** Requires browser/API-adapter
   work but no live-chain service. It cannot claim mined confirmation.

Playwright origin interception is test tooling, not something a public browser
tab can perform. CORS is not fixed by choosing a different URL in this Python
harness. A public live demo on an arbitrary origin cannot be delivered by changes
to this repository alone with the currently documented service policy. At the
already allowed origin, backend changes may be unnecessary, but that is
conditional on validating the existing services, not an observed result here.

## What `extras/musig.js` already supplies

- Registration with the wallet panel, three distinct exported cosigners,
  address construction, and the MuSig2 wallet view (`69-243`, `451-506`).
- A 2-of-3 policy: key path aggregates cosigners A+B; fallback leaves aggregate
  A+C and B+C. This is not a native arbitrary-threshold MuSig2 operation.
  The current signing flow exercises only A+B, not fallback leaves.
- Balance scanning over five receive indices, faucet request/polling, and
  spent-outpoint tracking (`256-325`).
- One-input self-spends with a fixed 1,000-sat fee, PSBT presentation, returned
  QR collection and recursive nonce/signature trips (`341-447`). Despite the
  button label, it selects one coin rather than consolidating every coin.
- Nonce-pool transport, trip/spare counters and broadcast-result display. It
  uses `WalletTutorial.specterFrames`, panel `present`, `watch` and `readPsbt`.

This is a manual feature, not the requested guided demo. `wallet-tutorial.js`
has useful pacing, pause/step/hands-on controls, evidence-driven actions and
confirmation polling, but its builders are closure-private and its public
exports do not accept a MuSig step plan. It is gated to smartcard firmware;
MuSig extras load only for doomsigner. Tutorial and wallet panel mounting are
mutually exclusive. Reuse requires a small integration design, not simply
calling the old tutorial with a different descriptor.

## Proof gaps before promising the nonce benefit

The harness itself documents fragile card input and QR timing paths. Cards are
off by default; its own notes say only card-backed signing restocks pooled
nonces. Other comments describe separate cards, while implementation saves both
signing seeds on card index 0. None of those card paths was verified here.
The harness feeds the panel's PSBT frames through the external driver's camera
canvas because the normal panel-to-device optical path was unreliable. A public
demo cannot depend on that Python camera intervention.

`extras/coordinator.py:390-407` compares an issued public nonce against a field
in the returned PSBT, not against a nonce extracted from a confirmed transaction.
`spend_returned` clears the issued list after the first response, reports missing
matches without failing, and finalizes based on witness presence (`452-490`).
The `used` counter is accumulated by the UI; the harness's final predicate only
checks `second["used"] > 0`. It does not assert fewer trips than spend one, verify
signatures independently, require a mined proof, or prove single-use card-secret
consumption. Matching retained PSBT metadata is narrower evidence than the
harness's original prose claims. A demo must not advertise those stronger claims
until suitable independent checks exist.

## Pieces to build and rough size

These are engineering estimates, not measured completion dates. One experienced
engineer, existing assets available; upstream firmware failures can expand them.

| Piece | Deliverable | Rough effort |
| --- | --- | --- |
| Service decision and validation | Choose live allowed-origin, gateway, or simulated ledger; validate contracts and identity without accidental mainnet use | 1–3 days; service repairs additional |
| Guided-run integration | MuSig-specific plan on doomsigner, explicit readiness events and firmware gates; coexist with wallet panel | 2–4 days |
| Seed and signing choreography | Three public seeds, exports, two signers, nonce vs signature rounds, correct QR lifetimes, no Python driver | 3–6 days |
| Card and pool correctness | Resolve card input/state issues, abort/restart behavior and one-time nonce handling; compare cold and warm rounds honestly | 3–7 days, potentially upstream work |
| Verification and result language | Independent transaction/signature validation, per-spend counters, optional chain proof, explicit fallback coverage limits | 2–4 days |
| Visitor reliability | Loading, faucet limits, empty balances, retry, pause, timeout, refresh/restart, small screens and secure-context failures | 2–4 days |
| Repeatable testing | Stateful offline API fixture with negative cases, console-oracle browser tests; separately authorized live test | 2–4 days |

Budget roughly **three to six engineer-weeks** for a credible public live demo,
with uncertainty concentrated in card/nonce correctness and camera timing.
A narrower offline signing walkthrough without pooled-nonce claims is a smaller
first milestone; it still needs a real fixture and independent signature checks.
No backend implementation, public demo, complete offline mock, or firmware fix
was added in this task. No files under `src/`, `build/`, or baselines were edited.

**Bottom line:** configurable local boot works; end-to-end MuSig2 success remains
unobserved and blocked offline by the chain API. Recommend an explicitly
simulated signing walkthrough first, then a separately validated live Signet
mode. Do not promise pooled-nonce savings or public-chain confirmation from the
current harness's success predicate.
