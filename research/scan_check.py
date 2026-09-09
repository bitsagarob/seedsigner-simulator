#!/usr/bin/env python3
"""Independent receive-side check: setavenger's blindbit-cli (Rust) against our
BlindBit Oracle, with the demo recipient's scan key. Neither knows about MuSig2,
PSBTs or shares; if the payment is found here, the chain alone is enough.

  ./scan_check.py <block height> [<end height>]
"""
import json, os, subprocess, sys
sys.path.insert(0, "/home/rob/apps/_scratch/musig2-app/src")
from embit import bip39
from seedsigner.helpers import silent_payments as sp_keys
from seedsigner.models.settings_definition import SettingsConstants

seeds = json.load(open("/home/rob/apps/bitsaga/research/seedsigner-sp/MUSIG2_SP.local.json"))["seeds"]
scan, spend = sp_keys.derive_keys(bip39.mnemonic_to_seed(seeds["R"]["mnemonic"]), SettingsConstants.MAINNET)
start = sys.argv[1]
end = sys.argv[2] if len(sys.argv) > 2 else start
state = "/home/rob/.cache/tmp/musig-blindbit-state.json"
if os.path.exists(state):
    os.remove(state)
# The scan secret goes through the environment, not argv, so it is not in `ps`
os.environ["SCAN_SECRET"] = scan.secret.hex()
cmd = ["/bin/sh", "-c", 'exec /home/rob/apps/_scratch/blindbit-rs/target/release/blindbit-cli scan --scan-secret "$SCAN_SECRET" "$@"', "sh",
       "--spend-pubkey", spend.get_public_key().sec().hex(),
       "--start-height", start, "--end-height", end,
       "--p2p-node-addr", "127.0.0.1:8333",
       "--oracle-url", "http://127.0.0.1:8011",
       "--network", "bitcoin", "--state-file", state, "--log-level", "info"]
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout[-3000:])
print(r.stderr[-3000:])
print("exit", r.returncode)
