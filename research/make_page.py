#!/usr/bin/env python3
"""Write the unlisted proof page for the MuSig2 silent payment demo.

Reads the RESULT.json of each run, copies the device screenshots into the
site's image tree, and writes webapp/insights/musig2-silent-payment.html with
the head (styles, includes) taken from the existing silent payments article so
it looks like the rest of the site. noindex, not in the sitemap, not linked.
"""
import json, os, re, shutil, html

SITE = "$WEBAPP_DIR"
SRC_PAGE = SITE + "/insights/silent-payments-on-seedsigner.html"
OUT_PAGE = SITE + "/insights/musig2-silent-payment.html"
IMG_DIR = SITE + "/images/insights/musig2-silent-payment"
SHOTS = "$TMP/musig-sp-shots-%s"
SLUG = "musig2-silent-payment"
TITLE = "A 2-of-3 MuSig2 multisig paying a silent payment address, on mainnet"
DESC = ("Two simulated SeedSigners holding a MuSig2 2-of-3 pay a BIP-352 silent payment address on "
        "mainnet. One taproot input, one taproot output, one 64-byte signature. Every screen, every "
        "transaction, and how to check it.")


def load(net):
    p = SHOTS % net + "/RESULT.json"
    return json.load(open(p)) if os.path.exists(p) else None


def head():
    src = open(SRC_PAGE).read()
    h = src[:src.index("<!-- jsonld:start -->")]
    h = h.replace("Silent payments on SeedSigner · Bitsaga Insights", TITLE + " · Bitsaga Insights")
    h = re.sub(r'<meta name="description" content="[^"]*">', '<meta name="description" content="%s">' % html.escape(DESC, quote=True), h)
    h = re.sub(r'<meta property="og:description" content="[^"]*">', '<meta property="og:description" content="%s">' % html.escape(DESC, quote=True), h)
    h = h.replace("/insights/silent-payments-on-seedsigner", "/insights/" + SLUG)
    h = h.replace("/images/insights/silent-payments-mainnet-demo/00-page-boot.png", "/images/insights/%s.png" % SLUG)
    return h + "</head>\n"


def copy_shots(net, prefix):
    """Copy the screens the page shows. Returns {(device, label): web path}.

    Matched by round and screen name, not by the running number, because the
    number of review screens depends on the transaction (a payment name adds
    one) and a fixed index would silently pick the wrong picture."""
    import glob
    d = SHOTS % net
    want = {
        "seed": "*-seed-loaded.png",
        "date": "*-date-set.png",
        "overview": "*-shares-PSBTOverviewScreen.png",
        "review": "*-shares-PSBTAddressDetailsScreen.png",
        "name": "*-shares-PSBTPaymentNameScreen.png",
        "step1": "*-shares-LargeIconStatusScreen.png",
        "qr1": "*-shares-QRDisplayScreen.png",
        "review2": "*-nonce-PSBTAddressDetailsScreen.png",
        "step2": "*-nonce-LargeIconStatusScreen.png",
        "step3": "*-sign-LargeIconStatusScreen.png",
        "qr3": "*-sign-QRDisplayScreen.png",
    }
    out = {}
    os.makedirs(IMG_DIR, exist_ok=True)
    for dev in "AB":
        for label, pattern in want.items():
            hits = sorted(glob.glob(os.path.join(d, dev + pattern)))
            if not hits:
                continue
            name = "%s%s-%s.png" % (prefix, dev, label)
            shutil.copy(hits[0], os.path.join(IMG_DIR, name))
            out[(dev, label)] = "/images/insights/%s/%s" % (SLUG, name)
    return out


def shot(imgs, dev, label, caption):
    p = imgs.get((dev, label))
    if not p:
        return ""
    return ('<figure class="shot"><img src="%s" alt="%s" loading="lazy" width="240" height="240">'
            '<figcaption>%s</figcaption></figure>' % (p, html.escape(caption), html.escape(caption)))


def tx_link(net, txid):
    if net == "mainnet":
        return '<a href="https://mempool.space/tx/%s">%s</a>' % (txid, txid)
    if net == "signet":
        return '<a href="https://signet.bitsaga.be/api/tx-proof?txid=%s">%s</a>' % (txid, txid)
    return "<code>%s</code>" % txid


def addr_link(net, a):
    if net == "mainnet":
        return '<a href="https://mempool.space/address/%s"><code>%s</code></a>' % (a, a)
    return "<code>%s</code>" % a


def body(main, signet, regtest, imgs_main, imgs_signet):
    r = main or signet
    net = "mainnet" if main else "signet"
    imgs = imgs_main if main else imgs_signet
    fp = r["fingerprints"]
    rows = ""
    for name, res in (("Mainnet", main), ("Bitsaga Signet", signet), ("Regtest", regtest)):
        if not res:
            continue
        n = {"Mainnet": "mainnet", "Bitsaga Signet": "signet", "Regtest": "regtest"}[name]
        rows += "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s vB</td></tr>" % (
            name, tx_link(n, res["txid"]), res.get("confirmed_block", "unconfirmed"),
            addr_link(n, res["sp_output_address"]), res["vsize"])

    import datetime
    published = datetime.date.fromisoformat(r["started"][:10]).strftime("%B %-d, %Y")
    scan_section = ""
    scan_txt = SHOTS % "mainnet" + "/SCAN.txt"
    if main and os.path.exists(scan_txt):
        scan = open(scan_txt).read().strip()
        scan_section = f'''
    <h2>Found by someone else's code</h2>
    <p>The sender's own arithmetic finding its own output proves little. So the receive side was checked with software that knows nothing about MuSig2, PSBTs or shares: setavenger's <a href="https://github.com/setavenger/blindbit-rs">blindbit-cli</a> (Rust), pointed at our <a href="https://silentpayments.net/lookout">BlindBit Oracle</a> and given only the recipient's scan key and spend public key, scanning block {main["confirmed_block"]}.</p>
    <pre><code>{html.escape(scan)}</code></pre>'''
    name_section = ""
    if r.get("payment_name"):
        name_section = f'''
    <h3>The recipient is a name</h3>
    <p>The transaction pays <code>{r["payment_name"]}</code>, a BIP-353 payment name. Its DNS record carries the silent payment address, and the PSBT carries the RFC 9102 DNSSEC proof of that record ({r["dnssec_proof_bytes"]} bytes). Each device validates the proof itself, from the root trust anchor down, and shows the name as verified only when the proof is valid, current, and covers exactly the address the output is derived from. A signer has no clock, so it takes its date from a timecode QR (<code>{r["timecode"]}</code>, UTC). The rule is that the date comes from a second screen; in this run the coordinator was that second screen, so the date check proves the mechanism and not independence.</p>'''
    return f'''<body>
<!--# include virtual="/includes/nav.html" -->

<main>
  <header class="page-head">
    <div class="shell">
      <p class="eyebrow"><a href="/insights">Insights</a> · Hardware</p>
      <h1>{html.escape(TITLE)}</h1>
      <p class="meta">By <a href="/about">Rob Segers</a> · {published}</p>
    </div>
  </header>
  <div class="article-hero"><img src="/images/insights/{SLUG}.png" alt="Two SeedSigner screens: one reviewing a payment to a verified name, the other confirming the multisig signature"></div>
  <div class="shell article-body">
    <a href="/insights" class="back-link">← Back to all insights</a>

    <p>Two people hold a 2-of-3 taproot multisig built with MuSig2 (BIP-327, BIP-390). They pay a silent payment address (BIP-352). Nobody holds the private key of the coin they spend, and a silent payment needs exactly that key. This page is the record of it happening on mainnet: every screen the two signers saw, every transaction, and how anyone can check it.</p>

    <div class="world-first-grid">
      <div class="world-first-card">
        <span class="wf-num">World first, as far as we can find</span>
        <p class="wf-title">A MuSig2 multisig paying a silent payment address on chain</p>
        <p class="wf-body">The design existed on paper (BIP-352's own footnote, and macgyver13's proposal with a draft Coldcard branch). We found no transaction on any network before this one. If you know of one, tell us and this line changes.</p>
        <p class="wf-proof">Proved · mainnet block {main["confirmed_block"] if main else "pending"}</p>
      </div>
      <div class="world-first-card">
        <span class="wf-num">Also first, same transaction</span>
        <p class="wf-title">Multisig signers verifying a payment name on device</p>
        <p class="wf-body">The recipient was a BIP-353 name. Both signers validated its DNSSEC proof themselves, from the root trust anchor down, before contributing anything.</p>
        <p class="wf-proof">Proved · same block</p>
      </div>
    </div>

    <h2>What this means if you hold bitcoin in a multisig</h2>
    <ul>
      <li><strong>Your multisig stops being visible.</strong> A classic 2-of-3 tells the whole world it is a 2-of-3, forever, on every coin. This one looks like a single key. Nobody can tell your setup from a phone wallet's.</li>
      <li><strong>You can pay a silent payment address at all.</strong> Silent payments exclude script multisig inputs. A MuSig2 key path is the only multisig that can send to an <code>sp1…</code> address.</li>
      <li><strong>The recipient gets a fresh output every time</strong>, from an address they published once. No address reuse on their side, no way to link your payment to their other payments.</li>
      <li><strong>You pay a name, and your signers check it.</strong> No long address to compare by eye; the device proves the name resolves to the address it is paying.</li>
      <li><strong>No co-signer can misdirect the money.</strong> Every share comes with a proof, and each device refuses to sign if any proof or the output does not check out.</li>
      <li><strong>Cheaper.</strong> One 64-byte signature instead of two signatures plus a script: this spend was {r["vsize"]} vB.</li>
    </ul>

    <div class="stat-box">
      <p style="margin:0;font-size:14px;letter-spacing:.14em;text-transform:uppercase;color:#f3a13d;font-weight:800">What the chain sees</p>
      <p style="margin:0;font-size:clamp(18px,2.4vw,26px);font-weight:700">one taproot input · one taproot output · one 64-byte signature</p>
      <p style="margin:0;color:rgba(255,249,239,.7)">No multisig is visible. No <code>sp1…</code> address ever appears on chain; a silent payment address is never written anywhere but the payer's own wallet.</p>
    </div>

    <h2>The transactions</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Network</th><th>Spend</th><th>Block</th><th>Output the recipient found</th><th>Size</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    <p>The spending input is the 2-of-3 coin at {addr_link(net, r["address"])}, funded by {tx_link(net, r["funding_txid"])}. The recipient's silent payment address is <code>{r["sp_address"]}</code>. It does not appear in either transaction; the output above was derived from it and from the input, and the recipient found it by scanning with its scan key, nothing else.</p>

    <h2>The screens</h2>
    <p>Two simulated SeedSigners, one seed each, in two separate browsers. The coordinator between them holds no key. Each signer scans the transaction three times.</p>
    {name_section}

    <h3>Step 1 of 3: a share and a proof</h3>
    <p>The output does not exist yet. The device shows who it is paying, and hands back its share of the Diffie-Hellman secret with a BIP-374 proof that the share was made with its own key.</p>
    <div class="shot-grid two">
      {shot(imgs, "A", "seed", "Signer A: seed loaded")}
      {shot(imgs, "B", "seed", "Signer B: seed loaded")}
      {shot(imgs, "A", "date", "Signer A: date taken from the timecode QR")}
      {shot(imgs, "B", "date", "Signer B: date taken from the timecode QR")}
      {shot(imgs, "A", "review", "Signer A reviews: the name, verified, the amount, the address")}
      {shot(imgs, "B", "review", "Signer B reviews the same")}
      {shot(imgs, "A", "step1", "Signer A: step 1 of 3, nothing signed")}
      {shot(imgs, "B", "step1", "Signer B: step 1 of 3, nothing signed")}
    </div>

    <h3>Step 2 of 3: nonces</h3>
    <p>The coordinator has combined the two shares into the output script. Each device verifies the other signer's proof and recomputes the script before it publishes a nonce. The review now shows the derived taproot address.</p>
    <div class="shot-grid two">
      {shot(imgs, "A", "review2", "Signer A reviews the derived output")}
      {shot(imgs, "B", "review2", "Signer B reviews the derived output")}
      {shot(imgs, "A", "step2", "Signer A: step 2 of 3, still nothing signed")}
      {shot(imgs, "B", "step2", "Signer B: step 2 of 3")}
    </div>

    <h3>Step 3 of 3: partial signatures</h3>
    <p>With both nonces in, each device verifies everything again and signs. Stock Bitcoin Core v31.1.0 aggregates the two partial signatures into the one Schnorr signature on chain, and relays it.</p>
    <div class="shot-grid two">
      {shot(imgs, "A", "step3", "Signer A: signed")}
      {shot(imgs, "B", "step3", "Signer B: signed")}
      {shot(imgs, "A", "qr3", "Signer A hands the transaction back")}
      {shot(imgs, "B", "qr3", "Signer B hands the transaction back")}
    </div>

    {scan_section}

    <h2>Why this needed new work</h2>
    <p>BIP-352 derives the recipient's output from the sum of the input private keys. For a taproot input that is the key behind the output key. In a MuSig2 key path no one has it; each signer has a share. Diffie-Hellman is linear, so the shares can be combined: each signer contributes <code>d_i · B_scan</code>, and the public KeyAgg coefficients, parity and tweaks turn the pieces into exactly what a single holder would have computed. BIP-352 itself sketches this in a footnote and says a proof may be needed.</p>
    <p>The proof is the part that matters. A share nobody can check lets one co-signer send the money to an output the recipient will never find. That is a loss of funds, not a privacy leak. Each share here travels with a BIP-374 proof against the signer's own participant key, and every device verifies every other proof, and the output script built from them, before it lets a nonce or a signature out. A tampered share, a borrowed proof and a wrong script are each covered by a test that expects a refusal.</p>
    <p>The PSBT fields are the ones macgyver13 proposed for exactly this case in <a href="https://gist.github.com/macgyver13/0f0281add9a27013c7ee519cad8084c6">Silent Payments from a MuSig2 Treasury</a> (July 2026), with a draft Coldcard implementation: <code>0x21</code> for the partial share and <code>0x22</code> for its proof, keyed by scan key and participant key. Using the same fields means a Coldcard on that branch and this device could sit in the same 2-of-3. We found no earlier on-chain transaction of this kind on any network; if you know one, tell us and this page will say so.</p>

    <h2>What is not claimed</h2>
    <ul>
      <li>The signers are the SeedSigner firmware running in a browser, not hardware. Same code, different box.</li>
      <li>BIP-352 says collaborative silent payments have no formal security proof. This does not change that.</li>
      <li>The standard BIP-375 per-input share and proof fields are not filled, because their proof needs the input's private key and nobody has it. Run over this PSBT, the BIP-375 reference validator passes the structure and input eligibility checks and fails ECDH coverage with <em>Silent payment output present but no ECDH share for scan key</em>. The per-signer proofs are the substitute, and a validator that reads them is a few lines.</li>
      <li>The coordinator fills the MuSig2 fields with a watch-only Bitcoin Core wallet, aggregates the shares itself, and hands the finished PSBT to Core to finalise. Core has no silent payment code and did not need any.</li>
    </ul>

    <h2>Check it yourself</h2>
    <p>Signers: fingerprints A <code>{fp["A"]}</code>, B <code>{fp["B"]}</code>, C <code>{fp["C"]}</code> (C never signed). Recipient R <code>{fp["R"]}</code>. Descriptor of the coin:</p>
    <pre><code>{html.escape(r["descriptor"])}</code></pre>
    <p>The final PSBTv2, with both shares, both proofs, both nonces and both partial signatures:</p>
    <pre><code style="word-break:break-all;white-space:pre-wrap">{r["psbt_final_v2"]}</code></pre>
    <p>The transaction as broadcast:</p>
    <pre><code style="word-break:break-all;white-space:pre-wrap">{r["tx_hex"]}</code></pre>
    <p>Code: firmware branch <code>musig2</code> on <a href="https://github.com/bitsagarob/seedsigner">bitsagarob/seedsigner</a> (helpers <code>musig2.py</code>, <code>musig2_psbt.py</code>, <code>musig2_session.py</code>, <code>musig2_sp.py</code> and their tests), simulator build served unlisted beside the <a href="/seedsigner-simulator/">SeedSigner simulator</a> at <code>/seedsigner-simulator/musig/wallet.html?firmware=doomsigner-musig</code>.</p>

    <p class="meta-note">Mainnet spend confirmed {published}, block {main["confirmed_block"] if main else "pending"}. Regtest and Bitsaga Signet runs the same day. Every screen, transaction and proof on this page is reproducible from the repository.</p>
  </div>
  </main>

  <div style="text-align:center;padding:40px 0 20px">
    <a href="/insights" style="display:inline-flex;align-items:center;gap:8px;color:#f7931a;font-weight:700;font-size:16px;text-decoration:none">
      <span style="font-size:20px">&#8592;</span> All insights
    </a>
  </div>

<!--# include virtual="/includes/footer.html" -->
</body>
</html>
'''


def main():
    mainnet, signet, regtest = load("mainnet"), load("signet"), load("regtest")
    imgs_main = copy_shots("mainnet", "main-") if mainnet else {}
    imgs_signet = copy_shots("signet", "signet-") if signet else {}
    page = head() + body(mainnet, signet, regtest, imgs_main, imgs_signet)
    with open(OUT_PAGE, "w") as f:
        f.write(page)
    print("wrote", OUT_PAGE, len(page), "bytes;", len(imgs_main), "mainnet shots,", len(imgs_signet), "signet shots")


if __name__ == "__main__":
    main()
