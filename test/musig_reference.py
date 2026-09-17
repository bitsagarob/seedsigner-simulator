import base64
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path

from mainnet_reference import N, P, point_add, point_mul, serialize_point


BIP328_CHAIN_CODE = bytes.fromhex(
    "868087ca02a6f974c4598924c36b57762d32cb45717167e300622c7167e38965")
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def tagged_hash(tag: str, data: bytes) -> bytes:
    prefix = sha256(tag.encode("ascii"))
    return sha256(prefix + prefix + data)


def lift_x(key: bytes):
    if len(key) != 32:
        raise ValueError("expected a 32-byte x-only key")
    x = int.from_bytes(key, "big")
    if x >= P:
        raise ValueError("x outside field")
    square = (x ** 3 + 7) % P
    y = pow(square, (P + 1) // 4, P)
    if y * y % P != square:
        raise ValueError("key not on curve")
    return x, y if y % 2 == 0 else P - y


def compressed_point(key: bytes):
    if len(key) != 33 or key[0] not in (2, 3):
        raise ValueError("expected a compressed public key")
    x, y = lift_x(key[1:])
    return x, y if key[0] == 2 else P - y


def verify_bip340(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    try:
        point = lift_x(public_key)
    except ValueError:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if r >= P or s >= N:
        return False
    challenge = int.from_bytes(tagged_hash(
        "BIP0340/challenge", signature[:32] + public_key + message), "big") % N
    candidate = point_add(point_mul(s), point_mul((-challenge) % N, point))
    return candidate is not None and candidate[1] % 2 == 0 and candidate[0] == r


def parse_xpub(text: str):
    if not text or len(text) > 112:
        raise ValueError("invalid extended public key length")
    number = 0
    for char in text:
        if char not in _B58:
            raise ValueError("invalid base58 character")
        number = number * 58 + _B58.index(char)
    data = b"\x00" * (len(text) - len(text.lstrip("1")))
    data += number.to_bytes((number.bit_length() + 7) // 8, "big")
    if len(data) != 82 or sha256(sha256(data[:-4]))[:4] != data[-4:]:
        raise ValueError("invalid extended public key checksum or length")
    payload = data[:-4]
    if payload[:4] not in (bytes.fromhex("0488b21e"), bytes.fromhex("043587cf")):
        raise ValueError("expected xpub or tpub version")
    if payload[4] == 0 and payload[5:13] != b"\x00" * 8:
        raise ValueError("invalid master extended public key metadata")
    return compressed_point(payload[45:]), payload[13:45]


def derive_public_child(point, chain_code: bytes, index: int):
    if not 0 <= index < 0x80000000 or len(chain_code) != 32:
        raise ValueError("expected unhardened child index and 32-byte chain code")
    digest = hmac.new(chain_code, serialize_point(point) + index.to_bytes(4, "big"),
                      hashlib.sha512).digest()
    tweak = int.from_bytes(digest[:32], "big")
    if tweak >= N:
        raise ValueError("invalid BIP32 child tweak; next index required")
    child = point_add(point, point_mul(tweak))
    if child is None:
        raise ValueError("BIP32 child is infinity; next index required")
    return child, digest[32:]


def aggregate_keys(public_keys, sort_keys=True):
    keys = sorted(public_keys) if sort_keys else list(public_keys)
    if not keys:
        raise ValueError("empty key aggregate")
    second = next((key for key in keys if key != keys[0]), None)
    key_hash = tagged_hash("KeyAgg list", b"".join(keys))
    aggregate = None
    for key in keys:
        point = compressed_point(key)
        coefficient = (1 if key == second else int.from_bytes(
            tagged_hash("KeyAgg coefficient", key_hash + key), "big") % N)
        aggregate = point_add(aggregate, point_mul(coefficient, point))
    if aggregate is None:
        raise ValueError("aggregate key is infinity")
    return aggregate


def compact_size(value: int) -> bytes:
    if not 0 <= value < 1 << 64:
        raise ValueError("CompactSize out of range")
    if value < 253:
        return bytes([value])
    for prefix, size in ((253, 2), (254, 4), (255, 8)):
        if value < 1 << (8 * size):
            return bytes([prefix]) + value.to_bytes(size, "little")


def serialize_bytes(data: bytes) -> bytes:
    return compact_size(len(data)) + data


def tapleaf_hash(script: bytes, version=0xC0) -> bytes:
    return tagged_hash("TapLeaf", bytes([version]) + serialize_bytes(script))


def tapbranch_hash(left: bytes, right: bytes) -> bytes:
    return tagged_hash("TapBranch", b"".join(sorted((left, right))))


def tap_tweak(internal_key: bytes, merkle_root=b""):
    if len(merkle_root) not in (0, 32):
        raise ValueError("invalid Taproot Merkle root length")
    tweak = int.from_bytes(tagged_hash("TapTweak", internal_key + merkle_root), "big")
    if tweak >= N:
        raise ValueError("TapTweak outside scalar range")
    output = point_add(lift_x(internal_key), point_mul(tweak))
    if output is None:
        raise ValueError("Taproot output is infinity")
    return output


def taproot_address(output_key: bytes, hrp="tb") -> str:
    lift_x(output_key)
    if hrp not in ("bc", "tb", "bcrt"):
        raise ValueError("unsupported address network")
    number = int.from_bytes(output_key, "big") << 4
    data = [1] + [(number >> shift) & 31 for shift in range(255, -1, -5)]
    expanded = [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]
    checksum = 1
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    for value in expanded + data + [0] * 6:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for bit, generator in enumerate(generators):
            if (top >> bit) & 1:
                checksum ^= generator
    checksum ^= 0x2BC830A3
    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    values = data + [(checksum >> shift) & 31 for shift in range(25, -1, -5)]
    return hrp + "1" + "".join(alphabet[value] for value in values)


def derive_descriptor(descriptor: str, branch=0, index=0, hrp="tb"):
    key = r"(?:\[[0-9a-fA-F]{8}(?:/[0-9]+[h']?)*\])?([xt]pub[1-9A-HJ-NP-Za-km-z]+)"
    pair = r"musig\(" + key + "," + key + r"\)/<0;1>/\*"
    match = re.fullmatch(r"tr\(" + pair + r",\{pk\(" + pair + r"\),pk\(" + pair + r"\)\}\)",
                         descriptor)
    if match is None:
        raise ValueError("expected tr(musig(A,B)/<0;1>/*,{pk(musig(A,C)/<0;1>/*),pk(musig(B,C)/<0;1>/*)}) without checksum")
    if branch not in (0, 1) or not 0 <= index < 0x80000000:
        raise ValueError("invalid descriptor branch or index")
    keys = match.groups()
    pairs = [frozenset(keys[i:i + 2]) for i in range(0, 6, 2)]
    if len(set(keys)) != 3 or len(set(pairs)) != 3 or any(len(pair) != 2 for pair in pairs):
        raise ValueError("expected three distinct two-key pairs over three account keys")
    points = {text: parse_xpub(text)[0] for text in keys}
    if len(set(points.values())) != 3:
        raise ValueError("expected three distinct account public keys")
    derived = []
    for offset in range(0, 6, 2):
        point = aggregate_keys([serialize_point(points[text]) for text in keys[offset:offset + 2]])
        chain_code = BIP328_CHAIN_CODE
        for child in (branch, index):
            point, chain_code = derive_public_child(point, chain_code, child)
        derived.append(serialize_point(point)[1:])
    scripts = [b"\x20" + key + b"\xac" for key in derived[1:]]
    leaves = [tapleaf_hash(script) for script in scripts]
    root = tapbranch_hash(*leaves)
    output = serialize_point(tap_tweak(derived[0], root))
    return {"internal_key": derived[0], "leaf_scripts": scripts, "leaf_hashes": leaves,
            "merkle_root": root, "output_key": output[1:], "aggregate": output,
            "script_pubkey": b"\x51\x20" + output[1:],
            "address": taproot_address(output[1:], hrp)}


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("truncated serialization")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def integer(self, size):
        return int.from_bytes(self.take(size), "little")

    def compact(self):
        first = self.integer(1)
        if first < 253:
            return first
        size = {253: 2, 254: 4, 255: 8}[first]
        value = self.integer(size)
        if value < {253: 253, 254: 65536, 255: 4294967296}[first]:
            raise ValueError("noncanonical CompactSize")
        return value

    def vector(self):
        return self.take(self.compact())

    def finish(self):
        if self.offset != len(self.data):
            raise ValueError("trailing serialization data")


def parse_transaction(data: bytes):
    reader = Reader(data)
    version = reader.integer(4)
    count = reader.compact()
    witness = count == 0
    if witness:
        if reader.take(1) != b"\x01":
            raise ValueError("unsupported transaction witness flag")
        count = reader.compact()
    if not count:
        raise ValueError("transaction has no inputs")
    inputs = []
    for _ in range(count):
        inputs.append({"outpoint": reader.take(36), "script_sig": reader.vector(),
                       "sequence": reader.integer(4), "witness": []})
    outputs = [(reader.integer(8), reader.vector()) for _ in range(reader.compact())]
    if witness:
        for txin in inputs:
            txin["witness"] = [reader.vector() for _ in range(reader.compact())]
        if not any(txin["witness"] for txin in inputs):
            raise ValueError("superfluous witness serialization")
    locktime = reader.integer(4)
    reader.finish()
    return {"version": version, "inputs": inputs, "outputs": outputs, "locktime": locktime}


def serialize_output(output) -> bytes:
    amount, script = output
    return amount.to_bytes(8, "little") + serialize_bytes(script)


def serialize_transaction(tx, include_witness=False) -> bytes:
    inputs = tx["inputs"]
    witness = include_witness and any(txin["witness"] for txin in inputs)
    result = tx["version"].to_bytes(4, "little") + (b"\x00\x01" if witness else b"")
    result += compact_size(len(inputs))
    for txin in inputs:
        result += txin["outpoint"] + serialize_bytes(txin["script_sig"])
        result += txin["sequence"].to_bytes(4, "little")
    result += compact_size(len(tx["outputs"]))
    result += b"".join(serialize_output(output) for output in tx["outputs"])
    if witness:
        for txin in inputs:
            result += compact_size(len(txin["witness"]))
            result += b"".join(serialize_bytes(item) for item in txin["witness"])
    return result + tx["locktime"].to_bytes(4, "little")


def bip341_sighash(tx, spent_outputs, input_index=0, hash_type=0, annex=None) -> bytes:
    inputs = tx["inputs"]
    if hash_type not in (0, 1, 2, 3, 0x81, 0x82, 0x83):
        raise ValueError("undefined Taproot sighash type")
    if len(spent_outputs) != len(inputs) or not 0 <= input_index < len(inputs):
        raise ValueError("spent outputs or input index mismatch")
    if annex is not None and not annex.startswith(b"\x50"):
        raise ValueError("invalid annex prefix")
    mode = hash_type & 3
    anyone = bool(hash_type & 0x80)
    message = bytes([hash_type]) + tx["version"].to_bytes(4, "little")
    message += tx["locktime"].to_bytes(4, "little")
    if not anyone:
        message += sha256(b"".join(txin["outpoint"] for txin in inputs))
        message += sha256(b"".join(amount.to_bytes(8, "little") for amount, _ in spent_outputs))
        message += sha256(b"".join(serialize_bytes(script) for _, script in spent_outputs))
        message += sha256(b"".join(txin["sequence"].to_bytes(4, "little") for txin in inputs))
    if mode not in (2, 3):
        message += sha256(b"".join(serialize_output(output) for output in tx["outputs"]))
    message += bytes([int(annex is not None)])
    if anyone:
        txin = inputs[input_index]
        message += txin["outpoint"] + serialize_output(spent_outputs[input_index])
        message += txin["sequence"].to_bytes(4, "little")
    else:
        message += input_index.to_bytes(4, "little")
    if annex is not None:
        message += sha256(serialize_bytes(annex))
    if mode == 3:
        if input_index >= len(tx["outputs"]):
            raise ValueError("SIGHASH_SINGLE without corresponding output")
        message += sha256(serialize_output(tx["outputs"][input_index]))
    return tagged_hash("TapSighash", b"\x00" + message)


def verify_key_path(tx, spent_outputs, input_index=0) -> bool:
    try:
        witness = list(tx["inputs"][input_index]["witness"])
        annex = witness.pop() if len(witness) >= 2 and witness[-1].startswith(b"\x50") else None
        if len(witness) != 1 or tx["inputs"][input_index]["script_sig"]:
            return False
        signature = witness[0]
        if len(signature) not in (64, 65) or (len(signature) == 65 and signature[-1] == 0):
            return False
        script = spent_outputs[input_index][1]
        if len(script) != 34 or script[:2] != b"\x51\x20":
            return False
        digest = bip341_sighash(tx, spent_outputs, input_index,
                               signature[64] if len(signature) == 65 else 0, annex)
        return verify_bip340(script[2:], digest, signature[:64])
    except (ValueError, IndexError, OverflowError):
        return False


def read_psbt(data: bytes):
    reader = Reader(data)
    if reader.take(5) != b"psbt\xff":
        raise ValueError("invalid PSBT magic")

    def read_map():
        records = {}
        while True:
            key = reader.vector()
            if not key:
                return records
            if key in records:
                raise ValueError("duplicate PSBT key")
            records[key] = reader.vector()

    globals_ = read_map()
    if b"\x00" not in globals_ or globals_.get(b"\xfb", b"\x00" * 4) != b"\x00" * 4:
        raise ValueError("expected PSBT v0 unsigned transaction")
    tx = parse_transaction(globals_[b"\x00"])
    if any(txin["script_sig"] or txin["witness"] for txin in tx["inputs"]):
        raise ValueError("PSBT global transaction is not unsigned")
    inputs = [read_map() for _ in tx["inputs"]]
    outputs = [read_map() for _ in tx["outputs"]]
    reader.finish()
    return globals_[b"\x00"], inputs, outputs


def verify_artifact(artifact):
    derived = derive_descriptor(artifact["descriptor"], artifact["branch"], artifact["index"])
    script = derived["script_pubkey"]
    utxo = artifact["utxo"]
    txid = bytes.fromhex(utxo["txid"])
    if len(txid) != 32 or not 0 <= artifact["amount"] <= utxo["value"] <= 21000000 * 100000000:
        raise ValueError("invalid transaction fields")
    expected = {"version": 2, "locktime": 0,
                "inputs": [{"outpoint": txid[::-1] + utxo["vout"].to_bytes(4, "little"),
                            "script_sig": b"", "sequence": 0xFFFFFFFD, "witness": []}],
                "outputs": [(artifact["amount"], script)]}
    if bytes.fromhex(artifact["destination"]) != script:
        raise ValueError("destination differs from independently derived wallet output")
    if "aggregate" in artifact and bytes.fromhex(artifact["aggregate"]) != derived["aggregate"]:
        raise ValueError("reported aggregate differs from independently derived output key")
    tx = parse_transaction(bytes.fromhex(artifact["txhex"]))
    unsigned = serialize_transaction(expected)
    if serialize_transaction(tx) != unsigned:
        raise ValueError("final transaction differs from intended spend")
    psbt_tx, inputs, _ = read_psbt(base64.b64decode(artifact["psbt"], validate=True))
    spent_outputs = [(utxo["value"], script)]
    if psbt_tx != unsigned or inputs[0].get(b"\x01") != serialize_output(spent_outputs[0]):
        raise ValueError("PSBT transaction or witness UTXO differs from intended spend")
    witness = tx["inputs"][0]["witness"]
    if len(witness) != 1 or len(witness[0]) != 64:
        raise ValueError("expected key-path SIGHASH_DEFAULT signature without annex")
    encoded_witness = compact_size(1) + serialize_bytes(witness[0])
    if inputs[0].get(b"\x08") != encoded_witness:
        raise ValueError("PSBT final witness differs from transaction")
    if not verify_key_path(tx, spent_outputs):
        raise ValueError("independent BIP341/BIP340 verification failed")
    return {**derived, "sighash": bip341_sighash(expected, spent_outputs),
            "signature": witness[0], "txid": sha256(sha256(unsigned))[::-1].hex(),
            "fee": utxo["value"] - artifact["amount"]}


def check_artifact(artifact):
    result = verify_artifact(artifact)
    tx = parse_transaction(bytes.fromhex(artifact["txhex"]))
    spent = [(artifact["utxo"]["value"], result["script_pubkey"])]
    signature = result["signature"]
    key = result["output_key"]
    bad_signature = signature[:-1] + bytes([signature[-1] ^ 1])
    output_amount, script = tx["outputs"][0]
    changed_tx = {**tx, "outputs": [(output_amount + 1, script)]}
    wrong_key = serialize_point(point_add(lift_x(key), point_mul(1)))[1:]
    return [
        ("artifact independently derives and verifies", True, result["address"]),
        ("changed signature rejected", not verify_bip340(key, result["sighash"], bad_signature), ""),
        ("changed output amount rejected", not verify_key_path(changed_tx, spent), ""),
        ("changed input amount rejected", not verify_key_path(tx, [(spent[0][0] + 1, script)]), ""),
        ("changed public key rejected", not verify_bip340(wrong_key, result["sighash"], signature), ""),
    ]


BIP340_VECTORS = [
    (0, "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9", "00" * 32,
     "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA82152"
     "5F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0", True),
    (1, "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE33418"
     "906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A", True),
    (6, "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "FFF97BD5755EEEA420453A14355235D382F6472F8568A18B2F057A14602975563"
     "CC27944640AC607CD107AE10923D9EF7A73C643E166BE5EBEAFA34B1AC553E2", False),
    (9, "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "00" * 32 + "123DDA8328AF9C23A94C1FEECFD123BA4FB73476F0D594DCB65C6425BD186051", False),
    (12, "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F"
     "69E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B", False),
    (13, "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E177769"
     "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", False),
    (14, "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC30",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E177769"
     "69E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B", False),
    (15, "778CAA53B4393AC467774D09497A87224BF9FAB6F6E68B23086497324D6FD117", "",
     "71535DB165ECD9FBBC046E5FFAEA61186BB6AD436732FCCC25291A55895464CF60"
     "69CE26BF03466228F19A3A62DB8A649F2D560FAC652827D1AF0574E427AB63", True),
]


def check_published_vectors():
    results = []
    for index, key, message, signature, expected in BIP340_VECTORS:
        got = verify_bip340(bytes.fromhex(key), bytes.fromhex(message), bytes.fromhex(signature))
        results.append((f"BIP340 published vector {index}", got == expected, ""))
    keys = [bytes.fromhex(key) for key in (
        "03935F972DA013F80AE011890FA89B67A27B7BE6CCB24D3274D18B2D4067F261A9",
        "02F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9")]
    expected = bytes.fromhex("0354240c76b8f2999143301a99c7f721ee57eee0bce401df3afeaa9ae218c70f23")
    results.append(("BIP328 published aggregate", serialize_point(aggregate_keys(keys, False)) == expected, ""))
    results.append(("BIP328 chain code", sha256(b"MuSig2MuSig2MuSig2") == BIP328_CHAIN_CODE, ""))
    synthetic = ("xpub661MyMwAqRbcFt6tk3uaczE1y6EvM1TqXvawXcYmFEWijEM4PDBnuCXwwXEK"
                 "GEouzXE6QLLRxjatMcLLzJ5LV5Nib1BN7vJg6yp45yHHRbm")
    point, chain_code = parse_xpub(synthetic)
    results.append(("BIP328 synthetic xpub", serialize_point(point) == expected and chain_code == BIP328_CHAIN_CODE, ""))
    parent = ("xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwBZeNK1VTs"
              "fTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw")
    child = ("xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWbWMiGj7Wf"
             "5uMash7SyYq527Hqck2AxYysAA7xmALppuCkwQ")
    point, chain_code = parse_xpub(parent)
    results.append(("BIP32 public m/0'/1", derive_public_child(point, chain_code, 1) == parse_xpub(child), ""))
    internal = bytes.fromhex("d6889cb081036e0faefa3a35157ad71086b123b2b144b649798b494c300a961d")
    output = serialize_point(tap_tweak(internal))[1:]
    results.append(("BIP341 published output key", output.hex() ==
                    "53a1f6e454df1aa2776a2814a721372d6258050de330b3c6d10ee8f4e0dda343", ""))
    results.append(("BIP350 published address", taproot_address(output, "bc") ==
                    "bc1p2wsldez5mud2yam29q22wgfh9439spgduvct83k3pm50fcxa5dps59h4z5", ""))
    leaves = [tapleaf_hash(bytes.fromhex(script)) for script in (
        "2044b178d64c32c4a05cc4f4d1407268f764c940d20ce97abfd44db5c3592b72fdac",
        "07546170726f6f74")]
    root = tapbranch_hash(*leaves)
    results.append(("BIP341 published two-leaf root", root.hex() ==
                    "ab179431c28d3b68fb798957faf5497d69c883c6fb1e1cd9f81483d87bac90cc", ""))
    internal = bytes.fromhex("f9f400803e683727b14f463836e1e78e1c64417638aa066919291a225f0e8dd8")
    output = serialize_point(tap_tweak(internal, root))
    results.append(("BIP341 published script-tree tweak", output.hex() ==
                    "0377e30a5522dd9f894c3f8b8bd4c4b2cf82ca7da8a3ea6a239655c39c050ab220", ""))
    raw = bytes.fromhex(
        "02000000097de20cbff686da83a54981d2b9bab3586f4ca7e48f57f5b55963115f3b334e9c"
        "010000000000000000d7b7cab57b1393ace2d064f4d4a2cb8af6def61273e127517d44759b6dafdd99"
        "0000000000fffffffff8e1f583384333689228c5d28eac13366be082dc57441760d957275419a41842"
        "0000000000fffffffff0689180aa63b30cb162a73c6d2a38b7eeda2a83ece74310fda0843ad604853b"
        "0100000000feffffffaa5202bdf6d8ccd2ee0f0202afbbb7461d9264a25e5bfd3c5a52ee1239e0ba6c"
        "0000000000feffffff956149bdc66faa968eb2be2d2faa29718acbfe3941215893a2a3446d32acd050"
        "000000000000000000e664b9773b88c09c32cb70a2a3e4da0ced63b7ba3b22f848531bbb1d5d5f4c94"
        "010000000000000000e9aa6b8e6c9de67619e6a3924ae25696bb7b694bb677a632a74ef7eadfd4eabf"
        "0000000000ffffffffa778eb6a263dc090464cd125c466b5a99667720b1c110468831d058aa1b82af1"
        "0100000000ffffffff0200ca9a3b000000001976a91406afd46bcdfd22ef94ac122aa11f241244a37ecc88ac"
        "807840cb0000000020ac9a87f5594be208f8532db38cff670c450ed2fea8fcdefcc9a663f78bab962b0065cd1d")
    spent = [(amount, bytes.fromhex(script)) for amount, script in (
        (420000000, "512053a1f6e454df1aa2776a2814a721372d6258050de330b3c6d10ee8f4e0dda343"),
        (462000000, "5120147c9c57132f6e7ecddba9800bb0c4449251c92a1e60371ee77557b6620f3ea3"),
        (294000000, "76a914751e76e8199196d454941c45d1b3a323f1433bd688ac"),
        (504000000, "5120e4d810fd50586274face62b8a807eb9719cef49c04177cc6b76a9a4251d5450e"),
        (630000000, "512091b64d5324723a985170e4dc5a0f84c041804f2cd12660fa5dec09fc21783605"),
        (378000000, "00147dd65592d0ab2fe0d0257d571abf032cd9db93dc"),
        (672000000, "512075169f4001aa68f15bbed28b218df1d0a62cbbcf1188c6665110c293c907b831"),
        (546000000, "5120712447206d7a5238acc7ff53fbe94a3b64539ad291c7cdbc490b7577e4b17df5"),
        (588000000, "512077e30a5522dd9f894c3f8b8bd4c4b2cf82ca7da8a3ea6a239655c39c050ab220"))]
    tx = parse_transaction(raw)
    results.append(("BIP341 published transaction round trip", serialize_transaction(tx) == raw, ""))
    for index, hash_type, expected in (
        (0, 3, "2514a6272f85cfa0f45eb907fcb0d121b808ed37c6ea160a5a9046ed5526d555"),
        (1, 131, "325a644af47e8a5a2591cda0ab0723978537318f10e6a63d4eed783b96a71a4d"),
        (3, 1, "bf013ea93474aa67815b1b6cc441d23b64fa310911d991e713cd34c7f5d46669"),
        (4, 0, "4f900a0bae3f1446fd48490c2958b5a023228f01661cda3496a11da502a7f7ef"),
        (6, 2, "15f25c298eb5cdc7eb1d638dd2d45c97c4c59dcaec6679cfc16ad84f30876b85"),
        (7, 130, "cd292de50313804dabe4685e83f923d2969577191a3e1d2882220dca88cbeb10"),
        (8, 129, "cccb739eca6c13a8a89e6e5cd317ffe55669bbda23f2fd37b0f18755e008edd2")):
        got = bip341_sighash(tx, spent, index, hash_type)
        results.append((f"BIP341 published sighash type {hash_type:#04x}", got.hex() == expected, ""))
    return results


if __name__ == "__main__":
    checks = check_published_vectors()
    for argument in sys.argv[1:]:
        try:
            artifact = json.loads(Path(argument).read_text())
            checks.extend(check_artifact(artifact))
        except (ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
            checks.append((f"artifact {argument}", False, str(exc)))
    for name, passed, detail in checks:
        print(("  ok   " if passed else "  FAIL ") + name + (f"  {detail}" if detail else ""))
    raise SystemExit(0 if all(passed for _, passed, _ in checks) else 1)
