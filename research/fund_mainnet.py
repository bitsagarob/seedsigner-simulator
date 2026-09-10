#!/usr/bin/env python3
"""Pay the 2-of-3 from the single-sig demo seed. Holds a key, so it is not part of the coordinator.

  ./fund_mainnet.py <bc1p address> <sats>
"""
import json, sys, urllib.request
from embit import bip32, bip39, networks, script
from embit.transaction import Transaction, TransactionInput, TransactionOutput

MAINNET_SENDER = "$SEEDS_DIR/MAINNET_SEND.local.json"


def mempool_get(path):
    with urllib.request.urlopen("https://mempool.space/api" + path, timeout=30) as r:
        return r.read().decode()


def fund_mainnet(address, amount_sat, cfg):
    """Spend the single-sig demo seed's P2WPKH coin into the 2-of-3, signed here."""
    NET = networks.NETWORKS["main"]
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(cfg["sender_mnemonic"]), version=NET["xprv"])
    candidates = {}
    for path in (cfg["sender_receive_path"], cfg["sender_change_path"]):
        k = root.derive(path)
        addr = script.p2wpkh(k).address(NET)
        candidates[addr] = (k, path)
    utxos = []
    for addr in candidates:
        for u in json.loads(mempool_get("/address/%s/utxo" % addr)):
            if u["status"]["confirmed"]:
                utxos.append((addr, u))
    if not utxos:
        raise RuntimeError("the mainnet sender seed has no confirmed coin; ask Rob to fund")
    addr, u = max(utxos, key=lambda x: x[1]["value"])
    key, path = candidates[addr]
    fee = 300
    change = u["value"] - amount_sat - fee
    assert change > 546, "not enough for the demo plus change: %d sat" % u["value"]
    tx = Transaction(vin=[TransactionInput(bytes.fromhex(u["txid"]), u["vout"])],
                     vout=[TransactionOutput(amount_sat, script.address_to_scriptpubkey(address)),
                           TransactionOutput(change, script.p2wpkh(key))])
    spk = script.p2wpkh(key)
    h = tx.sighash_segwit(0, script.p2pkh_from_p2wpkh(spk), u["value"])
    sig = key.sign(h)
    tx.vin[0].witness = script.Witness([sig.serialize() + b"\x01", key.sec()])
    import subprocess
    txid = subprocess.run(["/usr/local/bin/bitcoin-cli", "-conf=/etc/bitcoin/bitcoin.conf", "-datadir=/var/lib/bitcoind", "sendrawtransaction", tx.serialize().hex()], capture_output=True, text=True, check=True).stdout.strip()
    print("   funding tx broadcast:", txid, "(%d sat to the 2-of-3, %d change)" % (amount_sat, change))
    return txid




if __name__ == "__main__":
    with open(MAINNET_SENDER) as f:
        cfg = json.load(f)
    print(fund_mainnet(sys.argv[1], int(sys.argv[2]), cfg))
