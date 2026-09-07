// The same coordinator API, answered by embit instead of by hand-written
// JavaScript.
//
// Everything not listed here falls through to signet-coordinator.js, so this
// can take over one flow at a time and the rest of the page does not notice.
// The shapes are the JavaScript's, byte arrays and all, because the page is
// written against those and a refactor that changed them would be two changes
// at once.
(function (scope) {
  "use strict";

  var JS = scope.SignetCoordinator;
  if (!JS) return;

  var WORKER = "coordinator-worker.js";
  var PYODIDE = "pyodide-e24b45d3/";
  var ZIP = "wallet-embit.zip";
  var CODE = "coordinator.py";

  var worker = null;
  var pending = {};
  var counter = 0;
  var booted = null;

  // What the worker loaded, so the page can show it rather than claim it.
  var loaded = null;

  function ask(message) {
    if (!worker) {
      worker = new Worker(WORKER);
      worker.onmessage = function (event) {
        var waiting = pending[event.data.id];
        if (!waiting) return;
        delete pending[event.data.id];
        if (event.data.ok) waiting.resolve(event.data.value);
        else waiting.reject(new Error(event.data.value));
      };
    }
    return new Promise(function (resolve, reject) {
      var id = String(++counter);
      pending[id] = { resolve: resolve, reject: reject };
      message.id = id;
      worker.postMessage(message);
    });
  }

  function boot() {
    if (!booted) {
      booted = ask({ type: "boot", indexURL: PYODIDE, zipURL: ZIP, codeURL: CODE })
        .then(function (hashes) { loaded = hashes; return hashes; });
    }
    return booted;
  }

  function call(fn, args) {
    return boot().then(function () { return ask({ fn: fn, args: args }); });
  }

  function unhex(text) { return JS.unhex(text); }

  // ------------------------------------------------------------ the 2 of 3

  function buildWallet(exportedKeys) {
    return call("wallet", [exportedKeys]).then(function (descriptor) {
      return { keys: exportedKeys, descriptor: descriptor, threshold: 2 };
    });
  }

  function deriveAddress(wallet, branch, index) {
    return call("address", [wallet.descriptor, branch, index]).then(function (out) {
      return {
        branch: branch,
        index: index,
        descriptor: wallet.descriptor,
        address: out.address,
        scriptPubkey: unhex(out.script_pubkey),
        witnessScript: unhex(out.witness_script),
        cosigners: out.cosigners.map(function (one) {
          return {
            pubkey: unhex(one.pubkey),
            fingerprint: one.fingerprint,
            derivation: one.derivation,
          };
        }),
      };
    });
  }

  function buildPsbt(input, source, destination, amount) {
    return call("build_psbt", [source.descriptor, source.branch, source.index,
                               { txid: input.txid, vout: input.vout,
                                 value: Number(input.value) },
                               JS.hex(destination), Number(amount)])
      .then(function (psbt) { return JS.fromBase64(psbt); });
  }

  function finalise(input, source, destination, amount, signatures) {
    var byKey = {};
    Object.keys(signatures).forEach(function (key) {
      byKey[key] = JS.hex(signatures[key]);
    });
    return call("finalise", [source.descriptor, source.branch, source.index,
                             { txid: input.txid, vout: input.vout,
                               value: Number(input.value) },
                             JS.hex(destination), Number(amount), byKey]);
  }

  function partialSignatures(psbtBase64) {
    return call("partial_signatures", [psbtBase64]).then(function (found) {
      var out = {};
      Object.keys(found).forEach(function (key) { out[key] = unhex(found[key]); });
      return out;
    });
  }

  function transactionOutputs(rawHex) {
    return call("transaction_outputs", [rawHex]);
  }

  // Everything else is the JavaScript's until it is ported.
  var api = Object.create(JS);
  api.buildWallet = buildWallet;
  api.deriveAddress = deriveAddress;
  api.buildPsbt = buildPsbt;
  api.finalise = finalise;
  api.partialSignatures = partialSignatures;
  api.transactionOutputs = transactionOutputs;
  api.ready = boot;
  api.loaded = function () { return loaded; };

  scope.EmbitCoordinator = api;
})(typeof self !== "undefined" ? self : this);
