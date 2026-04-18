#!/usr/bin/env python3
"""
SignaAI Task Listener — monitors a worker wallet for incoming ESCROW:CREATE messages.
Pure Python, no AI calls. When a new task is detected, writes it to a trigger file
for OpenClaw to pick up and process.

Run continuously (recommended — add to launchd or run in background):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K

Run once and exit (useful for cron):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K --once
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from signum_api import get_api, ts, ok

ESCROW_PREFIX = "ESCROW:CREATE:"

# State: tracks which TXs have already been processed
STATE_FILE  = os.path.expanduser("~/.openclaw/workspace/signaai-listener-state.json")
# Trigger: pending tasks waiting for OpenClaw to pick up
TRIGGER_FILE = os.path.expanduser("~/.openclaw/workspace/signaai-pending-tasks.json")


# ── State helpers ─────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_timestamp": 0, "processed_txs": []}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def load_pending():
    if os.path.exists(TRIGGER_FILE):
        with open(TRIGGER_FILE) as f:
            return json.load(f)
    return []

def save_pending(tasks):
    os.makedirs(os.path.dirname(TRIGGER_FILE), exist_ok=True)
    with open(TRIGGER_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


# ── Core poll ─────────────────────────────────────────────────────────────────

def check_for_tasks(address, network, state):
    api = get_api(network)

    result = api.get("getAccountTransactions",
                     account=address,
                     firstIndex="0",
                     lastIndex="49")

    if not ok(result):
        print(f"[{now()}] API error: {result.get('error')}")
        return [], state

    new_tasks = []
    processed = set(state.get("processed_txs", []))

    for tx in (result.get("transactions") or []):
        tx_id = str(tx.get("transaction", ""))
        if not tx_id or tx_id in processed:
            continue

        msg = tx.get("attachment", {}).get("message", "")

        if msg.startswith(ESCROW_PREFIX):
            # Format: ESCROW:CREATE:<id>:<worker>:<amount_nqt>:<task_hash>:<deadline_block>
            parts = msg[len(ESCROW_PREFIX):].split(":")
            escrow_id = parts[0] if parts else "unknown"
            sender = tx.get("senderRS", "unknown")

            task = {
                "escrow_id": escrow_id,
                "tx_id": tx_id,
                "sender": sender,
                "timestamp": ts(tx.get("timestamp", 0)),
                "raw_message": msg,
                "detected_at": datetime.now().isoformat(),
                "status": "pending",
            }
            new_tasks.append(task)
            print(f"[{now()}] New task detected — escrow {escrow_id} from {sender}")

        processed.add(tx_id)

    # Keep last 500 processed TX IDs to avoid memory growth
    state["processed_txs"] = list(processed)[-500:]

    return new_tasks, state


# ── Main loop ─────────────────────────────────────────────────────────────────

def now():
    return datetime.now().strftime("%H:%M:%S")

def main():
    parser = argparse.ArgumentParser(description="SignaAI Task Listener")
    parser.add_argument("--address",       required=True,  help="Worker wallet address to monitor")
    parser.add_argument("--network",       default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--poll-interval", type=int, default=120, help="Seconds between polls (default: 120)")
    parser.add_argument("--once",          action="store_true", help="Run once and exit")
    args = parser.parse_args()

    print(f"SignaAI Listener starting")
    print(f"  Address:  {args.address}")
    print(f"  Network:  {args.network}")
    if not args.once:
        print(f"  Interval: {args.poll_interval}s")
    print(f"  Tasks:    {TRIGGER_FILE}")
    print()

    while True:
        state = load_state()
        new_tasks, state = check_for_tasks(args.address, args.network, state)

        if new_tasks:
            pending = load_pending()
            pending.extend(new_tasks)
            save_pending(pending)
            print(f"[{now()}] {len(new_tasks)} new task(s) written to trigger file")
        else:
            print(f"[{now()}] No new tasks")

        save_state(state)

        if args.once:
            break

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
