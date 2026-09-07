"""The coordinator's chain half, on embit.

This replaces hand-written JavaScript with the library the device already runs.
Nothing here is novel: it is descriptors, PSBTs and transactions, and embit
knows what those are. What stays in JavaScript is the browser: the panel, the
camera, QR in and out, and talking to the faucet.

Every function here is checked against the JavaScript it replaces, byte for
byte, by test/test_coordinator_parity.py.
"""
from embit import compact
from embit.descriptor import Descriptor
from embit.ec import PublicKey
from embit.networks import NETWORKS
from embit.psbt import PSBT, DerivationPath
from embit.script import Script
from embit.transaction import (Transaction, TransactionInput, TransactionOutput,
                               Witness)

NET = NETWORKS["test"]
SEQUENCE = 0xFFFFFFFD


def wallet(exported_keys, threshold=2):
    """The tutorial's 2-of-3, from the keys three devices exported."""
    inner = ",".join("%s/{0,1}/*" % key for key in exported_keys)
    return "wsh(sortedmulti(%d,%s))" % (threshold, inner)


def address(descriptor, branch, index):
    """One address, with everything a PSBT will later need about it."""
    d = Descriptor.from_string(descriptor).derive(index, branch_index=branch)
    return {
        "address": d.address(NET),
        "script_pubkey": d.script_pubkey().data.hex(),
        "witness_script": d.witness_script().data.hex(),
        "cosigners": [
            {
                "pubkey": key.sec().hex(),
                "fingerprint": key.origin.fingerprint.hex(),
                "derivation": key.origin.derivation,
            }
            for key in d.keys
        ],
    }


def build_psbt(descriptor, branch, index, utxo, destination, amount):
    """A PSBT spending one output of this wallet, paying one script.

    Everything the device cannot know goes in: what the output being spent is
    worth, the script it pays into, and which key of each cosigner is in that
    script, with the path it came from.
    """
    derived = Descriptor.from_string(descriptor).derive(index, branch_index=branch)
    tx = Transaction(
        version=2,
        vin=[TransactionInput(bytes.fromhex(utxo["txid"]), utxo["vout"],
                              sequence=SEQUENCE)],
        vout=[TransactionOutput(amount, Script(bytes.fromhex(destination)))],
    )
    psbt = PSBT(tx)
    scope = psbt.inputs[0]
    scope.witness_utxo = TransactionOutput(utxo["value"], derived.script_pubkey())
    scope.witness_script = derived.witness_script()
    for key in derived.keys:
        scope.bip32_derivations[PublicKey.parse(key.sec())] = DerivationPath(
            key.origin.fingerprint, key.origin.derivation)
    return psbt.to_string()


def finalise(descriptor, branch, index, utxo, destination, amount, signatures):
    """Two signatures and the witness script make the finished transaction."""
    derived = Descriptor.from_string(descriptor).derive(index, branch_index=branch)
    script = derived.witness_script()
    wanted = [signatures[key.sec().hex()] for key in derived.keys
              if key.sec().hex() in signatures]
    if len(wanted) < 2:
        raise ValueError("a 2 of 3 needs two signatures, and this has %d" % len(wanted))

    tx = Transaction(
        version=2,
        vin=[TransactionInput(bytes.fromhex(utxo["txid"]), utxo["vout"],
                              sequence=SEQUENCE)],
        vout=[TransactionOutput(amount, Script(bytes.fromhex(destination)))],
    )
    txid = tx.txid().hex()
    # OP_0 for CHECKMULTISIG's off-by-one, then the signatures in script order,
    # then the script they satisfy.
    tx.vin[0].witness = Witness(
        [b""] + [bytes.fromhex(s) for s in wanted[:2]] + [script.data])
    return {"hex": tx.serialize().hex(), "txid": txid}


# ------------------------------------------------------- single signature

# What Bitcoin Core will not relay: an output worth less than a third of what
# spending it would cost at the dust relay fee.
DUST = 294


def single_wallet(account):
    """wpkh, from one exported key."""
    return "wpkh(%s/{0,1}/*)" % account


def address_single(descriptor, branch, index):
    d = Descriptor.from_string(descriptor).derive(index, branch_index=branch)
    key = d.keys[0]
    return {
        "address": d.address(NET),
        "script_pubkey": d.script_pubkey().data.hex(),
        "pubkey": key.sec().hex(),
        "fingerprint": key.origin.fingerprint.hex(),
        "derivation": key.origin.derivation,
    }


def estimate_vsize(input_count, scripts):
    """The size of a P2WPKH spend before it exists. Every part is fixed length."""
    paid = sum(8 + len(compact.to_bytes(len(s))) + len(s) for s in scripts)
    base = (4 + len(compact.to_bytes(input_count)) + input_count * 41
            + len(compact.to_bytes(len(scripts))) + paid + 4)
    witness = 2 + input_count * (1 + 1 + 72 + 1 + 33)
    return -(-(base * 4 + witness) // 4)


def build_psbt_single(descriptor, spend):
    """Several inputs, several outputs, change and fee worked out from a rate.

    Change worth less than it costs to spend is dropped and the fee has it,
    which is what every wallet does and what the network prefers.
    """
    inputs, outputs = spend["inputs"], list(spend["outputs"])
    funded = sum(i["value"] for i in inputs)
    paying = sum(o["value"] for o in outputs)
    if funded < paying:
        raise ValueError("these inputs do not cover that spend")

    change_at = -1
    if spend.get("change"):
        change = spend["change"]
        scripts = [bytes.fromhex(o["script"]) for o in outputs]
        scripts.append(bytes.fromhex(change["script_pubkey"]))
        fee = -(-(estimate_vsize(len(inputs), scripts) * spend["fee_rate"]) // 1)
        left = funded - paying - int(fee)
        if left < 0:
            raise ValueError("these inputs do not cover that spend and its fee")
        if left >= DUST:
            change_at = len(outputs)
            outputs.append({"value": left, "script": change["script_pubkey"]})

    tx = Transaction(
        version=2,
        vin=[TransactionInput(bytes.fromhex(i["txid"]), i["vout"], sequence=SEQUENCE)
             for i in inputs],
        vout=[TransactionOutput(o["value"], Script(bytes.fromhex(o["script"])))
              for o in outputs],
    )
    psbt = PSBT(tx)
    for at, one in enumerate(inputs):
        source = one["source"]
        scope = psbt.inputs[at]
        scope.witness_utxo = TransactionOutput(
            one["value"], Script(bytes.fromhex(source["script_pubkey"])))
        scope.bip32_derivations[PublicKey.parse(bytes.fromhex(source["pubkey"]))] = \
            DerivationPath(bytes.fromhex(source["fingerprint"]), source["derivation"])
    if change_at >= 0:
        change = spend["change"]
        psbt.outputs[change_at].bip32_derivations[
            PublicKey.parse(bytes.fromhex(change["pubkey"]))] = DerivationPath(
                bytes.fromhex(change["fingerprint"]), change["derivation"])
    return psbt.to_string()


def dispatch(name, payload):
    """One entry point for the worker: JSON in, JSON out.

    Keeping the conversion here rather than in JavaScript means the worker never
    has to know the shape of anything, and a new function needs no plumbing.
    """
    import json

    fn = globals().get(name)
    if not callable(fn) or name.startswith("_"):
        raise ValueError("no such coordinator function: %s" % name)
    return json.dumps(fn(*json.loads(payload)))
