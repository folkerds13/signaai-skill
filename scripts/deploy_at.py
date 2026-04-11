#!/usr/bin/env python3
"""
SignaAI AT Escrow Deployer

Deploys the SignaAIEscrow AT smart contract to Signum.
The AT holds funds trustlessly — no operator, no trust required.

Usage:
  python3 deploy_at.py deploy <payer_passphrase> <worker_address> <deadline_minutes> <preimage_hex>
  python3 deploy_at.py gen-preimage
  python3 deploy_at.py info <at_address>

Workflow:
  1. Generate a preimage:       python3 deploy_at.py gen-preimage
  2. Deploy the escrow AT:      python3 deploy_at.py deploy "<passphrase>" <worker> 1440 <preimage_hex>
  3. Fund the AT:               python3 wallet.py send "<passphrase>" <at_address> <amount>
  4. Share preimage with worker after verifying task is complete
  5. Worker submits preimage:   python3 deploy_at.py submit "<worker_passphrase>" <at_address> <preimage_hex>
  6. AT auto-releases payment — no operator needed
"""
import sys
import os
import struct
import hashlib
import secrets
sys.path.insert(0, os.path.dirname(__file__))
from signum_api import get_api, signa, nqt, FEE_STANDARD, FEE_MESSAGE, ok
from wallet import get_my_address, send_signa

# ── AT Bytecode (compiled from SignaAIEscrow.java via SmartJ) ─────────────────
AT_CODE_HEX = (
    "320b033504011100000012fb0000003033040307000000350001080000001e08000000072835070307000000"
    "320a033504010a0000003506030900000012470000001a1000000033100108000000320903322301331601"
    "000000003317010100000033180102000000331901030000003505020b000000100b000000110b0000001e"
    "0b0000000b1a9700000033160104000000320304133500030b000000100b000000110c000000030d000000"
    "200c000000060000000f040d000000100d000000110b0000001e0b0000000b1afa0000003500040b000000"
    "100b0000001011000000110b000000110c0000003316010b0000003302040c000000133500030b000000"
    "100b000000110b0000003706040d0000000b00000005000000100d000000110600000013"
)
AT_CODE_HEX = AT_CODE_HEX.replace("\n", "").replace(" ", "")

AT_DPAGES   = 1
AT_CSPAGES  = 1
AT_USPAGES  = 1
AT_MIN_ACTIVATION_NQT = 100_000_000  # 1 SIGNA


# ── Helpers ───────────────────────────────────────────────────────────────────

def encode_long_le(value):
    """Encode a 64-bit signed integer as 8 bytes little-endian hex."""
    return struct.pack('<q', value).hex()


def sha256_hex(data_hex):
    """SHA256 of hex-encoded bytes, returns hex digest."""
    return hashlib.sha256(bytes.fromhex(data_hex)).hexdigest()


def sha256_str(text):
    """SHA256 of a UTF-8 string, returns hex digest."""
    return hashlib.sha256(text.encode()).hexdigest()


def build_data_field(preimage_hex, worker_account_id, deadline_minutes):
    """
    Build the AT data initialization field.

    Memory layout (little-endian 64-bit longs):
      [0-3] hashedKey  — SHA256(preimage), split into 4 x 8 bytes
      [4]   worker     — worker account ID (numeric)
      [5]   deadlineMinutes

    Returns hex string for the 'data' parameter in createATProgram.
    """
    # SHA256 the preimage → 32 bytes = 4 x 8-byte longs
    hash_bytes = hashlib.sha256(bytes.fromhex(preimage_hex)).digest()

    # Split 32-byte hash into 4 little-endian 64-bit longs
    hash_parts = []
    for i in range(4):
        chunk = hash_bytes[i*8:(i+1)*8]
        # Read as little-endian unsigned, store as signed (AT uses signed longs)
        val = struct.unpack('<q', chunk)[0]
        hash_parts.append(encode_long_le(val))

    worker_encoded  = encode_long_le(worker_account_id)
    deadline_encoded = encode_long_le(deadline_minutes)

    return "".join(hash_parts) + worker_encoded + deadline_encoded


def encode_preimage_message(preimage_hex):
    """
    Encode the preimage as a 32-byte message for submission to the AT.
    Signum messages are sent as hex strings of up to 32 bytes.
    Returns the hex string (padded to 64 hex chars = 32 bytes).
    """
    padded = preimage_hex.ljust(64, '0')[:64]
    return padded


# ── Core Functions ────────────────────────────────────────────────────────────

def gen_preimage():
    """Generate a cryptographically secure random preimage."""
    preimage = secrets.token_hex(32)  # 32 bytes = 256 bits
    hashed   = sha256_hex(preimage)
    return preimage, hashed


def deploy_at(payer_passphrase, worker_address, deadline_minutes, preimage_hex, network=None):
    """
    Deploy the SignaAIEscrow AT contract on Signum.

    Returns AT address and transaction ID.
    """
    api = get_api(network)

    # Get worker numeric account ID
    worker_info = api.get("getAccount", account=worker_address)
    if not ok(worker_info):
        return None, f"Could not find worker account: {worker_info.get('error')}"
    worker_id = int(worker_info.get("account", 0))

    # Build data field
    data_hex = build_data_field(preimage_hex, worker_id, deadline_minutes)

    # AT deployment fee minimum is 0.5 SIGNA on mainnet
    deploy_fee_nqt = 50_000_000  # 0.5 SIGNA

    print(f"  Deploying AT contract...")
    print(f"  Worker:           {worker_address}")
    print(f"  Deadline:         {deadline_minutes} minutes ({deadline_minutes//60}h)")
    print(f"  Hash of preimage: {sha256_hex(preimage_hex)[:16]}...")

    result = api.post(
        "createATProgram",
        secretPhrase=payer_passphrase,
        name="SignaAIEscrow",
        description="SignaAI agent task escrow - hash preimage release",
        code=AT_CODE_HEX,
        data=data_hex,
        dpages=AT_DPAGES,
        cspages=AT_CSPAGES,
        uspages=AT_USPAGES,
        minActivationAmountNQT=AT_MIN_ACTIVATION_NQT,
        feeNQT=deploy_fee_nqt,
    )

    if not ok(result):
        return None, f"Deployment failed: {result.get('error')}"

    tx_id = result.get("transaction")

    # AT address is assigned after TX confirms — query account ATs to find it
    at_address = None
    payer_address, _ = get_my_address(payer_passphrase, network)
    if payer_address:
        ats = api.get("getAccountATs", account=payer_address)
        for at in (ats.get("ats") or []):
            if at.get("name") == "SignaAIEscrow":
                at_address = at.get("atRS")
                break

    return {
        "tx_id": tx_id,
        "at_address": at_address,
        "worker": worker_address,
        "deadline_minutes": deadline_minutes,
        "preimage_hash": sha256_hex(preimage_hex),
    }, None


def submit_preimage(worker_passphrase, at_address, preimage_hex, network=None):
    """
    Worker submits the preimage to the AT contract to claim payment.
    The AT verifies SHA256(preimage) == stored hash → releases funds.
    """
    api = get_api(network)

    worker_address, err = get_my_address(worker_passphrase, network)
    if err:
        return None, err

    # Send preimage as a hex message to the AT
    # Pad/truncate to 32 bytes (64 hex chars)
    message = preimage_hex.ljust(64, '0')[:64]

    print(f"  Submitting preimage to AT {at_address}...")

    result = api.post(
        "sendMoney",
        secretPhrase=worker_passphrase,
        recipient=at_address,
        amountNQT=AT_MIN_ACTIVATION_NQT,  # must send at least activation amount
        message=message,
        messageIsText="false",
        feeNQT=FEE_MESSAGE,
    )

    if not ok(result):
        return None, f"Submission failed: {result.get('error')}"

    return {
        "tx_id": result.get("transaction"),
        "at_address": at_address,
        "preimage": preimage_hex,
        "note": "AT will verify on next block — if hash matches, funds released automatically",
    }, None


def get_at_info(at_address, network=None):
    """Get AT contract state and balance."""
    api = get_api(network)

    # getAT requires numeric account ID, not RS address — resolve it first
    account = api.get("getAccount", account=at_address)
    if not ok(account):
        return None, account.get("error", "Could not find AT account")

    at_numeric_id = account.get("account")
    bal_signa = signa(account.get("balanceNQT", 0))

    result = api.get("getAT", at=at_numeric_id)
    if not ok(result):
        return None, result.get("error")

    return {
        "address": result.get("atRS", at_address),
        "name": result.get("name"),
        "description": result.get("description"),
        "balance": bal_signa,
        "finished": result.get("finished", False),
        "frozen": result.get("frozen", False),
        "creator": result.get("creatorRS"),
    }, None


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="SignaAI AT Escrow Deployer")
    parser.add_argument("--network", default=os.environ.get("SIGNUM_NETWORK", "testnet"),
                        choices=["mainnet", "testnet"])
    sub = parser.add_subparsers(dest="cmd")

    # gen-preimage
    sub.add_parser("gen-preimage", help="Generate a secure random preimage + its SHA256 hash")

    # deploy
    p = sub.add_parser("deploy", help="Deploy the escrow AT contract")
    p.add_argument("payer_passphrase")
    p.add_argument("worker_address", help="Worker agent's Signum address")
    p.add_argument("deadline_minutes", type=int, help="Minutes until refund (e.g. 1440 = 24h)")
    p.add_argument("preimage_hex", help="32-byte hex preimage (from gen-preimage)")

    # submit
    p = sub.add_parser("submit", help="Worker submits preimage to claim payment")
    p.add_argument("worker_passphrase")
    p.add_argument("at_address", help="AT contract address")
    p.add_argument("preimage_hex", help="The preimage revealed by payer")

    # info
    p = sub.add_parser("info", help="Get AT contract state and balance")
    p.add_argument("at_address")

    args = parser.parse_args()
    os.environ["SIGNUM_NETWORK"] = args.network

    if args.cmd == "gen-preimage":
        preimage, hashed = gen_preimage()
        print(f"\nPreimage (keep secret until work is verified):")
        print(f"  {preimage}")
        print(f"\nSHA256(preimage) — stored in AT:")
        print(f"  {hashed}")
        print(f"\nStore the preimage securely. Reveal it to the worker only after verifying their work.")

    elif args.cmd == "deploy":
        print(f"Deploying SignaAI Escrow AT on {args.network}...")
        result, err = deploy_at(
            args.payer_passphrase, args.worker_address,
            args.deadline_minutes, args.preimage_hex, args.network
        )
        if err:
            print(f"Error: {err}")
        else:
            print(f"\n✓ AT contract deployed")
            print(f"  AT address:   {result['at_address']}")
            print(f"  Deploy TX:    {result['tx_id']}")
            print(f"  Worker:       {result['worker']}")
            print(f"  Deadline:     {result['deadline_minutes']} min ({result['deadline_minutes']//60}h)")
            print(f"  Preimage hash:{result['preimage_hash'][:32]}...")
            print(f"\n  Next: fund the AT by sending SIGNA to {result['at_address']}")
            print(f"  python3 wallet.py --network {args.network} send \"<passphrase>\" {result['at_address']} <amount>")

    elif args.cmd == "submit":
        print(f"Submitting preimage to AT {args.at_address}...")
        result, err = submit_preimage(
            args.worker_passphrase, args.at_address,
            args.preimage_hex, args.network
        )
        if err:
            print(f"Error: {err}")
        else:
            print(f"\n✓ Preimage submitted")
            print(f"  TX:   {result['tx_id']}")
            print(f"  {result['note']}")

    elif args.cmd == "info":
        result, err = get_at_info(args.at_address, args.network)
        if err:
            print(f"Error: {err}")
        else:
            status = "FINISHED" if result['finished'] else ("FROZEN" if result['frozen'] else "ACTIVE")
            print(f"\nAT Contract: {result['address']}")
            print(f"  Name:     {result['name']}")
            print(f"  Creator:  {result['creator']}")
            print(f"  Balance:  {result['balance']:,.4f} SIGNA")
            print(f"  Status:   {status}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
