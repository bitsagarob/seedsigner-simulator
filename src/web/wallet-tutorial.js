// The multisig tutorial: a whole 2 of 3, inside the simulator.
//
// Three seeds onto three SeedKeeper cards, the three public keys back off them,
// a 2 of 3 wallet built from those keys, coins from Bitsaga Signet's faucet, and
// a spend signed by two of the three cards and confirmed on that chain. Nobody
// leaves the page and nobody installs anything.
//
// Two modes, one machine. A step is a list of actions, and an action is three
// things: a sentence saying what has to happen, the keys or clicks that make it
// happen, and how we know it did. Self driving performs the middle one; hands on
// leaves it to the visitor. Both wait for the same evidence, which is why the
// panel keeps pace either way and why there is only one description of the flow.
//
// The coordinator is on the phone: signet-coordinator.js. This file is the
// theatre around it, and the driving of the device.
//
// The QR exchange is not faked past the optics. A code the phone holds up is
// drawn from real modules by qr-encode.js onto the phone's own screen, and the
// device's camera is pointed at that screen, so the wallet's unmodified decoder
// reads real pixels. A code the device shows is read back off the device's
// canvas the same way. Nobody's webcam is involved, and the panel says so.

(function (scope) {
  "use strict";

  var C = scope.SignetCoordinator;

  // Three published BIP39 test vectors, one per card. Three separate seeds
  // rather than three paths under one, because a quorum whose keys all came
  // from the same seed is not a quorum. Nothing about them is secret and
  // nothing should ever hold value.
  var SEEDS = [
    {
      card: "A",
      words: "abandon abandon abandon abandon abandon abandon abandon abandon "
           + "abandon abandon abandon about",
      seedqr: "000000000000000000000000000000000000000000000003",
      fingerprint: "73c5da0a",
    },
    {
      card: "B",
      words: "legal winner thank year wave sausage worth useful legal winner "
           + "thank yellow",
      seedqr: "101920151790203919831533203119191019201517902040",
      fingerprint: "b8688df1",
    },
    {
      card: "C",
      words: "letter advice cage absurd amount doctor acoustic avoid letter "
           + "advice cage above",
      seedqr: "102800320257000800640514001601281028003202570004",
      fingerprint: "28645006",
    },
  ];

  // 1,000 sat over roughly 150 virtual bytes. Bitsaga Signet is not busy and
  // nothing here is bidding for space; this is simply above the relay minimum.
  var FEE = 1000n;

  var NOT_REAL = "These are not real bitcoin. They exist only on our test "
               + "network, cannot be sold or sent to anyone, and are worth nothing.";

  // How fast self driving goes.
  //
  // The wallet answers a keypress in about a fifth of a second, so left to
  // itself this whole ceremony went past in two minutes: thirteen steps, a
  // hundred and fifty odd actions, and nothing on screen long enough to read.
  // Waiting on the wallet is not pacing, so the pacing is here: before an
  // action is performed, the run waits for roughly as long as the sentence
  // describing it takes to take in, and a step's own paragraph gets longer
  // because it is the part actually worth reading.
  //
  // These are a starting point rather than a finding. The controls in the panel
  // are the real answer for anyone this does not suit: Pause stops it between
  // actions and Step takes exactly one.
  var READ_FLOOR = 1300;     // a beat, even for a three word instruction
  var READ_PER_WORD = 145;   // about 410 words a minute, read rather than scanned
  var READ_MAX = 3800;       // no single action holds the run longer than this
  var STEP_MAX = 8000;       // except the paragraph that opens a step
  // Between the keypresses of one action. Fast enough not to be a wait, slow
  // enough that a menu is seen moving one line at a time rather than jumping.
  var PRESS_GAP = 560;
  // A QR crossing between the phone and the device is the one part of this that
  // is not a keypress, and it was the one part nobody saw. The caption said what
  // was about to be held up, the code went up, and the wallet's decoder had it
  // inside a frame: on a phone, where the panel and the device cannot both be
  // stared at, that read as the seed loading itself. So a transfer is given the
  // shape of the thing it is imitating -- the code goes up after the caption has
  // been read, and stays up for a moment after the device has taken it.
  // Two seconds is the floor: a code that is up for less than that reads as a
  // flash rather than as a thing being scanned, however honest the mechanism.
  // The device usually has it inside a frame, so this is entirely for the eye.
  var HOLD_UP = 1500;        // the code is up, and the line says it is scanning
  var HOLD_AFTER = 700;      // and it stays up after the device has taken it

  // The run, in the shape somebody can hold while they watch it. Fourteen steps
  // is an inventory of what has to happen; six phases is where you are. Each
  // step names the phase it belongs to, rather than this list naming ranges of
  // step numbers, so inserting a step cannot silently move the marks.
  var PHASES = [
    "Seeds onto cards",
    "Keys off the cards",
    "Build the wallet",
    "Get test coins",
    "Sign it twice",
    "Send it",
  ];

  // Drawn in the page's own idiom: strokes that inherit the button's colour.
  var ICONS = {
    play: "M8 5l11 7-11 7z",
    pause: "M9 5v14M15 5v14",
    step: "M6 5l9 7-9 7zM18 5v14",
    back: "M18 5l-9 7 9 7zM6 5v14",
    again: "M20 12a8 8 0 1 1-2.4-5.7M20 4v4.5h-4.5",
  };

  // ---------------------------------------------------------------- the panel

  var CSS = [
    ".tut{width:min(46rem,100%);box-sizing:border-box;position:relative;",
    "background:#12151a;border:1px solid #2a2f36;border-radius:10px;",
    "padding:1.1rem 1.25rem 1.25rem;color:#b6bec8;text-align:left;overflow:hidden}",
    ".tut h2{font-size:1rem;font-weight:600;color:#d7dbe0;margin:0}",

    // The one moving thing: a hairline that grows across the top of the panel
    // as the step's own actions complete. Self driving only, because in hands
    // on there is nothing to be ahead of.
    ".tut-bar{position:absolute;inset:0 auto auto 0;height:2px;width:100%;background:transparent}",
    ".tut-bar i{display:block;height:100%;width:0;background:#f7931a;",
    "transition:width .45s ease}",

    ".tut-head{display:flex;flex-wrap:wrap;align-items:center;",
    "justify-content:space-between;gap:.5rem .9rem}",
    ".tut-controls{display:flex;flex-wrap:wrap;gap:.4rem}",
    // Icons, not words. Three controls that are pressed while something is
    // happening do not need four words between them explaining themselves, and
    // the panel they sit in is half a phone screen. The name is still there for
    // anything that reads rather than looks: aria-label and title, both.
    ".tut button{display:inline-grid;place-items:center;width:2.2rem;height:2.2rem;",
    "font:inherit;font-size:.88rem;color:#8b939e;background:#1d2026;",
    "border:1px solid #2a2e35;border-radius:6px;padding:0;cursor:pointer}",
    ".tut button svg{width:1.1rem;height:1.1rem;fill:none;stroke:currentColor;",
    "stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}",
    ".tut button.wide{width:auto;gap:.45rem;grid-auto-flow:column;padding:0 .8rem}",
    ".tut button:hover:not(:disabled){color:#d7dbe0;border-color:#3a3f47}",
    ".tut button:disabled{opacity:.4;cursor:default}",
    ".tut button.on{color:#f7931a;border-color:#f7931a;background:#16181c}",
    ".tut button.primary{color:#12151a;background:#f7931a;border-color:#f7931a;",
    "font-weight:600}",
    ".tut button.primary:hover:not(:disabled){color:#12151a;background:#ffa32e;",
    "border-color:#ffa32e}",
    ".tut button:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",
    // display:inline-grid above outranks the [hidden] the browser supplies,
    // so the two controls that are meant to be absent at rest were merely
    // labelled absent and drawn anyway.
    ".tut button[hidden]{display:none}",

    // Where you are, in six phases rather than in fourteen steps. Fourteen is
    // an inventory; six is a shape somebody can hold while they watch. The one
    // in hand is named, the rest are marks, and what is behind you stays lit so
    // the row reads as ground covered rather than as a row of lights.
    ".tut-chooser{margin:.9rem 0 0}",
    ".tut-chooser[hidden]{display:none}",
    ".tut-chooser p{margin:0 0 .7rem}",
    ".tut-choices{display:flex;flex-wrap:wrap;gap:.5rem}",
    ".tut-choice-note{margin:.7rem 0 0;font-size:.82rem;color:#7c848f}",
    ".tut-phases{display:flex;align-items:center;gap:.3rem;margin:.7rem 0 0}",
    // display:flex outranks the [hidden] the browser supplies, the same way it
    // did for the two controls that were meant to be absent at rest.
    ".tut-phases[hidden]{display:none}",
    ".tut-phases i{flex:1 1 auto;height:3px;border-radius:2px;background:#2a2f36}",
    ".tut-phases i[data-state=done]{background:#5a4423}",
    ".tut-phases i[data-state=now]{background:#f7931a}",
    ".tut-phase{margin:.45rem 0 0;font-size:.76rem;letter-spacing:.06em;",
    "text-transform:uppercase;color:#7c848f}",
    ".tut-phase:empty{display:none}",

    ".tut-step{margin:.15rem 0 0;color:#d7dbe0;font-weight:600;font-size:1.05rem}",
    ".tut-do{margin:.6rem 0 0;padding:.5rem .7rem;border-left:2px solid #f7931a;",
    "background:#16181c;color:#d7dbe0}",
    ".tut-do:empty{display:none}",
    ".tut-verdict{margin:.8rem 0 0;border:1px solid #2a2f36;border-radius:6px;",
    "padding:.5rem .7rem;overflow-wrap:anywhere}",
    ".tut-verdict:empty{display:none}",
    ".tut-verdict[data-state=good]{color:#f7931a;border-color:#f7931a;background:#16181c}",
    ".tut-verdict[data-state=bad]{color:#ef4444;border-color:#7f1d1d;background:#1b0f10}",

    // The code, and nothing pretending to hold it. This was a drawn phone, with
    // a notch and a shadow and the word COORDINATOR under it, which is a mockup
    // of a device nobody has: the coordinator here is a web page, and the only
    // real thing in that picture was the code itself. So the code is the
    // picture. White tile, because that is what a camera needs to read one.
    ".tut-swap{display:flex;flex-wrap:wrap;gap:1rem;margin:1rem 0 0;align-items:flex-start}",
    ".tut[data-state=idle] .tut-swap{display:none}",
    ".tut-phone{width:9.5rem;flex:none}",
    ".tut-phone-screen{position:relative;background:#16181c;border:1px solid #2a2f36;",
    "border-radius:8px;overflow:hidden;min-height:6.4rem}",
    ".tut-phone-canvas{display:block;width:100%;background:#fff}",
    ".tut-phone-canvas[hidden]{display:none}",
    ".tut-phone-face{padding:.5rem .55rem;font-size:.74rem;line-height:1.45;color:#8b939e}",
    ".tut-phone-face b{display:block;color:#d7dbe0;font-weight:600;font-size:.8rem}",
    ".tut-phone-face span{display:block;overflow-wrap:anywhere;margin-top:.3rem}",
    ".tut-summary{display:grid;grid-template-columns:max-content 1fr;gap:.2rem .6rem;",
    "margin:.45rem 0 0;font-size:.72rem}",
    ".tut-summary dt{color:#7c848f}",
    ".tut-summary dd{margin:0;color:#d7dbe0;overflow-wrap:anywhere;",
    "font-family:ui-monospace,SFMono-Regular,Menlo,monospace}",

    ".tut-flow{flex:1 1 12rem;min-width:0}",
    ".tut-arrow{color:#f7931a;font-weight:600;font-size:.82rem;letter-spacing:.04em}",
    ".tut-arrow:empty{display:none}",
    ".tut-caption{margin:.25rem 0 0;font-size:.88rem}",
    ".tut-caption:empty{display:none}",

    // The details are for the one visitor in fifty who wants the hex. A button
    // with a sentence on it asks the other forty-nine to decide about it every
    // time they look at the panel; a small circled i does not.
    ".tut-fold{margin:1rem 0 0}",
    // Big enough for a thumb: it is one of two controls on a line that a phone
    // shows above the device, and a 24px circle beside a 44px button is a
    // target you aim at rather than press.
    ".tut-fold>summary{display:grid;place-items:center;width:2.6rem;height:2.6rem;",
    "list-style:none;cursor:pointer;font:italic 600 1rem/1 serif;",
    "color:#767d87;background:none;border:1px solid #3a4048;border-radius:50%}",
    ".tut-fold>summary::-webkit-details-marker{display:none}",
    ".tut-fold>summary:hover{color:#d7dbe0;border-color:#3a3f47}",
    ".tut-fold[open]>summary{color:#f7931a;border-color:#f7931a}",
    ".tut-fold>summary:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",
    ".tut-fold dl{display:grid;grid-template-columns:max-content 1fr;gap:.3rem .9rem;",
    "margin:.7rem 0 0;font-size:.85rem}",
    // Given a home beside the warning, at the top of the page, it cannot open in
    // place: what it opens is a list of keys and hashes, and pushing the device
    // down the screen to show them is not a thing an i is allowed to do. So it
    // hangs off the icon instead, which is what the page's other two i's do.
    ".tut-fold.floating{position:relative;margin:0}",
    // A page over the page, not a panel tucked under a corner of it.
    //
    // It was a floating box at z-index 12, which is a number that means nothing
    // on its own: the device's own artwork painted over the top two hundred
    // pixels of it, so what opened was the bottom half of a list with its
    // heading behind a smartcard. Everything about that is a negotiation with
    // whatever else the page happens to be drawing, and this content is a wall
    // of seeds, payloads and hashes that wants the whole screen anyway.
    //
    // So it takes the whole screen and stops negotiating. The one thing left
    // above it is the icon that opened it, because that is what closes it and a
    // control you cannot reach is a modal with no way out.
    ".tut-fold.floating>summary{position:relative;z-index:91}",
    // On a wide screen, a popover under the icon that opened it. The whole
    // window is the right answer on a phone and the wrong one here: opened
    // early, when this holds one sentence, taking the entire screen for it
    // reads as a page that has died rather than as a panel that has opened.
    // The objection that sent it full screen was the device's artwork painting
    // over a small box, and that was while it sat down beside the device; up in
    // the warning row there is nothing above the device to collide with.
    ".tut-fold.floating>.tut-foldbody{position:absolute;z-index:95;left:50%;",
    "transform:translateX(-50%);top:3.4rem;width:min(34rem,90vw);",
    "box-sizing:border-box;max-height:min(70vh,32rem);overflow-y:auto;",
    "background:#0b0c0e;border:1px solid #2a2f36;border-radius:10px;",
    "padding:.9rem 1rem;box-shadow:0 1rem 2rem rgba(0,0,0,.6)}",
    ".tut-fold.floating>.tut-foldbody dl{margin:0}",
    ".tut-fold.floating>.tut-foldbody>button{margin:.9rem auto 0;display:flex}",
    ".tut-fold dt{color:#7c848f}",
    ".tut-fold dd{margin:0;overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}",
    ".tut-fold dd.plain{font-family:inherit}",

    // A link, not a button. It was an orange box the same size and colour as the
    // one that opens the wallet, sitting an inch above it, and two of those say
    // "two equal ways in" when only one of them is what the page is for. This is
    // a guided demo of one ceremony, offered to somebody handed a link to it, so
    // it takes the shape everything else optional on the web takes. Centred as a
    // block of its own width, because the slot it goes in is the full width of
    // the column and text-align on the slot would follow the tutorial panel in
    // here later.
    ".tut-start{display:block;margin:.2rem auto;width:fit-content;font:inherit;",
    "font-size:.9rem;color:#f7931a;background:none;border:0;padding:.2rem;",
    "cursor:pointer;text-decoration:underline;text-underline-offset:3px}",
    ".tut-start:hover{color:#ffa32e}",
    ".tut-start:focus-visible{outline:2px solid #f7931a;outline-offset:2px;border-radius:4px}",

    // A page over the page, not a panel tucked under a corner of it, once there
    // is no room for a panel. It was a floating box at z-index 12, which is a
    // number that means nothing on its own: the device's own artwork painted
    // over the top two hundred pixels of it, so what opened was the bottom half
    // of a list with its heading behind a smartcard. Everything about that is a
    // negotiation with whatever else the page happens to be drawing, and by the
    // end of a run this content is a wall of seeds, payloads and hashes that
    // wants the whole screen anyway. So on a narrow one it takes the whole
    // screen and stops negotiating. The one thing left above it is the icon
    // that opened it, because that is what closes it and a control you cannot
    // reach is a modal with no way out.
    "@media (max-width:61.99rem){",
    // The same band, for the same reason: this i stays put while what it opened
    // scrolls, and without something opaque under it, it ends up sitting in the
    // middle of whatever line happens to be passing.
    ".tut-fold.floating[open]::before{content:\"\";position:fixed;left:0;right:0;",
    "top:0;z-index:90;height:var(--tut-top,4.4rem);background:#0b0c0e}",
    ".tut-fold.floating>.tut-foldbody{position:fixed;inset:0;z-index:89;",
    "transform:none;width:auto;max-height:none;border:none;border-radius:0;",
    "box-shadow:none;box-sizing:border-box;overflow-y:auto;background:#0b0c0e;",
    "padding:var(--tut-top,4.4rem) 1rem 2rem;-webkit-overflow-scrolling:touch}",
    ".tut-fold.floating>.tut-foldbody dl{margin:0;max-width:46rem;",
    "margin-inline:auto}",
    ".tut-fold.floating>.tut-foldbody>button{margin:1rem auto 0;display:flex}",
    "}",

    "@media (max-width:30rem){",
    ".tut{padding:.9rem .8rem 1rem}",
    ".tut-fold dl{grid-template-columns:1fr;gap:0}",
    ".tut-fold dd{margin:0 0 .45rem}",
    ".tut-phone{width:100%}",
    "}",

    // On a phone held upright the panel is half a screen, not a page, and it is
    // sharing that screen with the device it is driving. What has to survive is
    // the line saying what is happening now and the phone holding the code up;
    // the paragraph explaining the step is the first thing to go, because it is
    // the same four lines for the whole of a step the visitor is watching
    // happen. It is not deleted, it is not shown here: the runner asks whether
    // it is on the page before deciding how long to leave it up.
    // The phone goes back to being a phone beside the words rather than a
    // full-width one above them, because 100% of this column is most of the
    // panel and the code is only ever read by a camera that is not real.
    "@media (max-width:61.99rem) and (orientation:portrait){",
    ".tut{padding:.7rem .75rem .8rem}",
    ".tut-say{display:none}",
    ".tut-step{margin:.5rem 0 0}",
    // Standing text goes; live text stays. The webcam sentence is true and is
    // worth saying once on a screen with room for it, but it does not change
    // for the whole eight minutes, and here it is competing with the caption
    // that changes every few seconds.
    ".tut-swap{margin:.7rem 0 0;gap:.7rem;flex-wrap:nowrap}",
    ".tut-phone{width:8.5rem}",
    // No floor on a phone: with nothing to hold up, the tile was a hundred
    // pixels of empty box on the screen with the least to spare.
    ".tut-phone-screen{min-height:0}",
    ".tut-do{margin:.45rem 0 0}",
    "}",
  ].join("");

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------- the machine

  function Tutorial(options) {
    this.sendKey = options.sendKey;
    this.keymap = options.keymap;
    this.tray = options.tray;
    this.screen = options.screen;
    this.lines = [];
    this.cursor = 0;
    this.mode = "idle";
    this.paused = true;
    this.stepOnce = false;
    this.generation = 0;
    this.details = [];
    this.build(options.container, options);
  }

  Tutorial.prototype.build = function (container, options) {
    var style = element("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var root = element("section", "tut");
    root.id = "tutorial";
    this.root = root;

    this.bar = element("div", "tut-bar");
    this.barFill = element("i");
    this.bar.appendChild(this.barFill);

    var head = element("div", "tut-head");
    // The heading is for the panel at rest. Once a run is under way the phase
    // row and the step title say what it says and where you are as well, so it
    // goes: two titles above three words of instruction is the panel talking
    // about itself.
    // What it is, in the words somebody searching for it would use. "A 2 of 3 on
    // Bitsaga Signet" names the quorum and the network, which are the two things
    // a visitor does not know yet.
    this.heading = element("h2", null, "Multi-sig demo");
    head.appendChild(this.heading);
    this.controls = element("div", "tut-controls");
    head.appendChild(this.controls);

    this.playButton = this.control("Play", this.togglePlay.bind(this), ICONS.play);
    this.backButton = this.control("Back", this.stepBack.bind(this), ICONS.back);
    this.stepButton = this.control("One step", this.stepOn.bind(this), ICONS.step);
    this.againButton = this.control("Begin again", this.restart.bind(this), ICONS.again);

    // Where the run is, in phases. Marks rather than numbers: it is answering
    // "how far in am I", which is a length, not a count.
    this.phaseRow = element("div", "tut-phases");
    this.phaseMarks = PHASES.map(function () {
      var mark = element("i");
      this.phaseRow.appendChild(mark);
      return mark;
    }, this);
    this.phaseText = element("p", "tut-phase");

    // What the run needs before it can start, asked before the browser asks.
    // The device makes each seed from a photograph, so something has to be in
    // front of its camera; that is either the visitor's own camera or this
    // browser's randomness, and finding out which by triggering a permission
    // dialog nobody was expecting is the wrong way round.
    this.chooser = element("div", "tut-chooser");
    this.chooser.hidden = true;
    this.chooser.appendChild(element("p", null,
      "Each seed is made from a photograph, the way the device does it. What "
      + "should it photograph?"));
    var choices = element("div", "tut-choices");
    // Neither is the suggested answer: one is your camera and one is not, and
    // the page has no business preferring the one that turns your camera on.
    this.cameraChoice = element("button", "wide", "My camera");
    this.cameraChoice.type = "button";
    this.noiseChoice = element("button", "wide", "Random noise instead");
    this.noiseChoice.type = "button";
    choices.appendChild(this.cameraChoice);
    choices.appendChild(this.noiseChoice);
    this.chooser.appendChild(choices);
    this.chooser.appendChild(element("p", "tut-choice-note",
      "Your browser will ask for the camera. Nothing is recorded or sent: the "
      + "picture is taken by the device on this page and thrown away once the "
      + "seed is made."));
    var self1 = this;
    this.cameraChoice.addEventListener("click", function () { self1.choose(true); });
    this.noiseChoice.addEventListener("click", function () { self1.choose(false); });

    this.stepText = element("p", "tut-step");
    this.doText = element("p", "tut-do");
    this.verdict = element("p", "tut-verdict");

    var swap = element("div", "tut-swap");
    var phone = element("div", "tut-phone");
    var phoneScreen = element("div", "tut-phone-screen");
    this.canvas = element("canvas", "tut-phone-canvas");
    this.canvas.width = 640;
    this.canvas.height = 480;
    this.canvas.hidden = true;
    this.face = element("div", "tut-phone-face");
    phoneScreen.appendChild(this.canvas);
    phoneScreen.appendChild(this.face);
    phone.appendChild(phoneScreen);

    var flow = element("div", "tut-flow");
    this.arrow = element("p", "tut-arrow");
    this.caption = element("p", "tut-caption");
    flow.appendChild(this.arrow);
    flow.appendChild(this.caption);
    swap.appendChild(phone);
    swap.appendChild(flow);

    this.fold = element("details", "tut-fold");
    var summary = element("summary", null, "i");
    summary.title = "The keys, the codes and the transaction behind this run";
    summary.setAttribute("aria-label", summary.title);
    this.fold.appendChild(summary);
    // Everything the fold opens, in one box. It used to be two siblings of the
    // summary, which is fine while it opens in place and impossible once it
    // opens as a panel hanging off an icon somewhere else on the page.
    this.foldBody = element("div", "tut-foldbody");
    this.detailList = element("dl");
    this.foldBody.appendChild(this.detailList);
    this.fold.appendChild(this.foldBody);
    // Where the icon ends is where the text starts and where the band stops.
    // Measured on opening rather than guessed once: the row it sits in is laid
    // out by the page, and a warning that wraps moves it.
    var fold = this.fold;
    fold.addEventListener("toggle", function () {
      if (!fold.open) return;
      var icon = fold.querySelector("summary").getBoundingClientRect();
      fold.style.setProperty("--tut-top", Math.round(icon.bottom + 20) + "px");
    });
    // Hands on is not on the control row any more: three controls are what a
    // panel this size can hold. It is not gone, because pausing is not the same
    // as taking over -- a run resumed after somebody pressed the buttons
    // themselves performs the action again -- so it lives here, with the rest
    // of what only some visitors want.
    this.handsButton = element("button", "wide", "I will drive");
    this.handsButton.type = "button";
    var hands = this.handsButton;
    var self0 = this;
    hands.addEventListener("click", function () {
      // And the page of detail closes with it. Taking the buttons is a request
      // to look at the device, and this control lives behind an overlay that
      // covers the device.
      self0.fold.open = false;
      self0.toggleHands();
      hands.blur();
    });
    this.foldBody.appendChild(this.handsButton);

    // A way out that says so. The only one before this was the icon that opened
    // it, unlabelled and at the top of the window, which is fine when you know
    // and is a page with nothing on it when you do not.
    var close = element("button", "wide", "Close");
    close.type = "button";
    var self2 = this;
    close.addEventListener("click", function () { self2.fold.open = false; });
    this.foldBody.appendChild(close);

    root.appendChild(this.bar);
    root.appendChild(head);
    root.appendChild(this.chooser);

    // The step and the instruction sit wherever the page says they belong. Given
    // a slot under the device, that is where they go: they change every few
    // seconds and are read against the screen above them, and a panel on the
    // other half of a phone makes that a six minute ping-pong. Without a slot
    // they stay in the panel, which is what a page that has made no room does.
    var say = options.sayInto || root;
    say.appendChild(this.phaseRow);
    say.appendChild(this.phaseText);
    say.appendChild(this.stepText);
    say.appendChild(this.doText);
    root.appendChild(this.verdict);
    root.appendChild(swap);
    root.appendChild(this.fold);
    container.appendChild(root);

    // No seed words here. The three seeds are made on the device from a
    // photograph and are different on every run, so what this panel can honestly
    // show is what came back off each card, which the run adds as it reads them.
    this.detail("the photograph", "Each seed is made from one, taken by the "
      + "device. Your camera is asked for once, when Play is pressed; refused or "
      + "unavailable, the picture is noise from this browser's own random "
      + "source. The device refuses either if it is not random enough.", true);

    this.painter = this.canvas.getContext("2d", { willReadFrequently: true });
    this.clearPhone();
    this.summary();
    this.introduce();
    this.reflect();
    // A frame the capture stream can always find something new in, so the
    // device's camera never sits on a stale picture.
    var self = this;
    setInterval(function () {
      self.heartbeat = (self.heartbeat || 0) ^ 1;
      self.painter.fillStyle = self.heartbeat ? "#fefefe" : "#ffffff";
      self.painter.fillRect(0, 0, 2, 2);
    }, 60);
  };

  // Counted if wallet-track.js is on the page, ignored if it is not. The panel
  // is the one thing here somebody is either driving or watching, so which of
  // its buttons get pressed, and how far a run gets, is the whole question.
  function track(action, name) {
    if (scope.Track) scope.Track.event("tutorial", action, name);
  }

  function iconInto(button, path, label) {
    button.textContent = "";
    button.insertAdjacentHTML("afterbegin",
      '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="'
      + path + '"></path></svg>');
    button.title = label;
    button.setAttribute("aria-label", label);
  }

  // With an icon for the three that are pressed while something is happening,
  // and in words for anything that only ever appears once something has gone
  // wrong: Try again is not a control somebody is scanning a row for, it is a
  // sentence the panel is offering, and an unlabelled glyph under a red verdict
  // is a guess.
  Tutorial.prototype.control = function (label, handler, icon) {
    var button = element("button", icon ? null : "wide", icon ? "" : label);
    button.type = "button";
    if (icon) iconInto(button, icon, label);
    button.addEventListener("click", function () {
      handler();
      button.blur();
    });
    this.controls.appendChild(button);
    return button;
  };

  /** Which phase the run is in, and how much of the row is behind it. */
  Tutorial.prototype.setPhase = function (name) {
    var at = PHASES.indexOf(name);
    this.phaseText.textContent = name || "";
    this.phaseMarks.forEach(function (mark, i) {
      mark.dataset.state = at < 0 ? "" : (i < at ? "done" : (i === at ? "now" : ""));
    });
  };

  Tutorial.prototype.introduce = function () {
    // No step title yet: the panel's own heading already says what this is,
    // and repeating it under itself was the first line of the thing that had
    // too many lines.
    // Nothing at rest. The panel's heading says what this is and the button says
    // what to do about it; a sentence between them describing what is about to
    // happen is the page reading the demo out before playing it.
    this.stepText.textContent = "";
    this.doText.textContent = "";
    // Six because that is what it measures, on the deployed site, against the
    // real chain: 362 seconds on 2026-08-07. It was eight before the
    // instructions were cut, and the pacing is derived from their length.
  };

  // ------------------------------------------------------------ the log oracle

  Tutorial.prototype.log = function (message) {
    this.lines.push(message);
  };

  Tutorial.prototype.currentScreen = function () {
    for (var i = this.lines.length - 1; i >= 0; i--) {
      var found = /display\(\) enter: (\w+)/.exec(this.lines[i]);
      if (found) return found[1];
    }
    return null;
  };

  /** Wait for a line matching, from the cursor onwards, and move the cursor. */
  Tutorial.prototype.until = function (pattern, timeout) {
    var self = this;
    var matcher = new RegExp(pattern);
    return this.poll(timeout, function () {
      for (var i = self.cursor; i < self.lines.length; i++) {
        if (matcher.test(self.lines[i])) {
          self.cursor = i + 1;
          return true;
        }
      }
      return false;
    }, pattern);
  };

  /** Poll a predicate until it is true, or give up and say what we wanted. */
  Tutorial.prototype.poll = function (timeout, test, what) {
    var self = this;
    var generation = this.generation;
    var deadline = Date.now() + (this.mode === "hands" ? Math.max(timeout, 900000) : timeout);
    return new Promise(function (resolve, reject) {
      (function tick() {
        if (generation !== self.generation) return;         // restarted underneath us
        var value;
        try {
          value = test();
        } catch (error) {
          return reject(error);
        }
        if (value) return resolve(value);
        if (Date.now() > deadline) {
          return reject(new Error("nothing happened while waiting for " + what));
        }
        setTimeout(tick, 150);
      })();
    });
  };

  Tutorial.prototype.sleep = function (ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  };

  /**
   * Leave time to read what has just gone up, before anything else moves.
   *
   * Self driving only: in hands on the visitor sets the pace by pressing the
   * buttons, and waiting on top of that would only be in the way.
   */
  Tutorial.prototype.pace = function (text, ceiling) {
    if (this.mode !== "self" || !text) return Promise.resolve();
    var words = String(text).trim().split(/\s+/).length;
    var wait = Math.min(ceiling || READ_MAX, READ_FLOOR + words * READ_PER_WORD);
    // Interruptible, which is the whole of why Pause looked like it was one
    // step behind: this is the longest thing between two actions, and a sleep
    // that cannot be cut short means the press it was counting down to happens
    // anyway. Now the beat ends the moment Pause is pressed, and the gate below
    // catches the run before anything moves.
    var self = this;
    var started = Date.now();
    return new Promise(function (resolve) {
      (function tick() {
        if (self.paused || Date.now() - started >= wait) return resolve();
        setTimeout(tick, 60);
      })();
    });
  };

  /**
   * A fixed pause, for the parts of a transfer that are not text being read.
   * Self driving only, for the same reason pace is.
   */
  Tutorial.prototype.beat = function (ms) {
    if (this.mode !== "self") return Promise.resolve();
    return this.sleep(ms);
  };

  /**
   * Is this element on the page at all? A phone hides the step's paragraph to
   * keep the device and the panel on one screen, and waiting eight seconds for
   * a paragraph nobody is being shown is dead air rather than pacing.
   */
  Tutorial.prototype.shown = function (node) {
    return !!(node && node.offsetParent !== null);
  };

  // ------------------------------------------------------------ the device

  Tutorial.prototype.press = function (names, gap) {
    var self = this;
    return names.reduce(function (chain, name) {
      return chain.then(function () {
        self.sendKey(self.keymap[name]);
        return self.sleep(gap || PRESS_GAP);
      });
    }, Promise.resolve());
  };

  // ------------------------------------------------------------ the phone

  /**
   * The camera, asked for once, at the moment Play is pressed.
   *
   * The device makes its seeds from a photograph, as it does on hardware, and
   * the camera it reads during this run is the coordinator's own canvas. So the
   * real webcam is painted into that canvas for the shot rather than swapped in
   * as a second stream: the device keeps the one stream it opened, the QR
   * machinery is untouched, and there is no second permission prompt in the
   * middle of a run.
   *
   * Asked for inside the Play click, because that is the user gesture Safari
   * requires. Refused, missing, or served over plain http, it resolves to
   * nothing and the shot falls back to noise from crypto.getRandomValues, which
   * the panel then says out loud.
   */
  Tutorial.prototype.askForCamera = function () {
    var self = this;
    if (this.video || !scope.navigator || !navigator.mediaDevices
        || !navigator.mediaDevices.getUserMedia) return Promise.resolve(null);
    return navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false,
    }).then(function (stream) {
      var video = element("video");
      video.playsInline = true;
      video.muted = true;
      video.srcObject = stream;
      return video.play().then(function () {
        self.video = video;
        self.stream = stream;
        return stream;
      });
    }).catch(function () { return null; });
  };

  /** Give the camera back, so no light is left on after the run. */
  Tutorial.prototype.releaseCamera = function () {
    if (this.stream) this.stream.getTracks().forEach(function (t) { t.stop(); });
    this.stream = null;
    this.video = null;
  };

  /**
   * What the device's camera sees while it is taking a picture for entropy.
   *
   * The firmware checks this: a flat or nearly flat image is refused with "Poor
   * Entropy", which is worth knowing, because it means the fallback cannot be a
   * grey rectangle. It is noise from the browser's cryptographic source, and it
   * passes for the same reason a photograph of a room does.
   */
  Tutorial.prototype.showEntropy = function () {
    var self = this;
    if (this.entropyOn) return;          // one painter, however many ask for it
    this.entropyOn = true;
    (function paint() {
      if (!self.entropyOn) return;
      if (self.video && self.video.readyState >= 2) {
        self.painter.drawImage(self.video, 0, 0, 640, 480);
      } else {
        var frame = self.painter.createImageData(640, 480);
        var data = frame.data;
        for (var at = 0; at < data.length; at += 65536) {
          crypto.getRandomValues(data.subarray(at, Math.min(at + 65536, data.length)));
        }
        for (var i = 3; i < data.length; i += 4) data[i] = 255;
        self.painter.putImageData(frame, 0, 0);
      }
      self.face.hidden = true;
      self.canvas.hidden = false;
      setTimeout(paint, 140);
    })();
  };

  Tutorial.prototype.stopEntropy = function () {
    this.entropyOn = false;
  };

  /**
   * Paint while the device is taking a picture, whoever asked for it.
   *
   * This used to be started by the until of the action that presses into the
   * live preview, and stopped by the until of the next one. That ties the
   * painting to where the *run* thinks it is, and going back drives the two
   * apart: Back re-enters the step at a point the device can be at, the device
   * is already on the live preview, and the until that would have started the
   * painting has already resolved. So nothing painted, the device scored the
   * same frozen frame for ever at -0.00, drew its overlay over a picture that
   * never changed until the words piled up on themselves, and there was no way
   * forward: the firmware will not accept a picture with no entropy in it.
   *
   * Driven by the screen the device is actually on, the two cannot drift. The
   * calls in the run stay: they start the paint a beat sooner than this loop
   * would notice, and showEntropy refuses to start twice.
   */
  Tutorial.prototype.watchEntropy = function () {
    var self = this;
    if (this.entropyWatching) return;
    this.entropyWatching = true;
    (function look() {
      if (self.mode === "idle") { self.entropyWatching = false; return; }
      var live = self.currentScreen() === "ToolsImageEntropyLivePreviewScreen";
      if (live) self.showEntropy(); else self.stopEntropy();
      setTimeout(look, 200);
    })();
  };

  Tutorial.prototype.clearPhone = function () {
    this.painter.fillStyle = "#ffffff";
    this.painter.fillRect(0, 0, 640, 480);
  };

  /** Long values, ends kept: the middle of a txid tells nobody anything. */
  function ends(value) {
    var text = String(value || "");
    return text.length > 22 ? text.slice(0, 10) + "\u2026" + text.slice(-8) : text;
  }

  /**
   * What the coordinator is holding, in the shape a wallet shows it.
   *
   * The tile said "Coordinator" over an empty box for the first three minutes,
   * which is a label for a thing with nothing in it. This is the same box
   * saying what a wallet would say: how many keys it has, where it receives,
   * what it holds, what it spent. Empty is a real answer early on, and it says
   * that too, rather than saying nothing.
   */
  Tutorial.prototype.summary = function () {
    var state = this.state || {};
    var keys = (state.keys || []).filter(Boolean).length;
    var rows = [["Keys", keys + " of 3"]];
    if (state.receive) rows.push(["Receiving at", ends(state.receive.address)]);
    if (state.funding) rows.push(["Funded by", ends(state.funding)]);
    if (state.spend && state.spend.txid) rows.push(["Spent", ends(state.spend.txid)]);
    else if (state.wallet) rows.push(["Quorum", "2 of 3"]);

    this.face.textContent = "";
    this.face.appendChild(element("b", null, "Demo wallet"));
    var list = element("dl", "tut-summary");
    rows.forEach(function (row) {
      list.appendChild(element("dt", null, row[0]));
      list.appendChild(element("dd", null, row[1]));
    });
    this.face.appendChild(list);
    this.face.hidden = false;
    this.canvas.hidden = true;
    this.clearPhone();
  };

  /**
   * A word and a line about what the wallet half is doing.
   *
   * Nothing to say means nothing shown. The tile used to be handed a title and
   * an empty string, and drew the box anyway, so a visitor's first sight of this
   * column was one word over blank space -- which reads as something that failed
   * to load rather than as something waiting. An empty span is dropped for the
   * same reason: there is no such thing as a line with nothing on it.
   */
  Tutorial.prototype.showFace = function (title, text) {
    if (!title && !text) return this.hideFace();
    this.face.textContent = "";
    if (title) this.face.appendChild(element("b", null, title));
    if (text) this.face.appendChild(element("span", null, text));
    this.face.hidden = false;
    this.canvas.hidden = true;
    this.clearPhone();
  };

  Tutorial.prototype.hideFace = function () {
    this.face.textContent = "";
    this.face.hidden = true;
  };

  Tutorial.prototype.paintMatrix = function (matrix) {
    var modules = matrix.length;
    var scale = Math.floor(Math.min(640, 480) * 3 / 4 / modules);
    var size = modules * scale;
    var left = Math.floor((640 - size) / 2), top = Math.floor((480 - size) / 2);
    this.painter.fillStyle = "#ffffff";
    this.painter.fillRect(0, 0, 640, 480);
    this.painter.fillStyle = "#000000";
    for (var r = 0; r < modules; r++) {
      for (var c = 0; c < modules; c++) {
        if (matrix[r][c]) this.painter.fillRect(left + c * scale, top + r * scale, scale, scale);
      }
    }
    this.face.hidden = true;
    this.canvas.hidden = false;
  };

  /** What the phone's own camera would see: the device's screen. */
  Tutorial.prototype.mirrorDevice = function () {
    this.painter.fillStyle = "#0b0c0e";
    this.painter.fillRect(0, 0, 640, 480);
    // The device draws a QR into the left 240 by 240 of its 320 by 240 screen.
    this.painter.drawImage(this.screen, 0, 0, 240, 240, 80, 0, 480, 480);
    this.painter.strokeStyle = "#f7931a";
    this.painter.lineWidth = 6;
    this.painter.strokeRect(60, 20, 520, 440);
    this.face.hidden = true;
    this.canvas.hidden = false;
  };

  Tutorial.prototype.transfer = function (direction, caption) {
    this.arrow.textContent = direction === "in"
      ? "Phone  →  device" : "Device  →  phone";
    this.caption.textContent = caption;
  };

  /**
   * The line above the caption, while a code is actually up and being read.
   *
   * Said in words because the mechanism is invisible: a camera pointed at a
   * screen on the same page decodes in a frame, so without this the only
   * evidence that a scan happened is that the device moved on.
   */
  Tutorial.prototype.scanning = function (direction) {
    this.arrow.textContent = direction === "in"
      ? "Scanning the QR  ·  Phone  →  device"
      : "Scanning the QR  ·  Device  →  phone";
  };

  Tutorial.prototype.endTransfer = function () {
    this.arrow.textContent = "";
    this.caption.textContent = "";
    this.summary();
  };

  /** Read whatever QR is on the device's screen, with the page's own jsQR. */
  Tutorial.prototype.readDevice = function () {
    if (!scope.jsQR) return null;
    var context = this.screen.getContext("2d");
    var image = context.getImageData(0, 0, 240, 240);
    var found = scope.jsQR(image.data, image.width, image.height);
    return found && found.data ? found.data : null;
  };

  // ------------------------------------------------------- details, on demand

  Tutorial.prototype.detail = function (label, value, plain) {
    this.detailList.appendChild(element("dt", null, label));
    var dd = element("dd", plain ? "plain" : null, value);
    this.detailList.appendChild(dd);
  };

  // Shown while it is driving, paused or not: pausing does not undo the actions
  // that have already happened, so a line that emptied itself when the run
  // stopped would be saying something untrue. It stays where the last piece of
  // evidence left it, which is also what it does during the wait before an
  // action, because nothing has happened yet.
  Tutorial.prototype.setProgress = function (fraction) {
    this.barFill.style.width = this.mode === "self"
      ? Math.round(fraction * 100) + "%" : "0";
  };

  /**
   * How far through the action in hand, where the action itself knows.
   *
   * Only ever called with something real: the codes of an animated QR that have
   * actually been read, or how far the chain is towards its next block. An
   * action with nothing measurable inside it simply does not call this, and the
   * line waits at the last thing that really happened.
   */
  Tutorial.prototype.subProgress = function (fraction) {
    this.setProgress(this.fraction + fraction / (this.stepSize || 1));
  };

  // ------------------------------------------------------------ the controls

  Tutorial.prototype.togglePlay = function () {
    if (this.mode === "idle") {
      // Asked every time, and not remembered anywhere. Turning a camera on is
      // not a preference to be inferred from something somebody clicked once.
      track("play", "asked");
      this.pending = "self";
      this.chooser.hidden = false;
      return;
    }
    // Taking over is implicit: stepping, pausing and going back are all ways of
    // saying you will drive. Handing it back has to be one press, and this is
    // it, from wherever the run got to. The action the visitor was in the middle
    // of is done for them on the way, because the loop is waiting on that one
    // and would otherwise sit there while the run looked resumed.
    if (this.mode === "hands") {
      track("play", "let it drive");
      this.mode = "self";
      this.paused = false;
      this.stepOnce = false;
      this.reflect();
      return this.doItForThem();
    }
    // Before the flip, so the name is what was asked for rather than what the
    // button now says.
    track("play", this.paused ? "resume" : "pause");
    this.paused = !this.paused;
    this.stepOnce = false;               // Play means keep going, not one more
    this.reflect();
  };

  /**
   * One action, then stop again.
   *
   * The whole of the next thing the panel describes happens -- the keys are
   * pressed and the evidence for them is waited for -- and then the run pauses
   * itself. Nothing is ever left half pressed, because the pausing is done
   * between actions and only there, which is the same place the Play button
   * takes effect.
   */
  /**
   * The action the run is sitting on, done from here rather than by the loop.
   *
   * While the visitor is driving, the loop skips perform and waits on until, so
   * there is nothing to unpause: it is already waiting, and it will wait all
   * day. Performing the action from outside is enough, because the wait it is
   * already in is what notices the action happened. The loop learns nothing new
   * and needs no flag: this is the same perform the self driving run calls, and
   * the same until proves it worked.
   */
  Tutorial.prototype.doItForThem = function () {
    var step = this.steps && this.steps[this.at];
    var action = step && step.actions && step.actions[this.atAction];
    if (!action || !action.perform) return;
    action.perform({ tutorial: this, state: this.state || (this.state = {}) });
  };

  Tutorial.prototype.stepOn = function () {
    track("step", this.mode);
    // One step means one step whoever is driving. While the visitor is driving
    // it means "do this one for me", which is the thing somebody stuck on a
    // step actually wants, and it was the one moment this control was dead.
    //
    // No stepOnce here, deliberately. That flag pauses the run once the action
    // lands, which is right when the run was moving by itself and wrong here:
    // a hands on run already stops at every action, so pausing it again stops
    // the loop before it writes the next instruction, and the press looks like
    // it did nothing.
    if (this.mode === "hands") return this.doItForThem();
    this.stepOnce = true;
    if (this.mode === "idle") return this.start("self");
    this.paused = false;
    this.reflect();
  };

  /**
   * Take over, or hand back, at the action in hand.
   *
   * The action starts again in the new mode rather than the run carrying on
   * from where it was. Handing back in the middle of an action the visitor has
   * not finished would otherwise leave nobody to press its buttons, and the
   * panel would sit there waiting for something nobody was going to do. Doing
   * it again is safe: an action's evidence is a line the log has not shown yet,
   * so if it has already happened, the new attempt sees it immediately.
   */
  Tutorial.prototype.toggleHands = function () {
    if (this.mode === "idle") {
      track("drive", "hands");
      return this.start("hands");
    }
    var step = this.at, action = this.atAction;
    track("drive", this.mode === "hands" ? "self" : "hands");
    this.mode = this.mode === "hands" ? "self" : "hands";
    this.paused = false;
    this.stepOnce = false;
    this.generation++;               // let the wait in flight go
    this.reflect();
    this.run(step, action);
  };

  /**
   * Play the step it is in from the beginning.
   *
   * Not "go back one step", which the device cannot do: this run leaves real
   * state behind it on three cards and on a chain, and a step that put a seed
   * on a blank card drives a different ceremony the second time, because the
   * card is no longer blank. So a step says whether it can be run again, and
   * this is only offered where the answer is yes.
   *
   * It lands paused. Somebody who has pressed this wants to watch the step
   * rather than have it start away from them again.
   */
  /**
   * The answer, and the run.
   *
   * getUserMedia is called from inside this click rather than from the Play
   * that came before it, because this is the gesture the visitor made knowing
   * what it was for, which is also the one Safari will accept.
   */
  Tutorial.prototype.choose = function (camera) {
    track("camera", camera ? "yes" : "no");
    this.useCamera = camera;
    this.chooser.hidden = true;
    this.start(this.pending || "self");
  };

  /**
   * Where Back lands, which is the nearest point the device can actually be at.
   *
   * Three seeds went onto three cards, and a card that has one is not the card
   * that step began with: replaying it would drive a ceremony the screen is not
   * showing. Deleting the secret does not undo it either, since the card keeps
   * the PIN it was given. So the answer is not to disable the button, which
   * teaches nothing, but to send it somewhere true: the step it is in if that
   * can be re-driven, else the last one that can, else the beginning.
   */
  Tutorial.prototype.backTarget = function () {
    var steps = this.steps || [];
    for (var i = this.at; i >= 0; i--) {
      var can = steps[i] && steps[i].replay;
      if (typeof can === "function") can = can();
      if (can) return i;
    }
    return -1;
  };

  Tutorial.prototype.stepBack = function () {
    if (this.mode === "idle") return;
    var target = this.backTarget();
    if (target < 0) return this.restart();
    track("back", (this.steps[target] || {}).title || "");
    // Landing paused: somebody who pressed this wants to watch the step rather
    // than have it start away from them again.
    this.paused = true;
    this.stepOnce = false;
    this.generation++;               // let the wait in flight go
    this.reflect();
    this.run(target, 0);
  };

  Tutorial.prototype.restart = function () {
    // A reload, and it has to be: the three cards are simulated in the wallet's
    // own Python, so what they hold is not the page's to reset. What can be
    // fixed is landing back on a Play button afterwards, which is what made it
    // feel like the page had merely refreshed. It comes back running, in the
    // mode it was in.
    track("restart", "step " + (this.at + 1));
    var params = new URLSearchParams(location.search);
    params.set("tutorial", this.mode === "hands" ? "hands" : "play");
    location.search = params.toString();
  };

  Tutorial.prototype.reflect = function () {
    // Before anything is running there is one thing to do here, and it was an
    // unlabelled grey glyph between two others exactly like it. Idle, it is the
    // page's primary control and says so; once a run exists it becomes one of
    // three, because then the other two mean something and the row is a
    // transport rather than an invitation.
    var idle = this.mode === "idle";
    this.root.dataset.state = idle ? "idle" : "running";
    // Six empty marks measure nothing. The row appears with the run it is
    // measuring; before that it is a progress bar for a thing not happening.
    this.phaseRow.hidden = idle;
    if (!idle) this.chooser.hidden = true;
    iconInto(this.playButton, this.paused ? ICONS.play : ICONS.pause,
             this.paused ? "Play" : "Pause");
    if (idle) {
      this.playButton.appendChild(document.createTextNode("Play the demo"));
    }
    this.playButton.classList.toggle("wide", idle);
    this.playButton.classList.toggle("primary", idle);
    this.stepButton.hidden = idle;
    this.againButton.hidden = idle;
    this.backButton.hidden = idle;
    // Always live, and it says where it goes before it is pressed, because
    // where that is depends on what the run has already left behind it.
    this.backButton.disabled = false;
    var target = idle ? -1 : this.backTarget();
    var steps = this.steps || [];
    this.backButton.title =
      target === this.at ? "Play this step again"
      : target >= 0 ? "Back to " + (steps[target] || {}).title
      : "Start again: a card with a seed on it cannot be given its first one twice";
    this.playButton.classList.toggle("on", this.mode === "self" && !this.paused);
    // Never off. It used to be disabled while the visitor was driving, on the
    // grounds that hands on already is stepping, which is true and beside the
    // point: the press somebody makes there means "do this one for me", and it
    // was dead in the one mode where anybody gets stuck.
    this.stepButton.disabled = false;
    this.stepButton.title = this.mode === "hands"
      ? "Do this step for me" : "One step, then wait";
    this.handsButton.textContent = this.mode === "hands" ? "Let it drive" : "I will drive";
    this.handsButton.classList.toggle("on", this.mode === "hands");
    this.setProgress(this.fraction || 0);
  };

  Tutorial.prototype.start = function (mode) {
    if (this.mode !== "idle") return;
    // Only if that is what was chosen, and inside the click that chose it, which
    // is the gesture a camera prompt needs. Not waited on: the first card goes
    // into the reader long before the first photograph, and a run must not sit
    // on a dialog nobody answers.
    if (this.useCamera) this.askForCamera();
    this.heading.hidden = true;
    this.mode = mode;
    this.paused = false;
    this.reflect();
    this.watchEntropy();
    this.run(0);
  };

  /** Between actions, and only there, so a pause never lands mid keypress. */
  Tutorial.prototype.gate = function () {
    var self = this;
    if (!this.paused) return Promise.resolve();
    return this.poll(86400000, function () { return !self.paused; }, "the Play button");
  };

  // ------------------------------------------------------------ the runner

  // Resumes at an action rather than at a step, so a retry after the faucet
  // answered does not ask the faucet again.
  Tutorial.prototype.run = function (fromStep, fromAction) {
    var self = this;
    var steps = this.steps || (this.steps = buildSteps(this));
    var context = { tutorial: this, state: this.state || (this.state = {}) };

    function runStep(index, first) {
      if (index >= steps.length) return Promise.resolve();
      var step = steps[index];
      self.at = index;
      self.stepSize = step.actions.length;
      self.stepText.textContent = step.title;
      self.setPhase(step.phase);
      // Where Back would land depends on the step the run is in and on what the
      // cards hold, and both move while nobody is pressing anything, so the
      // controls are asked to say so again at every step rather than only when
      // one of them is pressed.
      self.reflect();
      if (!first) {
        self.verdict.textContent = "";
        self.verdict.removeAttribute("data-state");
        // The step's title has just changed; the last step's last instruction
        // is not the thing to leave standing under it.
        self.doText.textContent = "";
      }
      self.fraction = first / step.actions.length;
      self.setProgress(self.fraction);
      // Reaching a step, not resuming one. Taking the buttons in the middle of
      // a step re-enters it at the action in hand, and counting that would say
      // the run got there twice.
      if (!first) track("step-reached", step.title);

      // A step opens with its title, which is read before the first action
      // rather than underneath one already running.
      var opening = first ? Promise.resolve() : self.pace(step.title, STEP_MAX);

      return step.actions.slice(first).reduce(function (chain, action, offset) {
        var at = first + offset;
        return chain.then(function () {
          self.atAction = at;
          return self.gate();
        }).then(function () {
          self.doText.textContent = action.instruct || "";
          if (self.mode !== "self") return null;
          // The instruction is up; leave time to read it before the device
          // moves. An action with nothing to say gets no wait, because what
          // it is waiting for is the thing to watch.
          return self.pace(action.instruct).then(function () {
            // Asked twice on purpose: once before the beat and once after it,
            // so a Pause pressed while the instruction is being read stops the
            // run there rather than one action later.
            return self.gate();
          }).then(function () {
            if (action.perform) return action.perform(context);
          });
        }).then(function () {
          return action.until(context);
        }).then(function () {
          self.fraction = (at + 1) / step.actions.length;
          self.setProgress(self.fraction);
          if (self.stepOnce) {           // one action was all that was asked for
            self.stepOnce = false;
            self.paused = true;
            self.reflect();
          }
        });
      }, opening).then(function () {
        return runStep(index + 1, 0);
      });
    }

    return runStep(fromStep, fromAction || 0).then(function () {
      self.doText.textContent = "";
      self.endTransfer();
      self.setProgress(0);
      // The last step returned, so the spend is confirmed and the whole
      // ceremony is behind them. Only reachable here.
      track("finished", "");
      if (scope.Track) scope.Track.milestone("tutorial-finished");
      self.releaseCamera();
      self.offerHandsOn();
    }).catch(function (error) {
      self.fail(error);
    });
  };

  /**
   * What to do with somebody who has just watched the whole thing.
   *
   * It ended on a transaction id and offered nothing, which is a strange way to
   * treat six minutes of somebody's attention. This is the one offer that uses
   * what they now have: they have seen every screen and know what each was for,
   * so the buttons are worth more to them now than they were at the start.
   *
   * A reload rather than a restart in place: the device is holding the state the
   * run left it in, and a ceremony that begins by discarding three cards is not
   * the one being demonstrated.
   */
  Tutorial.prototype.offerHandsOn = function () {
    if (this.handsOffer) return;
    this.handsOffer = this.control("Try it yourself", function () {
      track("hands-offer", "taken");
      var params = new URLSearchParams(location.search);
      params.set("tutorial", "hands");
      location.search = params.toString();
    });
    this.handsOffer.classList.add("primary");
  };

  Tutorial.prototype.fail = function (error) {
    var self = this;
    this.stopEntropy();
    this.releaseCamera();
    this.setProgress(0);
    this.endTransfer();
    this.verdict.dataset.state = "bad";
    this.verdict.textContent = error.message;
    this.doText.textContent = "This step did not get where it was going. Try it "
      + "again, or take the buttons yourself.";
    if (!this.retryButton) {
      this.retryButton = this.control("Try again", function () {
        var step = self.at, action = self.atAction;
        self.verdict.textContent = "";
        self.verdict.removeAttribute("data-state");
        self.retryButton.remove();
        self.retryButton = null;
        self.generation++;
        self.cursor = self.lines.length;
        self.run(step, action);
      });
    }
  };

  // ------------------------------------------------------- building the steps

  // Each step used to carry a paragraph saying what was about to happen, and
  // every one of them was four lines of the same argument the step itself is
  // about to demonstrate. Watching the card get a PIN twice teaches that better
  // than a sentence saying it will, and on a phone the sentence was most of the
  // panel. What is left is the title, and which phase it belongs to.
  // replay says whether this step can be run again from where it left off, and
  // it is a fact about the device rather than a preference. A step that puts a
  // seed on a blank card cannot: the card is not blank the second time, so the
  // ceremony it drives is not the one on the screen. Reading a key off a card,
  // building the wallet, asking the faucet, signing: all of those can be done
  // twice and come out the same. Broadcasting cannot, and Done is the end.
  function step(title, phase, actions, replay) {
    return {
      title: title, phase: phase, actions: actions,
      // true, false, or a question to ask at the moment somebody presses Back:
      // whether a step can be run again is sometimes a fact about the device
      // rather than about the step.
      replay: replay === undefined ? true : replay,
    };
  }

  /** An action: what has to happen, what does it, and how we know it did. */
  function act(instruct, perform, until) {
    return { instruct: instruct, perform: perform, until: until };
  }

  function buildSteps(tutorial) {
    var t = tutorial;

    function keys(names, gap) {
      return function () { return t.press(names, gap); };
    }

    function screenIs(name, timeout) {
      return function () { return t.until("display\\(\\) enter: " + name + "\\b", timeout || 120000); };
    }

    function logged(pattern, timeout) {
      return function () { return t.until(pattern, timeout || 180000); };
    }

    function settle(ms) {
      return function () { return t.sleep(ms); };
    }

    function inserted(index) {
      return function () {
        return t.poll(60000, function () { return t.tray.inserted() === index; },
                      index < 0 ? "the card to come out" : "the card to go in");
      };
    }

    // The PIN, typed at speed. Four presses of the first key on the keyboard,
    // then the third side button to save: the shortest PIN the card accepts,
    // and the same one every time it is asked for. The ceremony is the real
    // one, screen for screen; only the typing is quick.
    function pin() {
      return keys(["Enter", "Enter", "Enter", "Enter", "3"], 90);
    }

    /**
     * Climb back to the home screen.
     *
     * Left goes to the back arrow at the top of a list screen and select takes
     * it, which is how many screens deep this is does not have to be known. Up
     * would be shorter and is wrong: on the home screen itself it lands on the
     * power button, and selecting that reboots the device and takes the cards
     * with it.
     */
    function homeAgain() {
      return act(
        "Press left and then select, as many times as it takes, until the "
        + "device is back on its home screen.",
        function () {
          var tries = 0;
          function climb() {
            if (t.currentScreen() === "MainMenuScreen" || tries++ > 8) return Promise.resolve();
            return t.press(["ArrowLeft"]).then(function () {
              if (t.currentScreen() === "MainMenuScreen") return;
              return t.press(["Enter"]).then(function () { return t.sleep(400); }).then(climb);
            });
          }
          return climb();
        },
        function () {
          return t.poll(120000, function () {
            return t.currentScreen() === "MainMenuScreen";
          }, "the home screen");
        });
    }

    /**
     * Keep confirming until a screen arrives, because what is in between
     * depends on settings and on the transaction rather than on this flow.
     *
     * Only ever press on a screen that has been up for two looks in a row. A
     * key sent during a transition is buffered and taken by whatever arrives
     * next, and one stray press past the signing screen dismisses the signed QR
     * before anything can read it.
     */
    function advance(target, tries, instruct) {
      return act(
        instruct || ("Keep pressing the select button until the device reaches "
                     + target + "."),
        function () {
          var attempts = 0;
          var previous = null;
          function again() {
            if (t.currentScreen() === target) return Promise.resolve();
            if (attempts++ > (tries || 6) * 2) return Promise.resolve();
            return t.sleep(900).then(function () {
              var screen = t.currentScreen();
              if (screen === target) return;
              if (screen !== previous) {
                previous = screen;                 // still settling; look again
                return again();
              }
              previous = null;
              return t.press(["Enter"]).then(again);
            });
          }
          return again();
        },
        function () {
          return t.poll(120000, function () { return t.currentScreen() === target; }, target);
        });
    }

    /**
     * Make the device forget the seed it is holding, and take the card out.
     *
     * Every flow that loads a seed off a card ends with this, so that the next
     * one starts from a device holding nothing. It is the honest state for a
     * signer between jobs, and it is also what keeps the menus predictable: the
     * Seeds screen offers "Load a seed" straight away when there is no seed
     * loaded, and a list of seeds when there is.
     */
    function forgetTheSeed(card) {
      return [
        act("Make the device forget the seed",
            keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
        act("The loaded seed", keys(["Enter"]), screenIs("SeedOptionsScreen")),
        act("Discard",
            keys(["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
            screenIs("WarningScreen")),
        act("Confirm",
            keys(["ArrowDown", "Enter"]), screenIs("MainMenuScreen")),
        act(card + " out of the reader",
            function () { t.tray.eject(); }, inserted(-1)),
      ];
    }

    /** The phone holds a QR up to the device's camera. */
    function handUp(caption, payload, until) {
      return act(null, null, function (context) {
        t.transfer("in", caption);
        // The caption is read, then the code goes up. Painting both in the same
        // frame is what made the seed appear to load itself.
        return t.pace(caption).then(function () {
          t.paintMatrix(scope.QREncode.matrix(
            typeof payload === "function" ? payload(context) : payload));
          t.scanning("in");
          return t.beat(HOLD_UP);
        }).then(function () {
          return until(context);
        }).then(function (value) {
          return t.beat(HOLD_AFTER).then(function () {
            t.endTransfer();
            return value;
          });
        });
      });
    }

    /** The same, for something too big for one code: the frames cycle. */
    function handUpFrames(caption, frames, until) {
      return act(null, null, function (context) {
        t.transfer("in", caption);
        var done = false;
        // The caption first, as above. The frames then cycle for as long as the
        // device needs them, which is already visible; what was not was the
        // moment the phone put the first one up.
        return t.pace(caption).then(function () {
          var list = typeof frames === "function" ? frames(context) : frames;
          t.scanning("in");
          var at = 0;
          (function cycle() {
            if (done) return;
            t.paintMatrix(scope.QREncode.matrix(list[at % list.length]));
            at++;
            setTimeout(cycle, 550);
          })();
          return until(context);
        }).then(function (value) {
          done = true;
          return t.beat(HOLD_AFTER).then(function () {
            t.endTransfer();
            return value;
          });
        }, function (error) {
          done = true;
          t.endTransfer();
          throw error;
        });
      });
    }

    /** The phone reads whatever the device is showing. */
    function readOff(caption, handle, timeout) {
      return act(null, null, function (context) {
        t.transfer("out", caption);
        t.scanning("out");
        var collector = null;
        return t.poll(timeout || 180000, function () {
          t.mirrorDevice();
          var text = t.readDevice();
          if (!text) return false;
          if (text.toLowerCase().indexOf("ur:") === 0) {
            collector = collector || scope.URDecode.collector();
            collector.receive(text);
            // Real sub-step progress: codes actually read, out of the number
            // this transfer turned out to have.
            if (collector.parts()) {
              t.caption.textContent = caption + " Code " + collector.have()
                                    + " of " + collector.parts() + ".";
              t.subProgress(collector.have() / collector.parts());
            }
            if (!collector.done()) return false;
            return handle(context, collector) || true;
          }
          return handle(context, text) || true;
        }, "the QR on the device's screen").then(function (value) {
          // Read, and left up for a moment: the other direction deserves the
          // same beat, or the caption vanishes the instant the phone has it.
          return t.beat(HOLD_AFTER).then(function () {
            t.endTransfer();
            return value;
          });
        });
      });
    }

    // -------------------------------------------------- a seed onto a card

    function seedOntoCard(i) {
      var seed = SEEDS[i];
      var card = "Card " + seed.card;
      return step(
        "Put a test seed on " + card,
        PHASES[0],
        [
          act(card + " into the reader",
              function () { t.tray.insert(i); }, inserted(i)),
          // Made on the device, not handed to it. A seed arriving as a QR is a
          // seed that existed somewhere else first, which is the one thing a
          // signing device is for avoiding; the codes in this run are for the
          // things that are meant to travel, which are public keys and an
          // unsigned transaction.
          act("Seeds", keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
          act("Create a seed",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("ButtonListScreen")),
          // What the camera is pointed at is the coordinator's side of this, so
          // it belongs in the waiting rather than in the pressing: an action's
          // perform is skipped when the visitor is driving, and a hands on run
          // would otherwise photograph an empty panel and be told its entropy
          // was poor. The same reason the QR codes are painted in their waits.
          act("A new seed, from a photograph", keys(["Enter"]), function () {
            return t.until("display\\(\\) enter: ToolsImageEntropyLivePreviewScreen\\b",
                           120000).then(function (value) {
              t.showEntropy();
              return t.beat(900).then(function () { return value; });
            });
          }),
          act("Take the picture", keys(["Enter"]), function () {
            return t.until("display\\(\\) enter: ToolsImageEntropyFinalImageScreen\\b",
                           120000).then(function (value) {
              t.stopEntropy();
              t.endTransfer();
              return value;
            });
          }),
          act("Accept it", keys(["ArrowRight"]), screenIs("ButtonListScreen")),
          act("Twelve words", keys(["Enter"]), screenIs("DireWarningScreen", 120000)),
          act("The device says to keep them private",
              keys(["Enter"]), screenIs("SeedWordsScreen")),
          act("The twelve words it made",
              keys(["Enter", "Enter", "Enter"]),
              screenIs("SeedWordsBackupTestPromptScreen")),
          // Skip, not Verify: the check types all twelve words back in, which is
          // the right thing to do with a seed you are keeping and forty presses
          // of a demo whose seed is thrown away at the end of the step.
          act("Skip the backup check",
              keys(["ArrowDown", "ArrowDown", "Enter"]), screenIs("SeedFinalizeScreen")),
          act("Done", keys(["Enter"]), screenIs("SeedOptionsScreen")),
          act("Backup seed",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("ButtonListScreen")),
          act("To SeedKeeper",
              keys(["ArrowDown", "Enter"]), screenIs("SeedAddPassphraseScreen")),
          act("The card asks for a PIN",
              pin(), screenIs("WarningScreen")),
          act("It has none yet",
              keys(["Enter"]), screenIs("SeedAddPassphraseScreen")),
          act("Choose one",
              pin(), screenIs("SeedAddPassphraseScreen")),
          act("Again, to confirm",
              pin(), screenIs("LargeIconStatusScreen")),
          act("The card is set up",
              keys(["Enter"]), screenIs("SeedAddPassphraseScreen")),
          act("Accept the label it offers",
              keys(["3"]),
              logged("\\[card\\] Card " + seed.card + " stored secret", 240000)),
          act(null, null, screenIs("LargeIconStatusScreen")),
          act("The seed is on the card",
              keys(["Enter"]), screenIs("SeedOptionsScreen")),
          act("Now the device forgets it",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("WarningScreen")),
          act("Confirm",
              keys(["ArrowDown", "Enter"]), screenIs("MainMenuScreen")),
          act(card + " out of the reader",
              function () { t.tray.eject(); }, inserted(-1)),
        ],
        // Not a fixed no: this step can be run again right up until the moment
        // it writes to the card, and a visitor who presses Back ten seconds in
        // should get the step rather than the whole demo. Once the card holds a
        // seed it holds a PIN too, and neither comes off.
        function () { return t.tray.state(i) === "blank"; });
    }

    // -------------------------------------------------- the key off a card

    function keyOffCard(i) {
      var seed = SEEDS[i];
      var card = "Card " + seed.card;
      return step(
        "Read " + card + "'s public key",
        PHASES[1],
        [
          act(card + " into the reader",
              function () { t.tray.insert(i); }, inserted(i)),
          act("Seeds",
              keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
          act("From SeedKeeper",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("SeedAddPassphraseScreen")),
          act("The card's PIN",
              pin(), screenIs("ButtonListScreen", 240000)),
          act("The one secret on the card",
              keys(["Enter"]),
              logged("\\[card\\] Card " + seed.card + " exporting secret", 240000)),
          act(null, null, screenIs("SeedFinalizeScreen", 240000)),
          act("Done", keys(["Enter"]), screenIs("SeedOptionsScreen")),
          act("Export Xpub",
              keys(["ArrowDown", "Enter"]), screenIs("ButtonListScreen")),
          act("Multisig",
              keys(["ArrowDown", "Enter"]), screenIs("ButtonListScreen")),
          act("Native Segwit",
              keys(["Enter"]), screenIs("ButtonListScreen")),
          act("Static, so it comes as one code",
              keys(["ArrowDown", "Enter"]), settle(1500)),
          // A privacy warning and a details page may sit between here and the
          // QR, depending on settings, so this is driven by where it has
          // arrived rather than by a fixed number of presses.
          advance("QRDisplayScreen", 5),
          readOff(card + "'s account public key",
                  function (context, text) {
                    context.state.keys = context.state.keys || [];
                    context.state.keys[i] = text;
                    t.detail(card + " account key", text);
                    return true;
                  }),
          act("Any button leaves the QR", keys(["Enter"]), screenIs("MainMenuScreen")),
        ].concat(forgetTheSeed(card)));
    }

    // -------------------------------------------------- signing, twice

    function signWith(i) {
      var seed = SEEDS[i];
      var card = "Card " + seed.card;
      return step(
        "Sign with " + card,
        PHASES[4],
        [
          act(card + " into the reader",
              function () { t.tray.insert(i); }, inserted(i)),
          act("Seeds",
              keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
          act("From SeedKeeper",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("SeedAddPassphraseScreen")),
          act("The card's PIN", pin(), screenIs("ButtonListScreen", 240000)),
          act("The secret on the card", keys(["Enter"]),
              logged("\\[card\\] Card " + seed.card + " exporting secret", 240000)),
          act(null, null, screenIs("SeedFinalizeScreen", 240000)),
          act("Done", keys(["Enter"]), screenIs("SeedOptionsScreen")),
          homeAgain(),
          act("Open Scan", keys(["Enter"]), screenIs("ScanScreen")),
          handUpFrames("The transaction to be signed, in several codes",
                       function (context) { return context.state.frames; },
                       function () {
                         return t.poll(300000, function () {
                           var screen = t.currentScreen();
                           return screen && screen !== "ScanScreen";
                         }, "the device to take the transaction");
                       }),
          advance("PSBTFinalizeScreen", 10,
                  "Through the review screens, until it offers to sign"),
          act("Approve it", keys(["Enter"]), screenIs("QRDisplayScreen", 240000)),
          readOff(card + "'s signature",
                  function (context, collector) {
                    var psbt = C.toBase64(collector.psbt());
                    context.state.signed = context.state.signed || [];
                    context.state.signed.push(psbt);
                    t.detail(card + " signed PSBT", psbt);
                    return true;
                  }, 300000),
          act("Any button leaves the QR", keys(["Enter"]),
              function () {
                return t.poll(60000, function () {
                  return t.currentScreen() === "MainMenuScreen";
                }, "the home screen");
              }),
        ].concat(forgetTheSeed(card)));
    }

    // -------------------------------------------------- the coordinator's own

    /** Work only the coordinator can do, so it happens in both modes. */
    function coordinator(work) {
      return act(null, null, function (context) {
        return Promise.resolve(work(context)).then(function () {
          // Deriving an address takes a fifth of a second and puts a sentence
          // up worth reading -- an address, an amount, a transaction id -- so
          // the step waits for a reader rather than for itself.
          return t.pace(t.verdict.textContent || t.stepText.textContent, STEP_MAX);
        });
      });
    }

    var steps = [];
    for (var s = 0; s < 3; s++) steps.push(seedOntoCard(s));
    for (var k = 0; k < 3; k++) steps.push(keyOffCard(k));

    steps.push(step(
      "Build the 2 of 3",
      PHASES[2],
      [
        coordinator(function (context) {
          t.showFace("Building the wallet", "Three keys into one 2 of 3.");
          // A fresh address, the way a wallet hands out a fresh one every time
          // rather than reusing the first. It also keeps two visitors doing this
          // at once out of each other's way: the three test seeds are public and
          // the same for everybody, so the wallet is the same wallet, and only
          // the address makes the coins theirs to watch.
          context.state.index = Math.floor(Math.random() * 10000);
          return C.buildWallet(context.state.keys).then(function (wallet) {
            context.state.wallet = wallet;
            t.detail("descriptor", wallet.descriptor);
            return C.deriveAddress(wallet, 0, context.state.index);
          }).then(function (receive) {
            context.state.receive = receive;
            t.detail("receive address", receive.address + "  (receive number "
                     + context.state.index + ")");
            t.detail("witness script", C.hex(receive.witnessScript));
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "The wallet is built. Its address for this "
              + "run is " + receive.address;
            t.showFace("2 of 3 wallet", receive.address);
          });
        }),
      ]));

    steps.push(step(
      "Tell the device about the wallet",
      PHASES[2],
      [
        act("Open Scan", keys(["Enter"]), screenIs("ScanScreen")),
        handUp("The 2 of 3 descriptor",
               function (context) {
                 return context.state.wallet.descriptor;
               },
               screenIs("MultisigWalletDescriptorScreen", 240000)),
        act("The wallet, then accept it",
            keys(["Enter"]),
            function () {
              return t.poll(120000, function () {
                var screen = t.currentScreen();
                return screen && screen !== "MultisigWalletDescriptorScreen";
              }, "the device to accept the wallet");
            }),
        homeAgain(),
      ]));

    steps.push(step(
      "Ask Bitsaga Signet's faucet for coins",
      PHASES[3],
      [
        coordinator(function (context) {
          t.showFace("Asking the faucet", context.state.receive.address);
          return C.network.claim(context.state.receive.address).then(function (paid) {
            context.state.funding = paid.txid;
            t.detail("faucet transaction", paid.txid);
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "The faucet sent "
              + (paid.amount_sat / 1e8).toFixed(8) + " to the wallet. " + NOT_REAL;
            t.showFace("Coins on the way", paid.txid);
          });
        }),
        coordinator(function (context) {
          return waitForBlock(t, context.state.funding, "the faucet's payment",
                              "Waiting for Bitsaga Signet to put it in a block.");
        }),
      ]));

    steps.push(step(
      "Build the spend",
      PHASES[4],
      [
        coordinator(function (context) {
          var state = context.state;
          t.showFace("Building the spend", "One input, one output, and the "
                     + "script that needs two signatures.");
          return C.network.proof(state.funding).then(function (proof) {
            var outputs = C.transactionOutputs(proof.tx);
            var script = C.hex(state.receive.scriptPubkey);
            var ours = outputs.filter(function (out) { return out.script === script; })[0];
            if (!ours) throw new Error("the faucet's transaction does not pay this wallet");
            state.input = { txid: state.funding, vout: ours.index, value: ours.value };
            return C.deriveAddress(state.wallet, 1, state.index);
          }).then(function (change) {
            state.change = change;
            state.amount = state.input.value - FEE;
            state.psbt = C.toBase64(C.buildPsbt(state.input, state.receive,
                                                change.scriptPubkey, state.amount));
            state.frames = specterFrames(state.psbt);
            t.detail("spending", state.input.txid + ":" + state.input.vout);
            t.detail("paying", change.address);
            t.detail("unsigned PSBT", state.psbt);
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "Ready to sign: "
              + (Number(state.amount) / 1e8).toFixed(8) + " to " + change.address
              + ", with " + FEE + " sat of fee. " + NOT_REAL;
            t.showFace("Unsigned transaction", state.frames.length + " codes to hold up");
          });
        }),
      ]));

    steps.push(signWith(0));
    steps.push(signWith(1));

    steps.push(step(
      "Put the two signatures together and send it",
      PHASES[5],
      [
        coordinator(function (context) {
          var state = context.state;
          t.showFace("Finishing it", "Two signatures into one witness.");
          var signatures = {};
          state.signed.forEach(function (psbt) {
            Object.assign(signatures, C.partialSignatures(psbt));
          });
          t.detail("signatures collected", String(Object.keys(signatures).length), true);
          return C.finalise(state.input, state.receive, state.change.scriptPubkey,
                            state.amount, signatures).then(function (final) {
            state.spend = final;
            t.detail("signed transaction", final.hex);
            t.detail("transaction id", final.txid);
            return C.network.broadcast(final.hex);
          }).then(function (sent) {
            if (sent.txid && sent.txid !== state.spend.txid) {
              throw new Error("the network gave the transaction a different id");
            }
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "Sent to Bitsaga Signet: " + state.spend.txid;
            t.showFace("Broadcast", state.spend.txid);
          });
        }),
        coordinator(function (context) {
          return waitForBlock(t, context.state.spend.txid, "the spend",
                              "Waiting for the spend to be mined.");
        }),
      ],
      false));

    steps.push(step(
      "Done",
      PHASES[5],
      [
        coordinator(function (context) {
          t.verdict.dataset.state = "good";
          t.verdict.textContent = "Confirmed on Bitsaga Signet: "
            + context.state.spend.txid;
          t.showFace("Confirmed", context.state.spend.txid);
          t.doText.textContent =
            "Everything above happened in this tab. The one thing to carry away: "
            + "all three keys were on one device here, which is fine for a demo "
            + "and wrong for real funds, where the keys belong in different "
            + "places and different hands.";
          return Promise.resolve();
        }),
      ],
      false));

    return steps;
  }

  /**
   * Wait for a transaction to be in a block.
   *
   * The proof endpoint answers 404 until then, so asking for the proof is the
   * confirmation check. The progress line follows the chain rather than a
   * guess: Bitsaga Signet makes a block roughly every thirty seconds, and the
   * status endpoint says how old the last one is, so the line grows towards the
   * next block and starts again if that block did not carry the transaction.
   */
  function waitForBlock(t, txid, what, say) {
    t.showFace("Waiting for a block", txid);
    var deadline = Date.now() + 300000;
    return new Promise(function (resolve, reject) {
      (function again() {
        C.network.proof(txid).then(function (proof) {
          t.detail(what + " confirmed in block", String(proof.height), true);
          t.verdict.dataset.state = "good";
          t.verdict.textContent = "Confirmed on Bitsaga Signet in block "
            + proof.height + ". " + NOT_REAL;
          t.showFace("Confirmed", "Block " + proof.height);
          resolve();
        }, function (error) {
          if (error.status !== 404) return reject(error);
          if (Date.now() > deadline) {
            return reject(new Error("Bitsaga Signet has not confirmed this "
                                    + "transaction. " + say));
          }
          C.network.status().then(function (status) {
            t.subProgress(Math.min(1, (status.last_block_age_seconds || 0) /
                                      (status.block_seconds || 30)));
          }).catch(function () {}).then(function () {
            setTimeout(again, 3000);
          });
        });
      })();
    });
  }

  /**
   * A PSBT split into the frames SeedSigner reassembles by plain concatenation.
   * Small frames rather than one dense code, which is what every coordinator
   * does and what a 640 by 480 camera can actually read.
   */
  function specterFrames(payload, size) {
    var chunk = size || 280;
    var parts = [];
    for (var at = 0; at < payload.length; at += chunk) {
      parts.push(payload.substr(at, chunk));
    }
    return parts.map(function (part, i) {
      return "p" + (i + 1) + "of" + parts.length + " " + part;
    });
  }

  // ------------------------------------------------------------ what the page uses

  scope.WalletTutorial = {
    /** The one button on the resting page. */
    offer: function (container) {
      var style = element("style");
      style.textContent = CSS;
      document.head.appendChild(style);
      var button = element("button", "tut-start", "Start the multi-sig demo");
      button.type = "button";
      button.id = "start-tutorial";
      button.addEventListener("click", function () {
        track("open", "");
        var params = new URLSearchParams(location.search);
        params.set("tutorial", "1");
        location.search = params.toString();
      });
      container.appendChild(button);
    },

    mount: function (options) {
      var tutorial = new Tutorial(options);
      scope.WalletTutorial.current = tutorial;
      // Asked for by the URL, which is what the end of a run hands out. Nothing
      // to press: somebody who chose this has already watched it once.
      // Reloaded into a run: it still asks what to photograph, because a reload
      // is not an answer. What the URL decides is which mode the answer starts.
      if (options.mode) {
        tutorial.pending = options.mode;
        tutorial.chooser.hidden = false;
      }
      // The same decoder the wallet's own camera path uses, because the phone
      // reading the device's screen is the same job in the other direction.
      if (!scope.jsQR) {
        var tag = document.createElement("script");
        tag.src = "jsQR.js";
        document.head.appendChild(tag);
      }
      return tutorial;
    },

    /**
     * The camera the device gets while the tutorial is running: the phone's own
     * screen, as a stream. Real pixels, decoded by the page's real decoder;
     * only the lens is missing, and no webcam is ever opened.
     */
    cameraSource: function () {
      var tutorial = scope.WalletTutorial.current;
      if (!tutorial) throw new Error("the tutorial is not mounted");
      // The canvas itself, not a stream of it. captureStream does not exist in
      // Safari, and this called it unguarded: on an iPhone it threw, the camera
      // layer reported that it could not open a camera, and the device put up
      // Hardware Error while the page advised allowing one nobody had asked for.
      // Nothing ever needed a stream: drawImage takes a canvas.
      return tutorial.canvas;
    },

    seeds: SEEDS,
    specterFrames: specterFrames,
  };
})(typeof self !== "undefined" ? self : this);
