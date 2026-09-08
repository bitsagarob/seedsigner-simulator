# The coordinator

A coordinator builds transactions and moves bytes. It holds no keys and signs
nothing: every signature and every secret belongs to the device.

Its chain half used to be `signet-coordinator.js`, 1,342 lines of hand-written
BIP32, bech32, PSBT and transaction code, written out rather than pulled in so a
page claiming to be checkable did not ship a library nobody reads. It is now
[embit](https://github.com/diybitcoinhardware/embit), which is the library the
device itself runs, so both halves agree about what a PSBT is by construction
rather than by luck.

## What is whose

| | |
| --- | --- |
| descriptors, keys, addresses, transactions, the PSBT container, key aggregation | embit |
| the MuSig2 fields in a PSBT, [BIP-373](https://bips.dev/373/), and the nonce pool | `coordinator.py`, about 150 lines |
| the panel, the camera, QR in and out, calls to the faucet | JavaScript |
| every signature | the device |
| Bitcoin Core | not here. It is the oracle the tests diff against |

embit is pinned to one commit of our fork, which is upstream plus BIP-390
`musig()` descriptors: three files and 230 added lines. `wallet-embit.zip` holds
that commit and nothing else, so its hash can be reproduced by fetching the
commit and rebuilding. `coordinator.py` is served as a plain file and listed in
`build/checksums.txt`.

## Three keys stand for one aggregate

A MuSig2 PSBT refers to the same aggregate three ways, and using the wrong one
writes a field the signer ignores without complaining.

| | | |
| --- | --- | --- |
| plain | KeyAgg of the undelivered participants | `0x1a` keydata |
| derived | plus BIP-328 | the taproot derivations, and the internal key |
| signing | plus the taptweak, for a key path | `0x1b` nonce keydata |

`musig_aggregates` returns all three so nothing downstream has to work it out.

## The nonce pool

MuSig2 costs two visits per signer: one to publish a nonce, one to sign once
every other nonce is known. A nonce made before there is a transaction is still
a valid nonce, so a device that leaves spares behind lets the coordinator put
one into the next transaction, and every signer signs on its first look.

This is FROST's preprocessing stage applied to MuSig2. What makes it safe is
that the secret half is sealed to a SeedKeeper which releases each one exactly
once; publishing a nonce twice hands anyone the private key.

An entry is spent **when it is issued**, not when a signed PSBT comes back: a
transaction that is built and abandoned has still published that nonce.

The only honest check is `pool_verify`, which compares the nonce the signer
published against the one it was given. Counting trips proves nothing, because a
device that ignored the pooled nonce still produces a valid transaction. Ask it
of the PSBT the device handed back: finalising strips the published nonce.

## Checking it

```
python3 test/test_coordinator_parity.py      # embit against the JavaScript, and against Core
python3 test/test_coordinator_flow.py        # the spend flow, no browser, no device
python3 test/test_pool_conformance.py        # the pooled-nonce field, against the shipped firmware
```

`signet-coordinator.js` stays even though the page no longer calls it. It is a
second, independent implementation, and `test_tutorial.py` still compares it
against embit. Two implementations agreeing is worth something; one agreeing
with itself is not.

## Next: cache the ECDH share too

Spec from apps-6c, 2026-09-08, verified in code before recording.

A silent-payment send costs a second device trip because each signer has to
compute its ECDH share. `musig2_psbt.write_share` is `_tweak_mul(scan_key,
secret)`: no outpoint, no sighash, no amount, so a share is a pure function of
(recipient scan key, participant secret) and stays valid for that counterparty
forever, labels included. Cache it exactly like a pooled nonce, with one
difference that matters: **do not spend it on issue.** A published nonce is
burned; a share is deterministic and reusable.

Then a repeat payment to a known recipient costs one trip per signer, the same
as single-sig.

Prerequisite the spec missed: none of the silent-payment flow is in this
coordinator yet. `buildSpSendPsbt` and `finaliseSpSend` are still hand-written
JavaScript, and `spend_start` builds a taproot MuSig2 spend, not a BIP-376
PSBTv2. Port the SP flow first, or there is nothing for `share_dress` to dress.

Known gap, theirs: `sp_scan_keys` reads only outputs, so shares can only be
harvested for recipients being paid in that transaction. The first payment to a
new counterparty still costs two trips. Pre-loading a roster needs a new PSBT
field.

Proof, since counting trips proves nothing: a cached share must be byte-identical
to one the device makes fresh, the coordinator-derived `PSBT_OUT_SCRIPT` must
equal what the device re-derives, and a corrupted proof must be refused.
