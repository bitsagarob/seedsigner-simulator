# The nonce pool

MuSig2 costs two visits per signer. The first collects a public nonce, the
second turns everyone's nonces into a partial signature, and nothing merges them
because a signer cannot sign until it has seen every other signer's nonce. An
air-gapped 2 of 3 is therefore four trips to the device, where ordinary multisig
is two. Ledger's own MuSig2 reference tool sidesteps this by keeping the device
plugged in for both rounds, which an air-gapped signer cannot do.

`nonce-pool.js` removes the extra trip, and it does it without any cryptography.

## How

A nonce made before there is a transaction to spend it on is still a valid
nonce: BIP-327 allows nonce generation with no message and no aggregate key. A
DoomSigner leaves four spare nonces behind in every PSBT it hands back, so it
has published its half of round one in advance. If the coordinator keeps them
and puts one into the next transaction it builds, every nonce is present before
anybody visits a device, and each signer signs on its first look.

Three responsibilities, and nothing else:

| | |
| --- | --- |
| `harvest` | take the spare nonces out of a PSBT coming back from a device |
| `dress` | put one into a PSBT going out, in the field BIP-373 defines |
| refuse | never hand out the same entry twice, for any reason |

Aggregation stays where it already works. Bitcoin Core is a complete MuSig2
coordinator: it validates the nonces, sums the partial signatures and finalises
the transaction, and it does all of that at the node with **no wallet loaded**.
Nothing here reimplements a line of it.

## The fields

Verified 2026-09-06 against Bitcoin Core v31.1.0 rather than read off the BIP,
because a coordinator that writes the wrong keydata writes a field the signer
silently ignores, and silence is the one failure this must not have.

**`PSBT_IN_MUSIG2_PUB_NONCE`** (BIP-373, type `0x1b`)

```
key    = 0x1b <33-byte participant pubkey> <33-byte aggregate pubkey> [32-byte leaf hash]
value  = <66-byte public nonce>
```

The leaf hash is present only for a script path spend. A key path spend has
66 bytes of keydata.

**The pooled nonce** (BIP-174 proprietary space, ours)

```
key    = 0xFC <compact_size(10)> "DOOMSIGNER" <compact_size(0x01)>
              <33-byte participant pubkey> <2-byte index>
value  = <66-byte public nonce> <144-byte sealed nonce>
```

Keyed by the signer and nothing else. A nonce generated with no message and no
aggregate is bound to neither, which is exactly what lets it serve a spend that
did not exist when it was made. The index only keeps several of them apart on
one input.

The sealed half is opaque. Only the signer's smartcard can open it, and only
once, so it is not a secret and nothing in this module interprets it.

Core carries both through `walletprocesspsbt`, `combinepsbt` and `finalizepsbt`
untouched, and finalisation still completes with them present. That is what
makes the whole thing possible, and it is measured, not assumed.

## An entry is spent when it is issued

Not when a signed PSBT comes back. A transaction that is built and then
abandoned has still published that nonce, and a nonce published twice is the
entire danger — two partial signatures under one secret nonce hand anyone the
private key.

Erring this way loses nonces when a user changes their mind. That is precisely
why the device leaves four behind rather than one.

## Proving it worked

Counting round trips proves nothing, and the absence of an error proves less. A
device that quietly ignored the pooled nonce and generated a fresh one would
still produce a valid signature and a valid transaction. It would just have cost
the visit this feature claims to remove, and nothing would say so.

So `verifyUsed` compares the bytes: the public nonce the finished PSBT publishes
for a participant must be the first 66 bytes of the pool entry that was issued
for it. That is the only honest check.

## Errors

Three things can go wrong and only one is serious.

| code | severity |
| --- | --- |
| `POOL_EMPTY` (reported in `missing`, never thrown) | not an error — that signer costs two visits, as it always did |
| `POOL_NOT_PUBLISHED` / `POOL_MISMATCH` | the pooled nonce was not used; the spend is fine, the saving was not made |
| `POOL_REUSE` | **key disclosure.** Hard refusal. The transaction is not built |

## Tests

```
node test/test_nonce_pool.js        # 12 checks against the module's own parser
python3 test/test_nonce_pool_core.py # the same PSBT, read back by Bitcoin Core
```

The fixtures are not hand-written. `test/make_nonce_pool_fixtures.py` generates
them from a real Core v31.1.0 regtest wallet holding a real `tr(musig(...))`
descriptor, because a pool that has only ever seen PSBTs this repository wrote
proves nothing about the wire.
