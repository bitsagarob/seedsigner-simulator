"""The coordinator's chain half: descriptors, addresses, PSBTs, transactions.

Checked against signet-coordinator.js and against Bitcoin Core by
test/test_coordinator_parity.py.
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
    """Spend one output of this wallet, paying one script."""
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
    """The size of a P2WPKH spend before it exists."""
    paid = sum(8 + len(compact.to_bytes(len(s))) + len(s) for s in scripts)
    base = (4 + len(compact.to_bytes(input_count)) + input_count * 41
            + len(compact.to_bytes(len(scripts))) + paid + 4)
    witness = 2 + input_count * (1 + 1 + 72 + 1 + 33)
    return -(-(base * 4 + witness) // 4)


def build_psbt_single(descriptor, spend):
    """Several inputs and outputs. Change below the dust limit goes to fee."""
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
    """Finish a signed single-signature PSBT, from the PSBT's own transaction."""
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
    """2-of-3: key path musig(A,B), leaves musig(A,C) and musig(B,C)."""
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
    """Every musig() expression, with the three keys that stand for it.

    plain    KeyAgg of the undelivered participants   0x1a keydata
    derived  plus BIP-328                             taproot derivations
    signing  plus the taptweak, for a key path        0x1b keydata
    """
    d = Descriptor.from_string(descriptor)
    derived_all = d.derive(index, branch_index=branch)
    merkle = derived_all.taptree.tweak() if derived_all.taptree else b""
    found = []

    def one(key, leaf_hash):
        parts = sorted(k.sec() for k in key.keys)
        plain = bytes(secp256k1.ec_pubkey_serialize(key_agg(parts)))
        derived = key.derive(index, branch_index=branch).sec()
        # A key path signs for the output key, a leaf for its own aggregate.
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
        # Hashes need derived keys, participants need undelivered ones. Same
        # tree, same order, so position pairs them.
        derived = d.derive(index, branch_index=branch)
        for plain, ready in zip(_leaves(d.taptree), _leaves(derived.taptree)):
            leaf_hash = tagged_hash("TapLeaf", ready.serialize())
            for key in plain.keys:
                one(key, leaf_hash)
    return found


def musig_aggregates(descriptor, branch, index):
    """_aggregates as hex, for callers outside this module."""
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
    """A MuSig2 PSBT with no nonce yet: BIP-373.

    The device holds no descriptor, so every aggregate is described here.
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
        # Filed under the BIP-328 fingerprint of the untweaked aggregate.
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


# ------------------------------------------------------------- the nonce pool

# BIP-174 proprietary space. musig2_card.py writes the same key.
POOL_IDENTIFIER = b"DOOMSIGNER"
SUBTYPE_POOLED_NONCE = 0x01
SIZE_PUBNONCE = 66
SIZE_SEALED = 144
PSBT_IN_MUSIG2_PUB_NONCE = 0x1B


def _pooled_key(participant, index):
    return (bytes([0xFC])
            + compact.to_bytes(len(POOL_IDENTIFIER)) + POOL_IDENTIFIER
            + compact.to_bytes(SUBTYPE_POOLED_NONCE)
            + participant + index.to_bytes(2, "big"))


def _nonce_key(participant, aggregate, leaf=None):
    key = bytes([PSBT_IN_MUSIG2_PUB_NONCE]) + participant + aggregate
    return key + leaf if leaf else key


def pool_harvest(psbt_string, input_index=0):
    """The spare nonces a device left behind, named by their public half."""
    scope = PSBT.from_string(psbt_string).inputs[input_index]
    prefix = _pooled_key(b"", 0)[:-2]
    found = []
    for key, value in scope.unknown.items():
        key, value = bytes(key), bytes(value)
        if not key.startswith(prefix) or len(value) != SIZE_PUBNONCE + SIZE_SEALED:
            continue
        found.append({"participant": key[len(prefix):len(prefix) + 33].hex(),
                      "id": value[:SIZE_PUBNONCE].hex(),
                      "entry": value.hex()})
    return found


def pool_dress(psbt_string, entries, input_index=0):
    """Put one made-in-advance nonce per signer into a PSBT going out.

    aggregate is the "signing" key from musig_aggregates; the other two are
    ignored silently.
    """
    psbt = PSBT.from_string(psbt_string)
    scope = psbt.inputs[input_index]
    for one in entries:
        participant = bytes.fromhex(one["participant"])
        blob = bytes.fromhex(one["entry"])
        leaf = bytes.fromhex(one["leaf"]) if one.get("leaf") else None
        scope.unknown[_nonce_key(participant, bytes.fromhex(one["aggregate"]), leaf)] = \
            blob[:SIZE_PUBNONCE]
        scope.unknown[_pooled_key(participant, 0)] = blob
    return psbt.to_string()


def pool_verify(psbt_string, issued, participant, aggregate, leaf=None,
                input_index=0):
    """Prove the signer used the nonce it was given.

    A device that ignored it still produces a valid transaction. Ask this of
    the PSBT the device handed back: finalising strips the nonce.
    """
    scope = PSBT.from_string(psbt_string).inputs[input_index]
    wanted = _nonce_key(bytes.fromhex(participant), bytes.fromhex(aggregate),
                        bytes.fromhex(leaf) if leaf else None)
    published = scope.unknown.get(wanted)
    if published is None:
        raise ValueError("that signer published no nonce under %s, so there is "
                         "nothing to compare against" % aggregate[:16])
    if bytes(published) != bytes.fromhex(issued)[:SIZE_PUBNONCE]:
        raise ValueError("that signer published a different nonce than the one "
                         "it was given, so the pooled one was not used")
    return True


def dispatch(name, payload):
    """One entry point for the worker: JSON in, JSON out."""
    import json

    fn = globals().get(name)
    if not callable(fn) or name.startswith("_"):
        raise ValueError("no such coordinator function: %s" % name)
    return json.dumps(fn(*json.loads(payload)))
