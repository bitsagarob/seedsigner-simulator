// The nonce pool, on the page.
//
// MuSig2 costs two visits per signer. The first collects a public nonce, the
// second turns everyone's nonces into a partial signature, and nothing can
// merge them because a signer cannot sign until it has seen every other
// signer's nonce. So an air-gapped 2 of 3 is four trips to the device, where
// ordinary multisig is two.
//
// It does not have to be. A nonce made before there is a transaction to spend
// it on is still a valid nonce: BIP-327 allows nonce generation with no message
// and no aggregate key. A signer that leaves spare nonces behind in the PSBT it
// hands back has published its half of round one in advance. If the coordinator
// keeps them and puts one into the next transaction it builds, every nonce is
// present before anybody visits a device, and each signer signs on its first
// look. One visit each, the same as ordinary multisig.
//
// This file is the bookkeeping that makes that safe, and it is the whole
// feature. It contains no cryptography of any kind:
//
//   * harvest -- take the spare nonces out of a PSBT coming back from a device
//   * dress   -- put one into a PSBT going out, in the field BIP-373 defines
//   * refuse  -- never hand out the same entry twice, for any reason
//
// The aggregation stays where it already works. Bitcoin Core is a complete
// MuSig2 coordinator: it validates the nonces, sums the partial signatures and
// finalises the transaction, and it does all of that at the node with no wallet
// loaded. Nothing here reimplements a line of it.
//
// It carries its own byte helpers rather than borrowing signet-coordinator.js's
// so that it can be read, tested and lifted out on its own. That is about
// thirty lines of ordinary varint and hex, duplicated on purpose.

(function (scope) {
  "use strict";

  // ------------------------------------------------------------ bytes

  function hex(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, "0");
    return out;
  }

  function unhex(text) {
    var out = new Uint8Array(text.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(text.substr(i * 2, 2), 16);
    return out;
  }

  function concat(parts) {
    var total = parts.reduce(function (n, part) { return n + part.length; }, 0);
    var out = new Uint8Array(total);
    var at = 0;
    parts.forEach(function (part) { out.set(part, at); at += part.length; });
    return out;
  }

  function equal(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function varint(value) {
    if (value < 0xfd) return new Uint8Array([value]);
    if (value <= 0xffff) return new Uint8Array([0xfd, value & 0xff, value >> 8]);
    return new Uint8Array([0xfe, value & 0xff, (value >> 8) & 0xff,
                           (value >> 16) & 0xff, (value >> 24) & 0xff]);
  }

  function fromBase64(text) {
    var binary = typeof atob === "function"
      ? atob(text)
      : Buffer.from(text, "base64").toString("binary");
    var out = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
    return out;
  }

  function toBase64(bytes) {
    var binary = "";
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return typeof btoa === "function"
      ? btoa(binary)
      : Buffer.from(binary, "binary").toString("base64");
  }

  // ------------------------------------------------------------ the fields

  // BIP-373. Read off a PSBT Bitcoin Core produced rather than off the BIP,
  // because a coordinator that writes the wrong keydata writes a field the
  // signer silently ignores, and silence is the one failure this must not have.
  // Verified 2026-09-06 against Core v31.1.0: the keydata is a 33 byte
  // participant key then a 33 byte aggregate key, with a 32 byte leaf hash
  // appended only for a script path spend, and the value is a 66 byte nonce.
  var PSBT_IN_MUSIG2_PUB_NONCE = 0x1b;

  // BIP-174 proprietary space, which is where anything not in a BIP belongs.
  // The identifier keeps these out of every other producer's way, and Core
  // carries them through walletprocesspsbt, combinepsbt and finalizepsbt
  // untouched, which is what makes this possible at all.
  var PSBT_IN_PROPRIETARY = 0xfc;
  var IDENTIFIER = "DOOMSIGNER";
  var SUBTYPE_POOLED_NONCE = 0x01;

  // What the device leaves behind: the public nonce it will publish, then the
  // secret nonce sealed under keys that never leave its smartcard. The sealed
  // half is opaque here and stays that way. Only the card can open it, and only
  // once, so it is not a secret and nothing in this file interprets it.
  var SIZE_PUBNONCE = 66;
  var SIZE_SEALED = 144;
  var SIZE_ENTRY = SIZE_PUBNONCE + SIZE_SEALED;

  function identifierBytes() {
    var out = new Uint8Array(IDENTIFIER.length);
    for (var i = 0; i < IDENTIFIER.length; i++) out[i] = IDENTIFIER.charCodeAt(i);
    return out;
  }

  /** The proprietary key prefix every DoomSigner field of one subtype shares. */
  function proprietaryPrefix(subtype) {
    var id = identifierBytes();
    return concat([new Uint8Array([PSBT_IN_PROPRIETARY]), varint(id.length), id,
                   varint(subtype)]);
  }

  /** Where one unused nonce waits, before it has a transaction to belong to. */
  function pooledNonceKey(participant, index) {
    return concat([proprietaryPrefix(SUBTYPE_POOLED_NONCE), participant,
                   new Uint8Array([(index >> 8) & 0xff, index & 0xff])]);
  }

  /** Where a public nonce goes once it does belong to one. */
  function pubNonceKey(participant, aggregate, leafHash) {
    return concat([new Uint8Array([PSBT_IN_MUSIG2_PUB_NONCE]), participant,
                   aggregate, leafHash || new Uint8Array(0)]);
  }

  // ------------------------------------------------------------ the PSBT

  function reader(bytes) {
    var at = 0;
    return {
      left: function () { return bytes.length - at; },
      take: function (n) { at += n; return bytes.subarray(at - n, at); },
      varint: function () {
        var first = bytes[at++];
        if (first < 0xfd) return first;
        if (first === 0xfd) { at += 2; return bytes[at - 2] | (bytes[at - 1] << 8); }
        at += 4;
        return bytes[at - 4] | (bytes[at - 3] << 8) | (bytes[at - 2] << 16)
          | (bytes[at - 1] * 0x1000000);
      },
    };
  }

  /**
   * Every key/value map in a PSBT: the globals, then one per input, then one
   * per output. Records are kept in the order they were read and handed back
   * the same way, so a PSBT that is parsed and reserialised without being
   * changed comes out byte for byte as it went in.
   */
  function psbtMaps(psbtBase64) {
    var read = reader(fromBase64(psbtBase64));
    if (hex(read.take(5)) !== "70736274ff") throw new Error("that is not a PSBT");
    var maps = [];
    while (read.left() > 0) {
      var map = [];
      for (;;) {
        var keyLength = read.varint();
        if (keyLength === 0) break;
        var key = read.take(keyLength);
        map.push({ key: key, value: read.take(read.varint()) });
      }
      maps.push(map);
    }
    return maps;
  }

  function serialisePsbt(maps) {
    return toBase64(concat([unhex("70736274ff")].concat(
      maps.map(function (map) {
        return concat(map.map(function (entry) {
          return concat([varint(entry.key.length), entry.key,
                         varint(entry.value.length), entry.value]);
        }).concat([new Uint8Array([0x00])]));
      }))));
  }

  function startsWith(bytes, prefix) {
    if (bytes.length < prefix.length) return false;
    return equal(bytes.subarray(0, prefix.length), prefix);
  }

  // ------------------------------------------------------------ errors

  // Three things can go wrong here and only one of them is serious. The
  // messages say which, in the words the situation deserves: an empty pool is
  // a slower spend and reads like one, a reused nonce is a key disclosure and
  // reads like one.
  function PoolError(code, message, detail) {
    var error = new Error(message);
    error.name = "PoolError";
    error.code = code;
    error.detail = detail || {};
    return error;
  }

  function shortKey(participant) {
    return hex(participant).slice(0, 8);
  }

  // ------------------------------------------------------------ the store

  /** A pool kept in memory. Enough for a test, and the shape localStorage fills. */
  function memoryStore() {
    var data = {};
    return {
      get: function (key) { return data[key] === undefined ? null : data[key]; },
      set: function (key, value) { data[key] = value; },
    };
  }

  function browserStore(prefix) {
    return {
      get: function (key) { return localStorage.getItem(prefix + key); },
      set: function (key, value) { localStorage.setItem(prefix + key, value); },
    };
  }

  // ------------------------------------------------------------ the pool

  function create(options) {
    var store = (options && options.store) || memoryStore();
    var clock = (options && options.clock) || function () { return Date.now(); };

    function load(walletId) {
      var raw = store.get(walletId);
      return raw ? JSON.parse(raw) : { entries: [], spent: {} };
    }

    function save(walletId, state) {
      store.set(walletId, JSON.stringify(state));
    }

    /**
     * Take the spare nonces out of a PSBT a device has handed back.
     *
     * An entry is identified by its public nonce and nothing else. That is what
     * the device will publish and what the chain will eventually commit to, so
     * two entries with the same public half are the same nonce however they
     * arrived, and recognising that is what stops the same nonce entering the
     * pool twice by two routes.
     */
    function harvest(walletId, psbtBase64) {
      var state = load(walletId);
      var known = {};
      state.entries.forEach(function (entry) { known[entry.id] = true; });

      var prefix = proprietaryPrefix(SUBTYPE_POOLED_NONCE);
      var added = 0;
      var seen = 0;

      // The maps after the globals are the inputs and then the outputs, and
      // nothing distinguishes the two without parsing the unsigned transaction.
      // Both are walked because a pooled nonce record is recognised by its key
      // rather than by where it sits, and an output map has never carried one.
      // What is recorded is therefore the map it was found in, not an input
      // number, and nothing downstream treats it as one.
      psbtMaps(psbtBase64).slice(1).forEach(function (map, mapIndex) {
        map.forEach(function (record) {
          if (!startsWith(record.key, prefix)) return;
          if (record.value.length !== SIZE_ENTRY) return;
          seen += 1;
          var participant = record.key.subarray(prefix.length,
                                                prefix.length + 33);
          var id = hex(record.value.subarray(0, SIZE_PUBNONCE));
          // Already spent means this arrived back attached to the transaction
          // it was spent on. It is not a spare, whatever field it travelled in.
          if (known[id] || state.spent[id]) return;
          known[id] = true;
          added += 1;
          state.entries.push({
            id: id,
            participant: hex(participant),
            entry: hex(record.value),
            foundIn: mapIndex,
            harvested: clock(),
          });
        });
      });

      save(walletId, state);
      return { added: added, seen: seen, spare: state.entries.length };
    }

    /** How many unused nonces this signer has waiting. */
    function spare(walletId, participantHex) {
      return load(walletId).entries.filter(function (entry) {
        return entry.participant === participantHex;
      }).length;
    }

    /**
     * Put one unused nonce per signer into a PSBT about to go out.
     *
     * `participants` is one entry per signer that should sign without a second
     * visit: { participant, aggregate, leafHash }, keys as 33 byte arrays and
     * the leaf hash only for a script path spend.
     *
     * A signer with nothing in the pool is not an error. It is named in
     * `missing` and it costs that signer the extra trip it would have cost
     * anyway; the spend still completes.
     *
     * An entry is spent the moment it is written into a PSBT, not when a signed
     * PSBT comes back. A transaction that is built and then abandoned has still
     * published that nonce, and a nonce published twice is the whole danger.
     * Erring this way loses nonces when a user changes their mind, which is
     * exactly why the device leaves four behind rather than one.
     */
    function dress(walletId, psbtBase64, participants, inputIndex) {
      var state = load(walletId);
      var at = inputIndex || 0;
      var maps = psbtMaps(psbtBase64);
      var map = maps[1 + at];
      if (!map) throw PoolError("POOL_NO_INPUT",
        "This transaction has no input " + at + " to put a nonce on.", { input: at });

      var used = [];
      var missing = [];

      participants.forEach(function (who) {
        var wanted = hex(who.participant);
        var index = -1;
        for (var i = 0; i < state.entries.length; i++) {
          if (state.entries[i].participant === wanted) { index = i; break; }
        }
        if (index === -1) {
          missing.push(wanted);
          return;
        }
        var entry = state.entries.splice(index, 1)[0];
        if (state.spent[entry.id]) {
          // Reaching here means the pool disagreed with itself. Refuse rather
          // than reconcile: there is no version of this worth guessing at.
          throw PoolError("POOL_REUSE", reuseMessage(entry, state.spent[entry.id]),
                          { entry: entry, spentAt: state.spent[entry.id] });
        }
        var bytes = unhex(entry.entry);
        map.push({
          key: pubNonceKey(who.participant, who.aggregate, who.leafHash),
          value: bytes.subarray(0, SIZE_PUBNONCE),
        });
        map.push({
          key: pooledNonceKey(who.participant, 0),
          value: bytes,
        });
        state.spent[entry.id] = { at: clock(), wallet: walletId };
        used.push({ participant: wanted, id: entry.id });
      });

      save(walletId, state);
      return { psbt: serialisePsbt(maps), used: used, missing: missing };
    }

    function reuseMessage(entry, spent) {
      var when = new Date(spent.at).toISOString().replace("T", " ").slice(0, 16);
      return "Refusing to reuse a nonce. The spare nonce for key "
        + shortKey(unhex(entry.participant)) + " was already given out on "
        + when + ". Signing twice under one nonce hands anyone that key's "
        + "private key, so this transaction is not built.";
    }

    /**
     * Prove the signer used the nonce it was given.
     *
     * The only honest check. Counting round trips proves nothing, and the
     * absence of an error proves less: a device that quietly ignored the
     * pooled nonce and made a fresh one would still produce a valid signature
     * and a valid transaction. It would just have cost the visit this feature
     * claims to remove, and nothing would say so.
     *
     * So compare the bytes. The public nonce the finished PSBT publishes for a
     * participant must be the first 66 bytes of the pool entry that was issued
     * for it.
     */
    function verifyUsed(psbtBase64, issued, participant, aggregate, leafHash,
                        inputIndex) {
      var maps = psbtMaps(psbtBase64);
      var map = maps[1 + (inputIndex || 0)] || [];
      var wanted = pubNonceKey(participant, aggregate, leafHash);
      var published = null;
      map.forEach(function (record) {
        if (equal(record.key, wanted)) published = record.value;
      });
      if (!published) {
        throw PoolError("POOL_NOT_PUBLISHED",
          "Key " + shortKey(participant) + " published no public nonce, so "
          + "there is nothing to compare the pooled one against.",
          { participant: hex(participant) });
      }
      var expected = unhex(issued).subarray(0, SIZE_PUBNONCE);
      if (!equal(published, expected)) {
        throw PoolError("POOL_MISMATCH",
          "Key " + shortKey(participant) + " published a different nonce than "
          + "the one it was given. The pooled nonce was not used, so this "
          + "spend cost that signer two visits rather than one.",
          { published: hex(published), expected: hex(expected) });
      }
      return true;
    }

    return {
      harvest: harvest,
      spare: spare,
      dress: dress,
      verifyUsed: verifyUsed,
      state: load,
    };
  }

  scope.NoncePool = {
    create: create,
    memoryStore: memoryStore,
    browserStore: browserStore,
    pooledNonceKey: pooledNonceKey,
    pubNonceKey: pubNonceKey,
    psbtMaps: psbtMaps,
    serialisePsbt: serialisePsbt,
    SIZE_PUBNONCE: SIZE_PUBNONCE,
    SIZE_SEALED: SIZE_SEALED,
    IDENTIFIER: IDENTIFIER,
    SUBTYPE_POOLED_NONCE: SUBTYPE_POOLED_NONCE,
    PSBT_IN_MUSIG2_PUB_NONCE: PSBT_IN_MUSIG2_PUB_NONCE,
  };
})(typeof module !== "undefined" && module.exports ? module.exports : this);
