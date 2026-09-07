"""The coordinator's chain half, on embit.

This replaces hand-written JavaScript with the library the device already runs.
Nothing here is novel: it is descriptors, PSBTs and transactions, and embit
knows what those are. What stays in JavaScript is the browser: the panel, the
camera, QR in and out, and talking to the faucet.

Every function here is checked against the JavaScript it replaces, byte for
byte, by test/test_coordinator_parity.py.
"""
from embit import compact
from embit.descriptor.musig import key_agg
from embit.descriptor.taptree import TapLeaf
from embit.hashes import hash160, tagged_hash
from embit.misc import secp256k1
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


def transaction_outputs(raw_hex):
    """The outputs of a raw transaction, enough to find which one paid us."""
    tx = Transaction.from_string(raw_hex)
    return [{"index": i, "value": out.value, "script": out.script_pubkey.data.hex()}
            for i, out in enumerate(tx.vout)]


def partial_signatures(psbt_string):
    """Whatever signatures came back on the first input, by public key."""
    scope = PSBT.from_string(psbt_string).inputs[0]
    return {key.sec().hex(): sig.hex()
            for key, sig in scope.partial_sigs.items()}


def finalise_single(psbt_string):
    """One signature and its key per input, and the transaction is finished.

    The transaction comes out of the PSBT rather than being rebuilt, because
    what is broadcast has to be what was signed.
    """
    psbt = PSBT.from_string(psbt_string)
    for at, scope in enumerate(psbt.inputs):
        if not scope.partial_sigs:
            raise ValueError("input %d came back without a signature" % at)
        key, sig = next(iter(scope.partial_sigs.items()))
        psbt.tx.vin[at].witness = Witness([sig, key.sec()])
    return psbt.tx.serialize().hex()


# ------------------------------------------------------------------ MuSig2

PSBT_IN_MUSIG2_PARTICIPANT_PUBKEYS = 0x1A


def musig_wallet(keys):
    """2-of-3: key path musig(A,B), with musig(A,C) and musig(B,C) as leaves.

    Any two of the three can spend. A and B use the key path and pay for one
    signature; a pair involving C falls back to a leaf.
    """
    a, b, c = keys
    return ("tr(musig(%s,%s)/<0;1>/*,{pk(musig(%s,%s)/<0;1>/*),"
            "pk(musig(%s,%s)/<0;1>/*)})" % (a, b, a, c, b, c))


def _add_tweak(point, tweak):
    point = bytearray(point)
    out = secp256k1.ec_pubkey_add(point, tweak)
    return out if out is not None else point


def _leaves(tree):
    """Every TapLeaf, left to right, without hashing anything."""
    node = tree.tree if hasattr(tree, "tree") else tree
    if node is None:
        return []
    if isinstance(node, TapLeaf):
        return [node]
    return _leaves(node[0]) + _leaves(node[1])


def _aggregates(descriptor, branch, index):
    """Every musig() expression in the descriptor, with what a PSBT needs of it.

    The keydata of PSBT_IN_MUSIG2_PARTICIPANT_PUBKEYS is the plain aggregate of
    the participants *before* any derivation, because the derivation that
    follows musig() applies to the aggregate and is carried separately. Read off
    a Core PSBT rather than off the BIP: getting it wrong writes a field the
    signer ignores without complaining.
    """
    d = Descriptor.from_string(descriptor)
    derived_all = d.derive(index, branch_index=branch)
    merkle = derived_all.taptree.tweak() if derived_all.taptree else b""
    found = []

    def one(key, leaf_hash):
        parts = sorted(k.sec() for k in key.keys)
        plain = bytes(secp256k1.ec_pubkey_serialize(key_agg(parts)))
        derived = key.derive(index, branch_index=branch).sec()
        # A key-path signer signs for the output key, so its nonce is filed
        # under the taptweaked aggregate. A leaf signer signs for its own.
        # Parity is kept: Core writes the real prefix, not a forced 02.
        if leaf_hash is None and merkle is not None:
            point = secp256k1.ec_pubkey_parse(b"\x02" + derived[1:33])
            tweak = tagged_hash("TapTweak", derived[1:33] + merkle)
            signing = bytes(secp256k1.ec_pubkey_serialize(
                _add_tweak(point, tweak)))
        else:
            signing = derived
        found.append({
            "participants": parts,
            "plain": plain,
            "derived": derived,
            "signing": signing,
            "leaf": leaf_hash,
        })

    one(d.key, None)
    if d.taptree:
        # A leaf can only be hashed once its keys are derived, so the hashes come
        # from the derived tree and the participants from the undelivered one.
        # Same tree, same order, so position pairs them.
        derived = d.derive(index, branch_index=branch)
        for plain, ready in zip(_leaves(d.taptree), _leaves(derived.taptree)):
            leaf_hash = tagged_hash("TapLeaf", ready.serialize())
            for key in plain.keys:
                one(key, leaf_hash)
    return found


def musig_aggregates(descriptor, branch, index):
    """Every musig() expression, as a coordinator needs to talk about it.

    Two different keys stand for one aggregate and mixing them up is silent:
    PSBT_IN_MUSIG2_PARTICIPANT_PUBKEYS is keyed by the plain aggregate, and
    PSBT_IN_MUSIG2_PUB_NONCE is keyed by the aggregate after BIP-328
    derivation. A nonce filed under the plain one is ignored without complaint.
    """
    return [{"participants": [p.hex() for p in agg["participants"]],
             "plain": agg["plain"].hex(),
             "derived": agg["derived"].hex(),
             "signing": agg["signing"].hex(),
             "leaf": agg["leaf"].hex() if agg["leaf"] else None}
            for agg in _aggregates(descriptor, branch, index)]


def musig_address(descriptor, branch, index):
    d = Descriptor.from_string(descriptor).derive(index, branch_index=branch)
    return {
        "address": d.address(NET),
        "script_pubkey": d.script_pubkey().data.hex(),
        "internal_key": d.key.sec().hex(),
        "merkle_root": (d.taptree.tweak() if d.taptree else b"").hex(),
    }


def musig_psbt(descriptor, branch, index, utxo, destination, amount):
    """A PSBT a MuSig2 signer can act on, carrying no nonce yet.

    The device holds no descriptor, so everything it checks the aggregate
    against has to be here: the participants of every musig() expression, the
    derivation each aggregate took, and which of its own keys are in them.
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
    scope.taproot_internal_key = derived.key.get_public_key()
    if derived.taptree:
        scope.taproot_merkle_root = derived.taptree.tweak()

    leaves_of = {}
    for agg in _aggregates(descriptor, branch, index):
        scope.unknown[bytes([PSBT_IN_MUSIG2_PARTICIPANT_PUBKEYS]) + agg["plain"]] = \
            b"".join(agg["participants"])
        # The aggregate's own derivation, filed under the BIP-328 fingerprint of
        # the untweaked aggregate, which is what the device looks it up by.
        scope.taproot_bip32_derivations[PublicKey.parse(agg["derived"])] = (
            [agg["leaf"]] if agg["leaf"] else [],
            DerivationPath(hash160(agg["plain"])[:4], [branch, index]),
        )
        for part in agg["participants"]:
            leaves_of.setdefault(part, set())
            if agg["leaf"]:
                leaves_of[part].add(agg["leaf"])

    origins = {}
    for key in Descriptor.from_string(descriptor).keys:
        for one in getattr(key, "keys", [key]):
            origins[one.sec()] = one.origin
    for part, leaves in leaves_of.items():
        scope.taproot_bip32_derivations[PublicKey.parse(part)] = (
            sorted(leaves),
            DerivationPath(origins[part].fingerprint, origins[part].derivation),
        )
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
