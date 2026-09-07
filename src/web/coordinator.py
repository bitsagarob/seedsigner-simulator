"""The coordinator's chain half, on embit.

This replaces hand-written JavaScript with the library the device already runs.
Nothing here is novel: it is descriptors, PSBTs and transactions, and embit
knows what those are. What stays in JavaScript is the browser: the panel, the
camera, QR in and out, and talking to the faucet.

Every function here is checked against the JavaScript it replaces, byte for
byte, by test/test_coordinator_parity.py.
"""
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
