# Research

The scripts the MuSig2 work was built from, kept because they are the evidence
for it rather than because anything depends on them. Nothing under `src/` or
`test/` imports any of this.

They lived outside the repository while the design was still moving. They are
here now because the coordinator they were checking against is here, and a
check kept somewhere else is a check nobody runs.

## What is worth reading

| File | What it shows |
|------|---------------|
| `bip327_reference.py` | The BIP-327 reference implementation, unmodified, used as the thing to agree with |
| `verify_core_coordinator.py` | Bitcoin Core taking every MuSig2 round, unpatched |
| `verify_core_proprietary.py` | Core preserving the proprietary fields the sealed and pooled nonces travel in |
| `ranged_2of3.py`, `ranged_2of3_ours.py` | A 2-of-3 built the standard way and our way, side by side |
| `device_half.py` | The device's half of a signing, away from any device |
| `foreign_partial_sig.py` | A partial signature made elsewhere, checked here |
| `dump_musig_fields.py` | Prints the MuSig2 fields of a PSBT |

## What will not run as it stands

Several of these hold absolute paths from the machine they were written on
(`/home/rob/apps/_scratch/...`), and some point at trees that no longer exist.
They are kept as a record of what was checked, not as a suite. Read them before
running them.

`fund_mainnet.py` spends real money on mainnet. It reads the seed to spend from
a `*.local.json` file outside the repository, which is not here and never will
be.
