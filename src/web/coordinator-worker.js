// The coordinator's chain half, off the main thread.
//
// It boots Python, unpacks embit and coordinator.py, and answers questions
// about descriptors, addresses, PSBTs and transactions. It never sees a seed
// and has no channel to the device: the page holds both halves apart.
//
// Same Pyodide the device uses, already downloaded and cached by then, but
// without any of the device's wheels. embit is pure Python, so this needs the
// interpreter and nothing else.

let pyodide = null;
let coordinator = null;

function reply(id, ok, value) {
  self.postMessage({ id: id, ok: ok, value: value });
}

async function boot(indexURL, zipURL) {
  importScripts(indexURL + "pyodide.js");
  pyodide = await loadPyodide({ indexURL: indexURL });
  const zip = await (await fetch(zipURL)).arrayBuffer();
  // Hashed before unpacking, and handed back, so the page can say which bytes
  // it is running rather than which bytes it asked for.
  const digest = await crypto.subtle.digest("SHA-256", zip);
  await pyodide.unpackArchive(zip, "zip", { extractDir: "/coordinator" });
  pyodide.runPython("import sys; sys.path.insert(0, '/coordinator')");
  coordinator = pyodide.pyimport("coordinator");
  return Array.from(new Uint8Array(digest))
    .map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
}

self.onmessage = async function (event) {
  const message = event.data;
  try {
    if (message.type === "boot") {
      reply(message.id, true, await boot(message.indexURL, message.zipURL));
      return;
    }
    if (!coordinator) throw new Error("the coordinator has not booted");
    // JSON both ways: the worker never has to know the shape of anything.
    const answer = coordinator.dispatch(message.fn, JSON.stringify(message.args));
    reply(message.id, true, JSON.parse(answer));
  } catch (error) {
    reply(message.id, false, String(error && error.message ? error.message : error));
  }
};
