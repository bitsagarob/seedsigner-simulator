// The nonce pool, checked against PSBTs Bitcoin Core actually produced.
//
// A pool that only ever sees PSBTs this repository wrote proves nothing about
// the wire, so the fixtures here come from a real Core v31.1.0 regtest wallet
// holding a real tr(musig(...)) descriptor, with pooled nonce records spliced
// in the way the device leaves them. Regenerate them with:
//
//     python3 test/make_nonce_pool_fixtures.py > test/fixtures/nonce-pool.json
//
// Run:  node test/test_nonce_pool.js

"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

const pool = require(path.join(__dirname, "..", "src", "web", "nonce-pool.js"))
  .NoncePool;

const FIXTURES = path.join(__dirname, "fixtures", "nonce-pool.json");
const fixtures = JSON.parse(fs.readFileSync(FIXTURES, "utf8"));

let failures = 0;
let ran = 0;

function test(name, body) {
  ran += 1;
  try {
    body();
    console.log("  ok    " + name);
  } catch (error) {
    failures += 1;
    console.log("  FAIL  " + name);
    console.log("        " + error.message);
  }
}

function unhex(text) {
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(text.substr(i * 2, 2), 16);
  return out;
}

function hex(bytes) {
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, "0");
  return out;
}

const participant = unhex(fixtures.participant);
const aggregate = unhex(fixtures.aggregate);

console.log("nonce pool");

test("a PSBT parsed and reserialised is unchanged", () => {
  const round = pool.serialisePsbt(pool.psbtMaps(fixtures.stocked));
  assert.strictEqual(round, fixtures.stocked,
    "reserialising moved bytes, so nothing below can be trusted");
});

test("harvest finds every spare nonce the device left", () => {
  const p = pool.create();
  const result = p.harvest("w", fixtures.stocked);
  assert.strictEqual(result.seen, fixtures.pooledCount,
    `saw ${result.seen} records, expected ${fixtures.pooledCount}`);
  assert.strictEqual(result.added, fixtures.pooledCount);
  assert.strictEqual(p.spare("w", fixtures.participant), fixtures.pooledCount);
});

test("harvesting the same PSBT twice adds nothing the second time", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const again = p.harvest("w", fixtures.stocked);
  assert.strictEqual(again.added, 0, "a nonce entered the pool twice");
  assert.strictEqual(p.spare("w", fixtures.participant), fixtures.pooledCount);
});

test("harvest ignores a PSBT with no pooled nonces in it", () => {
  const p = pool.create();
  const result = p.harvest("w", fixtures.bare);
  assert.strictEqual(result.seen, 0);
  assert.strictEqual(result.added, 0);
});

test("dress writes a BIP-373 pub nonce and the sealed half", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const out = p.dress("w", fixtures.bare, [{ participant, aggregate }]);

  assert.strictEqual(out.missing.length, 0);
  assert.strictEqual(out.used.length, 1);

  const map = pool.psbtMaps(out.psbt)[1];
  const wanted = hex(pool.pubNonceKey(participant, aggregate));
  const published = map.filter((r) => hex(r.key) === wanted);
  assert.strictEqual(published.length, 1, "no PSBT_IN_MUSIG2_PUB_NONCE written");
  assert.strictEqual(published[0].value.length, 66,
    "a public nonce is 66 bytes and this one is not");
  assert.strictEqual(hex(published[0].value), out.used[0].id,
    "the published nonce is not the entry that was issued");

  const sealed = map.filter((r) => hex(r.key).startsWith("fc0a" +
    Buffer.from("DOOMSIGNER").toString("hex") + "01"));
  assert.strictEqual(sealed.length, 1, "the sealed half did not travel with it");
  assert.strictEqual(sealed[0].value.length, 66 + 144);
});

test("dress spends the entry, so the pool shrinks", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const before = p.spare("w", fixtures.participant);
  p.dress("w", fixtures.bare, [{ participant, aggregate }]);
  assert.strictEqual(p.spare("w", fixtures.participant), before - 1);
});

test("dress never issues the same entry twice", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const seen = new Set();
  for (let i = 0; i < fixtures.pooledCount; i++) {
    const out = p.dress("w", fixtures.bare, [{ participant, aggregate }]);
    assert.ok(!seen.has(out.used[0].id), "the same nonce was issued twice");
    seen.add(out.used[0].id);
  }
  assert.strictEqual(seen.size, fixtures.pooledCount);
});

test("an empty pool is reported, not thrown", () => {
  const p = pool.create();
  const out = p.dress("w", fixtures.bare, [{ participant, aggregate }]);
  assert.strictEqual(out.used.length, 0);
  assert.deepStrictEqual(out.missing, [fixtures.participant]);
});

test("a spent entry cannot come back through harvest", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const out = p.dress("w", fixtures.bare, [{ participant, aggregate }]);
  const spentId = out.used[0].id;
  // The device hands the transaction back with the same spares still on it.
  p.harvest("w", fixtures.stocked);
  const ids = p.state("w").entries.map((e) => e.id);
  assert.ok(!ids.includes(spentId),
    "a nonce that was already published came back into the pool");
});

test("verifyUsed passes when the signer used what it was given", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const out = p.dress("w", fixtures.bare, [{ participant, aggregate }]);
  assert.strictEqual(
    p.verifyUsed(out.psbt, fixtures.entryFor[out.used[0].id],
                 participant, aggregate),
    true);
});

test("verifyUsed refuses when the signer published a different nonce", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const out = p.dress("w", fixtures.bare, [{ participant, aggregate }]);
  const ids = Object.keys(fixtures.entryFor).filter((id) => id !== out.used[0].id);
  assert.throws(
    () => p.verifyUsed(out.psbt, fixtures.entryFor[ids[0]], participant, aggregate),
    (error) => {
      assert.strictEqual(error.code, "POOL_MISMATCH");
      assert.match(error.message, /published a different nonce/);
      return true;
    });
});

test("verifyUsed says so when no nonce was published at all", () => {
  const p = pool.create();
  p.harvest("w", fixtures.stocked);
  const entry = p.state("w").entries[0].entry;
  assert.throws(
    () => p.verifyUsed(fixtures.bare, entry, participant, aggregate),
    (error) => {
      assert.strictEqual(error.code, "POOL_NOT_PUBLISHED");
      return true;
    });
});

console.log(`\n${ran - failures}/${ran} passed`);
process.exit(failures === 0 ? 0 : 1);
