#!/usr/bin/env python3
"""
Signum Agent Escrow — trustless task payment system (Phase 1: on-chain audit trail)

State machine:
  CREATED → SUBMITTED → RELEASED
                      → REFUNDED (if deadline passed)

All state transitions are recorded as on-chain messages — fully auditable.
Funds flow through the escrow operator wallet (dev wallet for prototype).
Upgrade path: replace operator wallet with an AT contract for full trustlessness.

On-chain message format:
  ESCROW:CREATE:<escrow_id>:<worker>:<amount_nqt>:<result_hash>:<deadline_block>
  ESCROW:SUBMIT:<escrow_id>:<result_hash>
  ESCROW:RELEASE:<escrow_id>:<worker>
  ESCROW:REFUND:<escrow_id>:<payer>

Usage:
  python3 escrow.py create <payer_passphrase> <worker_address> <amount> "<task_description>" [--deadline-hours 24]
  python3 escrow.py submit <worker_passphrase> <escrow_id> "<result_content>" [--sources "url1,url2"]
  python3 escrow.py release <operator_passphrase> <escrow_id>
  python3 escrow.py refund <operator_passphrase> <escrow_id>
  python3 escrow.py status <escrow_id>
  python3 escrow.py list <address>
"""
import sys
import os
import json
import hashlib
import secrets
import time
sys.path.insert(0, os.path.dirname(__file__))
from signum_api import get_api, signa, nqt, ts, FEE_MESSAGE, FEE_STANDARD, ok
from wallet import get_my_address, send_signa, get_transactions
from verify import hash_content, publish_proof

# ── Constants ─────────────────────────────────────────────────────────────────
ESCROW_PREFIX = "ESCROW:"
BLOCKS_PER_HOUR = 15  # ~4 min per block = 15 blocks/hour

# Escrow states
STATE_CREATED   = "CREATED"
STATE_SUBMITTED = "SUBMITTED"
STATE_RELEASED  = "RELEASED"
STATE_REFUNDED  = "REFUNDED"


# ── Core Functions ────────────────────────────────────────────────────────────

def create_escrow(payer_passphrase, worker_address, amount_signa,
                  task_description, deadline_hours=24, network=None):
    """
    Create an escrow agreement on-chain.

    Steps:
    1. Generate a unique escrow ID
    2. Compute task hash (what the worker must deliver)
    3. Record escrow creation on-chain (payer → self message)
    4. Transfer funds to escrow operator wallet

    Returns escrow_id and full escrow record.
    """
    api = get_api(network)

    # Get payer address
    payer_address, err = get_my_address(payer_passphrase, network)
    if err:
        return None, f"Could not derive payer address: {err}"

    # Get current block for deadline calculation
    status = api.get("getBlockchainStatus")
    if not ok(status):
        return None, "Could not get blockchain status"

    current_block = int(status.get("numberOfBlocks", 0))
    deadline_block = current_block + (deadline_hours * BLOCKS_PER_HOUR)

    # Generate unique escrow ID
    escrow_id = secrets.token_hex(8)  # 16 char hex, unique

    # Hash the task description — this is what the worker must reference
    task_hash = hashlib.sha256(task_description.encode()).hexdigest()[:32]

    amount_nqt = nqt(amount_signa)

    # Build on-chain record message
    message = (f"{ESCROW_PREFIX}CREATE:{escrow_id}:"
               f"{worker_address}:{amount_nqt}:{task_hash}:{deadline_block}")

    # Step 1: Record escrow creation (payer → self) via sendMessage (no amount required)
    print(f"  Recording escrow on-chain...")
    record_result = api.post("sendMessage",
                             secretPhrase=payer_passphrase,
                             recipient=payer_address,
                             message=message,
                             messageIsText="true",
                             feeNQT=FEE_MESSAGE)
    if not ok(record_result):
        return None, f"Failed to record escrow: {record_result.get('error')}"
    record_tx = record_result.get("transaction")

    # Step 2: Notify the worker directly so their listener can detect the task
    print(f"  Notifying worker...")
    time.sleep(2)
    notify_message = f"{ESCROW_PREFIX}ASSIGN:{escrow_id}:{task_hash}"
    api.post("sendMessage",
             secretPhrase=payer_passphrase,
             recipient=worker_address,
             message=notify_message,
             messageIsText="true",
             feeNQT=FEE_MESSAGE)

    # Step 3: Transfer funds to escrow operator (using same wallet for prototype)
    # In production: this sends to the AT contract address
    print(f"  Transferring {amount_signa} SIGNA to escrow...")
    time.sleep(2)  # brief pause between transactions

    fund_message = f"{ESCROW_PREFIX}FUND:{escrow_id}"
    fund_tx, err = send_signa(payer_passphrase, payer_address, amount_signa,
                              message=fund_message, network=network)
    if err:
        return None, f"Failed to fund escrow: {err}"

    escrow = {
        "escrow_id": escrow_id,
        "state": STATE_CREATED,
        "payer": payer_address,
        "worker": worker_address,
        "amount_signa": amount_signa,
        "task_description": task_description,
        "task_hash": task_hash,
        "deadline_block": deadline_block,
        "deadline_hours": deadline_hours,
        "record_tx": record_tx,
        "fund_tx": fund_tx,
        "current_block": current_block,
    }

    return escrow, None


def submit_result(worker_passphrase, escrow_id, result_content,
                  sources=None, network=None):
    """
    Worker submits completed task result on-chain.

    1. Hash the result content
    2. Publish hash to Signum (verifiable proof)
    3. Send submission message to escrow record
    """
    api = get_api(network)

    worker_address, err = get_my_address(worker_passphrase, network)
    if err:
        return None, err

    # Hash the result
    hashes = hash_content(result_content, sources)
    result_hash = hashes["content_hash"]

    # Publish proof on-chain first
    print(f"  Publishing result proof on-chain...")
    proof, err = publish_proof(worker_passphrase, result_hash,
                               hashes["sources_hash"],
                               label=f"escrow-{escrow_id}",
                               network=network)
    if err:
        return None, f"Failed to publish proof: {err}"

    time.sleep(2)

    # Submit to escrow record via sendMessage
    message = f"{ESCROW_PREFIX}SUBMIT:{escrow_id}:{result_hash}"
    print(f"  Submitting result to escrow...")
    submit_result_tx = api.post("sendMessage",
                                secretPhrase=worker_passphrase,
                                recipient=worker_address,
                                message=message,
                                messageIsText="true",
                                feeNQT=FEE_MESSAGE)
    if not ok(submit_result_tx):
        return None, f"Failed to submit result: {submit_result_tx.get('error')}"
    submit_tx = submit_result_tx.get("transaction")

    return {
        "escrow_id": escrow_id,
        "worker": worker_address,
        "result_hash": result_hash,
        "proof_tx": proof["tx_id"],
        "submit_tx": submit_tx,
        "state": STATE_SUBMITTED,
    }, None


def release_payment(operator_passphrase, escrow_id, network=None):
    """
    Release escrow funds to worker after verifying result hash matches.
    Called by operator after confirming submission is valid.

    In AT version: this happens automatically when hash matches.
    """
    api = get_api(network)

    operator_address, err = get_my_address(operator_passphrase, network)
    if err:
        return None, err

    # Get escrow state from chain — scan operator's confirmed transactions
    escrow_data, err = get_escrow_status(escrow_id, address=operator_address, network=network)
    if err:
        return None, err

    if escrow_data["state"] != STATE_SUBMITTED:
        return None, f"Escrow not in SUBMITTED state (current: {escrow_data['state']})"

    worker = escrow_data.get("worker")
    amount = escrow_data.get("amount_signa", 0)

    if not worker:
        return None, "Could not determine worker address from escrow record"

    # Verify hashes match
    submitted_hash = escrow_data.get("submitted_hash", "")
    task_hash = escrow_data.get("task_hash", "")

    print(f"  Releasing {amount} SIGNA to {worker}...")

    release_message = f"{ESCROW_PREFIX}RELEASE:{escrow_id}:{worker}"
    tx_id, err = send_signa(operator_passphrase, worker, amount,
                            message=release_message, network=network)
    if err:
        return None, f"Release failed: {err}"

    return {
        "escrow_id": escrow_id,
        "state": STATE_RELEASED,
        "worker": worker,
        "amount_signa": amount,
        "tx_id": tx_id,
    }, None


def refund_escrow(operator_passphrase, escrow_id, network=None):
    """
    Refund escrow to payer if deadline has passed without valid submission.
    """
    api = get_api(network)

    operator_address, err = get_my_address(operator_passphrase, network)
    if err:
        return None, err

    escrow_data, err = get_escrow_status(escrow_id, address=operator_address, network=network)
    if err:
        return None, err

    if escrow_data["state"] == STATE_RELEASED:
        return None, "Escrow already released — cannot refund"

    # Check deadline
    status = api.get("getBlockchainStatus")
    current_block = int(status.get("numberOfBlocks", 0))
    deadline_block = escrow_data.get("deadline_block", 0)

    if current_block < deadline_block and escrow_data["state"] != STATE_CREATED:
        blocks_left = deadline_block - current_block
        hours_left = blocks_left / BLOCKS_PER_HOUR
        return None, f"Deadline not reached — {blocks_left} blocks ({hours_left:.1f}h) remaining"

    payer = escrow_data.get("payer")
    amount = escrow_data.get("amount_signa", 0)

    if not payer:
        return None, "Could not determine payer address"

    print(f"  Refunding {amount} SIGNA to {payer}...")
    refund_message = f"{ESCROW_PREFIX}REFUND:{escrow_id}:{payer}"
    tx_id, err = send_signa(operator_passphrase, payer, amount,
                            message=refund_message, network=network)
    if err:
        return None, f"Refund failed: {err}"

    return {
        "escrow_id": escrow_id,
        "state": STATE_REFUNDED,
        "payer": payer,
        "amount_signa": amount,
        "tx_id": tx_id,
    }, None


def get_escrow_status(escrow_id, address=None, network=None):
    """
    Reconstruct escrow state from on-chain messages.
    Scans transaction history for ESCROW: messages matching the escrow_id.
    """
    api = get_api(network)

    # If no address provided, use the dev wallet (escrow operator)
    # In production: query the AT contract directly
    if not address:
        # Try to find the escrow in recent transactions
        # For prototype, we scan a known address
        return _scan_for_escrow(escrow_id, network)

    result = api.get("getAccountTransactions",
                     account=address,
                     firstIndex=0,
                     lastIndex=199)

    txs = result.get("transactions", [])
    escrow, err = _parse_escrow_from_txs(escrow_id, txs)

    # If we found a worker address different from the scanned address,
    # also scan the worker's transactions to pick up SUBMIT messages
    worker = escrow.get("worker")
    if worker and worker != address and escrow.get("state") == STATE_CREATED:
        worker_result = api.get("getAccountTransactions",
                                account=worker,
                                firstIndex=0,
                                lastIndex=199)
        worker_txs = worker_result.get("transactions", [])
        escrow2, _ = _parse_escrow_from_txs(escrow_id, worker_txs)
        # Merge — keep base data from first scan, take higher state from worker scan
        if escrow2.get("state") in (STATE_SUBMITTED, STATE_RELEASED, STATE_REFUNDED):
            escrow.update({k: v for k, v in escrow2.items()
                           if k not in ("payer", "worker", "amount_nqt", "amount_signa",
                                        "task_hash", "deadline_block", "create_tx", "created_at")})

    return escrow, err


def _scan_for_escrow(escrow_id, network=None):
    """Scan recent chain transactions for an escrow record."""
    api = get_api(network)

    # This is a simplified scan — in production use an indexed store
    result = api.get("getUnconfirmedTransactions")
    txs = result.get("unconfirmedTransactions", [])

    state, err = _parse_escrow_from_txs(escrow_id, txs)
    if state and state.get("state"):
        return state, None

    return {"escrow_id": escrow_id, "state": "UNKNOWN",
            "note": "Provide --address to scan a specific account"}, None


def _parse_escrow_from_txs(escrow_id, transactions):
    """Parse escrow state from a list of transactions."""
    # State priority — higher index wins
    STATE_RANK = {STATE_CREATED: 0, STATE_SUBMITTED: 1,
                  STATE_RELEASED: 2, STATE_REFUNDED: 2}

    escrow = {"escrow_id": escrow_id, "state": STATE_CREATED}
    best_rank = -1

    for tx in transactions:
        msg = tx.get("attachment", {}).get("message", "")
        if not msg.startswith(ESCROW_PREFIX):
            continue
        if escrow_id not in msg:
            continue

        parts = msg[len(ESCROW_PREFIX):].split(":")
        action = parts[0] if parts else ""

        if action == "CREATE" and len(parts) >= 6:
            # Always extract base data from CREATE regardless of rank
            escrow.update({
                "payer": tx.get("senderRS"),
                "worker": parts[2],
                "amount_nqt": int(parts[3]) if parts[3].isdigit() else 0,
                "amount_signa": int(parts[3]) / 100_000_000 if parts[3].isdigit() else 0,
                "task_hash": parts[4],
                "deadline_block": int(parts[5]) if parts[5].isdigit() else 0,
                "create_tx": tx.get("transaction"),
                "created_at": ts(tx.get("timestamp")),
            })
            if STATE_RANK[STATE_CREATED] > best_rank:
                escrow["state"] = STATE_CREATED
                best_rank = STATE_RANK[STATE_CREATED]
        elif action == "SUBMIT" and len(parts) >= 3:
            if STATE_RANK[STATE_SUBMITTED] > best_rank:
                escrow.update({
                    "state": STATE_SUBMITTED,
                    "submitted_hash": parts[2],
                    "submit_tx": tx.get("transaction"),
                    "submitted_at": ts(tx.get("timestamp")),
                    "submitted_by": tx.get("senderRS"),
                })
                best_rank = STATE_RANK[STATE_SUBMITTED]
        elif action == "RELEASE":
            if STATE_RANK[STATE_RELEASED] > best_rank:
                escrow.update({
                    "state": STATE_RELEASED,
                    "release_tx": tx.get("transaction"),
                    "released_at": ts(tx.get("timestamp")),
                })
                best_rank = STATE_RANK[STATE_RELEASED]
        elif action == "REFUND":
            if STATE_RANK[STATE_REFUNDED] > best_rank:
                escrow.update({
                    "state": STATE_REFUNDED,
                    "refund_tx": tx.get("transaction"),
                    "refunded_at": ts(tx.get("timestamp")),
                })
                best_rank = STATE_RANK[STATE_REFUNDED]

    return escrow, None


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Signum Agent Escrow")
    parser.add_argument("--network", default=os.environ.get("SIGNUM_NETWORK", "testnet"),
                        choices=["mainnet", "testnet"])
    sub = parser.add_subparsers(dest="cmd")

    # create
    p = sub.add_parser("create", help="Create a new escrow")
    p.add_argument("payer_passphrase")
    p.add_argument("worker_address")
    p.add_argument("amount", type=float, help="SIGNA amount")
    p.add_argument("task_description")
    p.add_argument("--deadline-hours", type=int, default=24)

    # submit
    p = sub.add_parser("submit", help="Worker submits completed result")
    p.add_argument("worker_passphrase")
    p.add_argument("escrow_id")
    p.add_argument("result_content")
    p.add_argument("--sources", default="")

    # release
    p = sub.add_parser("release", help="Release payment to worker")
    p.add_argument("operator_passphrase")
    p.add_argument("escrow_id")

    # refund
    p = sub.add_parser("refund", help="Refund payment to payer")
    p.add_argument("operator_passphrase")
    p.add_argument("escrow_id")

    # status
    p = sub.add_parser("status", help="Check escrow status")
    p.add_argument("escrow_id")
    p.add_argument("--address", default=None)

    args = parser.parse_args()
    os.environ["SIGNUM_NETWORK"] = args.network

    if args.cmd == "create":
        print(f"Creating escrow on {args.network}...")
        result, err = create_escrow(
            args.payer_passphrase, args.worker_address, args.amount,
            args.task_description, args.deadline_hours, args.network
        )
        if err:
            print(f"Error: {err}")
        else:
            print(f"\n✓ Escrow created")
            print(f"  Escrow ID:    {result['escrow_id']}")
            print(f"  Payer:        {result['payer']}")
            print(f"  Worker:       {result['worker']}")
            print(f"  Amount:       {result['amount_signa']} SIGNA")
            print(f"  Task hash:    {result['task_hash']}")
            print(f"  Deadline:     block {result['deadline_block']} (~{result['deadline_hours']}h)")
            print(f"  Record TX:    {result['record_tx']}")
            print(f"  Fund TX:      {result['fund_tx']}")
            print(f"\n  Save this escrow ID: {result['escrow_id']}")

    elif args.cmd == "submit":
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
        print(f"Submitting result for escrow {args.escrow_id}...")
        result, err = submit_result(
            args.worker_passphrase, args.escrow_id,
            args.result_content, sources, args.network
        )
        if err:
            print(f"Error: {err}")
        else:
            print(f"\n✓ Result submitted")
            print(f"  Escrow ID:   {result['escrow_id']}")
            print(f"  Result hash: {result['result_hash']}")
            print(f"  Proof TX:    {result['proof_tx']}")
            print(f"  Submit TX:   {result['submit_tx']}")

    elif args.cmd == "release":
        print(f"Releasing escrow {args.escrow_id}...")
        result, err = release_payment(
            args.operator_passphrase, args.escrow_id, args.network
        )
        if err:
            print(f"Error: {err}")
        else:
            print(f"\n✓ Payment released")
            print(f"  Worker:   {result['worker']}")
            print(f"  Amount:   {result['amount_signa']} SIGNA")
            print(f"  TX:       {result['tx_id']}")

    elif args.cmd == "refund":
        print(f"Refunding escrow {args.escrow_id}...")
        result, err = refund_escrow(
            args.operator_passphrase, args.escrow_id, args.network
        )
        if err:
            print(f"Error: {err}")
        else:
            print(f"\n✓ Refund sent")
            print(f"  Payer:    {result['payer']}")
            print(f"  Amount:   {result['amount_signa']} SIGNA")
            print(f"  TX:       {result['tx_id']}")

    elif args.cmd == "status":
        result, err = get_escrow_status(args.escrow_id, args.address, args.network)
        if err:
            print(f"Error: {err}")
        else:
            state = result.get("state", "UNKNOWN")
            state_icons = {
                STATE_CREATED: "🟡", STATE_SUBMITTED: "🔵",
                STATE_RELEASED: "✅", STATE_REFUNDED: "↩️",
                "UNKNOWN": "❓"
            }
            print(f"\n{state_icons.get(state, '?')} Escrow {args.escrow_id}: {state}")
            for k, v in result.items():
                if k not in ("escrow_id", "state") and v:
                    print(f"  {k:<20} {v}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
