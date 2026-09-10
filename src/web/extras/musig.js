/*! The MuSig2 wallet, as a feature the page may or may not carry.
 *
 * This lived inside wallet-coordinator.js, behind a MUSIG flag, next to the
 * silent-payment views behind flags of their own. That file reached 3257 lines
 * and the two builds of this page had to be kept apart by a git branch, which
 * is how the deployed one came to run firmware months older than the page
 * around it.
 *
 * So an optional feature is a file now. Nothing in the shell knows this exists:
 * it registers itself, and a build that does not want it simply does not carry
 * it. Adding the next experiment costs a file and a line, and cannot touch the
 * build that leaves it out.
 *
 * Loaded by wallet.html only on the firmware that can sign a MuSig2 spend.
 */
(function (scope) {
  "use strict";

  // Two objects, and the moved code called one of them C. The shell's C is the
  // coordinator that builds transactions and talks to the chain; the panel is
  // WalletCoordinator. Binding C to the panel here made every C.parseAccount,
  // C.musigAddress and C.spendStart in this file reach for a method that
  // object has never had, and what that looked like was a cosigner list that
  // stopped after the first one.
  var C = scope.EmbitCoordinator || scope.SignetCoordinator;
  var Panel = scope.WalletCoordinator;

  // What this feature needs from the panel. Checked here, at load, because a
  // name the shell forgot to export is undefined rather than an error: the file
  // loads, the panel draws, and it throws much later at the first line that
  // reaches for it. EXPORT_PATH was missing exactly this way, and what it
  // looked like from outside was a cosigner list that stopped after the first
  // one, fifteen minutes into a test run.
  ["element", "sats", "scanChain", "track", "Wallet", "feed",
   "EXPORT_PATH", "ACCOUNT_LINE", "ACCOUNT_URS"].forEach(function (name) {
    if (Panel[name] === undefined) {
      throw new Error("extras/musig.js needs WalletCoordinator." + name
                      + ", which wallet-coordinator.js does not export");
    }
  });

  var element = Panel.element;
  var sats = Panel.sats;
  var scanChain = Panel.scanChain;
  var track = Panel.track;
  var Wallet = Panel.Wallet;
  var feed = Panel.feed;
  var EXPORT_PATH = Panel.EXPORT_PATH;
  var ACCOUNT_LINE = Panel.ACCOUNT_LINE;
  var ACCOUNT_URS = Panel.ACCOUNT_URS;

  // The fee a spend pays, and how far past the first address to look for coins.
  // A wallet that has spent once has its money at the next index, and looking
  // at one address reported nothing and sent the visitor back to the faucet.
  var MUSIG_FEE = 1000;
  var LOOK_AHEAD = 5;

  // ------------------------------------------------------------- MuSig2
  //
  // Only reachable on the firmware that can sign it. The coordinator decides
  // everything; this asks it and renders the answer.

  // Two published BIP39 test vectors stand in for the other cosigners, so one
  // device is enough to look at a 2-of-3. Nothing here holds a key: these are
  // account xpubs and the seeds behind them are in the BIP39 test file.
  // Each cosigner is a seed exported from the device, so the wallet is one
  // anybody can spend from rather than a shape with two keys nobody holds.

  /** The way into the MuSig2 wallet, offered whatever the balance is. */
  Wallet.prototype.musigEntry = function () {
    var self = this;
    var row = element("div", "wal-actions");
    row.appendChild(this.button("MuSig2", false, function () {
      self.view = "musig";
      if (!self.musig) {
        // The key already read is cosigner one; the rest arrive the same way.
        var a = self.account;
        self.musig = { keys: ["[" + a.fingerprint + a.path + "]" + a.tpub] };
        self.watchForCosigner();
      }
      self.render();
    }));
    return row;
  };

  Wallet.prototype.renderMusig = function () {
    var self = this;
    var state = this.musig || { keys: [] };
    var have = state.keys.length;

    this.body.appendChild(element("p", "wal-step-head", "MuSig2 2 of 3"));

    // Sparrow's shape, because it is the one people recognise: the cosigners
    // listed with their fingerprints, then the policy, then the address.
    var list = element("div", "wal-cosigners");
    for (var i = 0; i < 3; i++) {
      var row = element("div", "wal-cosigner");
      row.dataset.state = i < have ? "have" : (i === have ? "next" : "wait");
      row.appendChild(element("span", "wal-cosigner-n", String(i + 1)));
      row.appendChild(element("span", "wal-cosigner-name", "Cosigner " + (i + 1)));
      // "Ready", not a fingerprint. Eight characters of hex cannot be checked
      // by eye and mean nothing to anyone who has not been told what a
      // fingerprint is; it is on hover for whoever wants it.
      var mark = element("span", "wal-cosigner-fp",
        i < have ? "Ready" : (i === have ? "Waiting for the signer" : ""));
      if (i < have) mark.title = "key fingerprint " + fingerprintOf(state.keys[i]);
      row.appendChild(mark);
      list.appendChild(row);
    }
    this.body.appendChild(list);

    if (have < 3) {
      this.body.appendChild(element("p", "wal-note", have === 0
        ? "On the signer: " + EXPORT_PATH
        : "Load the next seed on the signer and export it the same way. Every "
          + "cosigner is a different seed on its own card."));
    }

    if (!state.address) {
      // Always shown, disabled until every cosigner is in, and saying why on
      // hover. A button that appears out of nowhere leaves a visitor unsure
      // whether they are finished or stuck.
      var waiting = 3 - have;
      var actions = element("div", "wal-actions");
      var make = this.button("Create the wallet", true, function () {
        if (have === 3) self.buildMusig();
      });
      if (have < 3) {
        make.disabled = true;
        make.title = "Export " + waiting + " more cosigner"
          + (waiting === 1 ? "" : "s") + " from the device first.";
      }
      actions.appendChild(make);
      this.body.appendChild(actions);
      if (state.busy) this.body.appendChild(element("p", "wal-note", state.busy));
      return;
    }

    // What the wallet holds, before what can be done with it. The two ways of
    // handing the address out used to sit above the balance, so the first thing
    // read on a funded wallet was a pair of grey secondary buttons.
    //
    // No counters here either. How many times a signer was visited, and how
    // many nonces are in reserve, are facts about the protocol rather than
    // about anything the person reading this has to do. Both are under the "i".
    if (state.total) {
      this.body.appendChild(element("p", "wal-balance", sats(state.total)));
      var send = element("div", "wal-actions");
      // Short. The long form ran the width of a phone screen, and a primary
      // button that wraps stops looking like a button.
      send.appendChild(this.button("Spend the balance", true,
        function () { self.musigSend(); }));
      this.body.appendChild(send);
    }

    // Held back until after the result below, because these two are utilities
    // for handing the address out, and putting them between the spend button
    // and what the spend did separated an action from its outcome.
    var receiving = element("div", "wal-actions");
    if (!state.total) {
      receiving.appendChild(this.button("Get test bitcoin", true, function () {
        self.musigClaim();
      }));
    }
    receiving.appendChild(this.copier("Copy address", state.address));
    receiving.appendChild(this.button("Show as QR code", false, function () {
      self.present([state.address]);
      self.say("Open Scan on the signer and point it at this code.");
    }));

    // present() only paints; a view has to put the canvas on the page. This one
    // did not, so a spend showed its transaction to a canvas that was not in
    // the document and the device sat in Scan seeing nothing.
    if (this.presenting) {
      this.body.appendChild(element("p", "wal-say",
        "Open Scan on the signer and hold it in front of this code until the "
        + "whole transaction has been read."));
      this.body.appendChild(this.canvas);
      this.canvas.hidden = false;
    }

    if (state.sent) {
      // The amount, and nothing else. A transaction id is 64 characters that
      // nobody here has anywhere to paste: there is no explorer to open it in,
      // and the balance above already says the spend happened.
      var done = element("p", "wal-sent",
        state.sentAmount ? "Sent " + sats(state.sentAmount) : "Sent");
      done.title = "transaction " + state.sent;
      this.body.appendChild(done);
    }
    // Both ends of the address, which is how one is checked against a screen:
    // nobody reads the middle. The whole of it is on hover and on the button.
    var addr = element("p", "wal-mono",
      state.address.slice(0, 10) + "\u2026" + state.address.slice(-8));
    addr.title = state.address;
    this.body.appendChild(element("p", "wal-verify-head", "Receiving address"));
    this.body.appendChild(addr);
    this.body.appendChild(receiving);
    if (state.busy) this.body.appendChild(element("p", "wal-note", state.busy));
  };

  /** Collect the next cosigner's key off the device's screen. */
  Wallet.prototype.watchForCosigner = function () {
    var self = this;
    var collector = null;
    this.watch(function () {
      var text = self.readDevice();
      if (!text) return false;
      var trimmed = text.trim();
      // The device goes on showing the key it just exported, so without this
      // the same one is read again every tick and the panel is rebuilt each
      // time, which leaves no room for anything else to run.
      if (trimmed === self.lastExport) return false;
      if (ACCOUNT_LINE.test(trimmed)) return trimmed;
      var head = /^ur:([a-z0-9-]+)\//i.exec(trimmed);
      if (!head || ACCOUNT_URS.indexOf(head[1].toLowerCase()) === -1) return false;
      collector = collector || scope.URDecode.collector();
      if (!feed(collector, trimmed)) return false;
      if (!collector.done()) return false;
      return collector.payload();
    }, 86400000, "the next cosigner's key on the device's screen")
      .then(function (exported) {
        self.lastExport = exported;
        return Promise.resolve(C.parseAccount(exported)).then(function (account) {
          var key = "[" + account.fingerprint + account.path + "]" + account.tpub;
          // The same seed exported twice is one cosigner, not two.
          if (self.musig.keys.indexOf(key) === -1) {
            self.musig.keys.push(key);
            self.render();
          }
          if (self.musig.keys.length < 3) self.watchForCosigner();
        });
      })
      .catch(function (why) {
        // A cancelled watch is ordinary: the drawer shut, or another read
        // took over. Anything else is a key the panel could not make sense
        // of, which otherwise looks exactly like a device that never showed
        // one.
        if (/^Stopped waiting/.test(why && why.message)) return;
        self.error = "That export could not be read: " + (why && why.message);
        self.render();
      });
  };

  /** The fingerprint out of a [xxxxxxxx/path]xpub export. */
  function fingerprintOf(key) {
    var found = /^\[([0-9a-fA-F]{8})/.exec(key);
    return found ? found[1] : "";
  }

  /** What the MuSig2 address holds, asked of the chain directly.

      The panel's own refresh scans its single-signature branches; this wallet
      is one address and not on them.
   */
  Wallet.prototype.musigRefresh = function () {
    var self = this;
    if (!this.musig || !this.musig.address) return Promise.resolve();
    // The first few addresses, not only the one on screen. A wallet that has
    // spent once has its money at the next index, and looking at one address
    // reported nothing and sent the visitor back to the faucet.
    var want = [];
    for (var i = 0; i < LOOK_AHEAD; i++) want.push(i);
    return Promise.all(want.map(function (index) {
      return C.musigAddress(self.musig.descriptor, 0, index)
        .then(function (out) { return { index: index, address: out.address }; });
    })).then(function (spots) {
      return scanChain(spots.map(function (s) { return s.address; }))
        .then(function (held) {
          // scanChain hands back the map the chain answered with, keyed by
          // address. Reaching for .addresses[0] on it found nothing every
          // time, so this wallet always looked empty however much it held.
          var coins = [];
          var gone = self.musig.spent || {};
          spots.forEach(function (spot) {
            ((held[spot.address] || {}).utxos || []).forEach(function (coin) {
              // A coin this tab has already spent. The chain index keeps
              // listing it for a while after the block, and building on it
              // again produced the first transaction a second time, which the
              // network refused as "outputs already in utxo set".
              if (gone[coin.txid + ":" + coin.vout]) return;
              coin.index = spot.index;
              coins.push(coin);
            });
          });
          self.musig.coins = coins;
          self.musig.total = coins.reduce(
            function (n, c) { return n + (c.value || 0); }, 0);
          self.render();
        });
    }).catch(function (why) {
      self.error = "Could not ask the chain about that address: " + why.message;
      self.render();
    });
  };

  /** Ask the faucet to pay the MuSig2 address. */
  /** Look for the faucet's payment until it is there, or until tries run out. */
  function waitForCoin(wallet, tries) {
    return wallet.musigRefresh().then(function () {
      if (wallet.musig.total || tries <= 0) return;
      return new Promise(function (again) { setTimeout(again, 10000); })
        .then(function () { return waitForCoin(wallet, tries - 1); });
    });
  }

  Wallet.prototype.musigClaim = function () {
    var self = this;
    this.musig.busy = "Asking the faucet\u2026";
    this.render();
    return C.network.claim(this.musig.address).then(function () {
      self.musig.busy = "Paid. Waiting for a block, about thirty seconds.";
      self.render();
      // Asking once is asking too early: the faucet has paid but the block
      // holding it has not been found yet, and nothing else would ever look
      // again.
      return waitForCoin(self, 18);
    }).then(function () {
      self.musig.busy = "";
      self.render();
    }).catch(function (why) {
      self.musig.busy = "";
      self.error = "The faucet refused: " + why.message;
      self.render();
    });
  };

  /** Spend from the MuSig2 wallet, one visit per signer.
   *
   * The coordinator decides: it builds, puts a pooled nonce in if it has one,
   * checks what comes back and says when it is finished. This carries QR codes
   * between it and the device, and counts the trips.
   */
  /**
   * Spend one of the wallet's coins back into the wallet itself.
   *
   * Its own next address, derived here, rather than somewhere named in a
   * constant: an address written down is one nobody checks, and the one that
   * used to be here was not a valid address at all.
   */
  Wallet.prototype.musigSend = function () {
    var self = this;
    var coin = (this.musig.coins || [])[0];
    this.error = "";
    if (!coin) {
      this.error = "There is nothing in that wallet to spend.";
      return this.render();
    }

    var frames = scope.WalletTutorial && scope.WalletTutorial.specterFrames;
    if (!frames) {
      this.error = "wallet-tutorial.js is not on this page, so there is nothing "
                 + "here to split the transaction into codes.";
      return this.render();
    }

    this.musig.trips = 0;
    this.musig.busy = "Building the transaction\u2026";
    this.render();

    // Everything inside a promise, so a throw on the way to the first call is
    // reported like any other failure. One that escaped left the panel saying
    // it was building a transaction that was never built.
    // Ask the chain what is still unspent before choosing a coin. Spending
    // twice without looking rebuilt the first transaction exactly, down to the
    // same input and output, and the network refused it as one it already had.
    return this.musigRefresh().then(function () {
      coin = (self.musig.coins || [])[0];
      if (!coin) throw new Error("There is nothing in that wallet to spend.");
      // Remembered here and kept once the network takes the transaction, so
      // the next refresh stops offering a coin this tab has already spent.
      self.musig.spending = coin.txid + ":" + coin.vout;
      // Kept so the result can say what was sent, not just that something was.
      self.musig.sending = coin.value - MUSIG_FEE;
      // Back to the address this wallet watches, which is index 0. Paying
      // index 1 put the money somewhere the panel never looks, so the balance
      // read zero afterwards and the next spend asked the faucet instead.
      return C.musigAddress(self.musig.descriptor, 0, 0);
    }).then(function (out) {
      return C.spendStart({
        descriptor: self.musig.descriptor, branch: 0, index: coin.index || 0,
        utxo: { txid: coin.txid, vout: coin.vout, value: coin.value },
        destination: out.script_pubkey,
        amount: coin.value - MUSIG_FEE,
        pool: self.musig.pool || {},
      });
    }).then(function (state) { return self.musigVisit(state, frames); })
      .catch(function (why) {
        self.musig.busy = "";
        self.error = why.message;
        self.render();
      });
  };

  /** One visit to the device, repeated until the coordinator says it is done. */
  Wallet.prototype.musigVisit = function (state, frames) {
    var self = this;
    this.musig.trips += 1;
    this.musig.pool = state.pool;
    this.musig.issued = state.issued;
    // What is being held up, so a test can read it back and so a failed trip
    // can be looked at rather than guessed at.
    this.musig.psbt = state.psbt;
    this.musig.busy = "Trip " + this.musig.trips + ": show this to the device.";
    this.render();

    this.present(frames(state.psbt, 280));
    this.render();
    return this.watch(function () {
      return self.currentScreen() === "ScanScreen";
    }, 300000, "the device to open Scan").then(function () {
      // Hold the code up until the answer has been read, rather than taking it
      // down the moment the device shows anything that is not Scan. It shows a
      // loading screen while its camera opens, which used to end the
      // presentation before it had seen a single frame, after which it sat in
      // Scan looking at nothing. Reading the device's own screen does not need
      // this canvas, so leaving it up costs nothing.
      self.musig.busy = "Reading the device\u2019s answer\u2026";
      self.render();
      return self.readPsbt(600000);
    }).then(function (collector) {
      self.stopPresenting();
      self.canvas.hidden = true;
      self.render();
      return C.spendReturned(state, C.toBase64(collector.psbt()));
    }).then(function (next) {
      self.musig.pool = next.pool;
      self.musig.spares = (next.pool[Object.keys(next.pool)[0]] || []).length;
      if (next.verified && next.verified.length) {
        self.musig.used = (self.musig.used || 0) + next.verified.length;
      }
      if (!next.done) return self.musigVisit(next, frames);
      self.musig.busy = "Sending\u2026";
      self.render();
      return C.network.broadcast(next.txhex).then(function (sent) {
        self.musig.busy = "";
        self.musig.sent = sent.txid;
        self.musig.sentAmount = self.musig.sending || 0;
        if (self.musig.spending) {
          self.musig.spent = self.musig.spent || {};
          self.musig.spent[self.musig.spending] = true;
          self.musig.spending = null;
        }
        self.render();
        return self.musigRefresh();
      });
    });
  };

  /** Ask the coordinator for the wallet, and show what it says. */
  Wallet.prototype.buildMusig = function () {
    var self = this;
    var keys = this.musig.keys;
    this.musig.busy = "Starting the coordinator\u2026";
    this.render();
    C.musigWallet(keys).then(function (descriptor) {
      return C.musigAddress(descriptor, 0, 0).then(function (out) {
        self.musig.descriptor = descriptor;
        self.musig.address = out.address;
        self.musig.spares = 0;
        self.musig.busy = "";
        self.render();
        // The same three keys always make the same wallet, so one built again
        // may already hold coins. Ask, rather than show an empty wallet and
        // offer the faucet money it does not need.
        return self.musigRefresh();
      });
    }).catch(function (why) {
      self.musig.busy = "";
      self.error = "The coordinator could not build that wallet: " + why.message;
      self.render();
    });
  };
  Panel.defineFeature({
    view: "musig",
    render: function (wallet) { wallet.renderMusig(); },
    entry: function (wallet) { return wallet.musigEntry(); },
    // What the "i" panel says about this, contributed by the thing it
    // describes rather than written into the shell as an "if musig".
    about:
      "<h3>The policy this builds</h3><ul><li>2 of 3. The key path is"
      + " <code>musig(1,2)</code>; <code>musig(1,3)</code> and"
      + " <code>musig(2,3)</code> are fallback leaves, so any two of the three"
      + " can spend.</li></ul>"
      + "<h3>One field is ours</h3><ul>"
      + "<li><a href='https://bips.dev/174/'>BIP-174</a> reserves"
      + " <code>0xFC</code> for named private use: an identifier, a subtype,"
      + " then whatever its owner likes. Ours is <code>DOOMSIGNER</code>"
      + " subtype <code>0x01</code>, a nonce made in advance.</li>"
      + "<li>It exists because BIP-373 assumes a signer keeps its secret nonce"
      + " in memory between rounds. One that powers off cannot, so the secret"
      + " travels sealed to the card, which opens it exactly once.</li></ul>"
      + "<h3>Why a spend costs one visit</h3><ul>"
      + "<li>MuSig2's first round does not depend on the transaction, so it can"
      + " happen before there is one.</li>"
      + "<li>The device tops its spares back up to four in every transaction it"
      + " hands back, so stocking costs no extra trip.</li>"
      + "<li>Not a new idea: FROST's preprocessing stage, and Cryptnox ship a"
      + " card that makes MuSig2 nonces early.</li></ul>",
    specs: [
      ["327", "MuSig2", "Deployed"],
      ["341", "Taproot", "Deployed"],
      ["328", "Derivation on the aggregate key", "Complete"],
      ["373", "MuSig2 fields in a PSBT", "Complete"],
      ["390", "the musig() descriptor", "Draft"],
    ],
  });
}(typeof window !== "undefined" ? window : this));
