#!/usr/bin/env python3
"""
queue_escrow.py — append a task to the payer queue for daemon processing.

Usage:
    python3 queue_escrow.py "task description" --worker S-XXXX-... [--amount 1.0]

The daemon (listener.py) picks this up and creates the escrow on-chain.
Receipt arrives via Telegram in ~6-10 minutes.
"""
import argparse
import json
import os
import uuid
from datetime import datetime, timezone

QUEUE_FILE = os.path.join(
    os.environ.get("SIGNAAI_STATE_DIR", os.path.expanduser("~/.openclaw/workspace")),
    "signaai-payer-queue.json"
)
DEFAULT_WORKER = "S-44S7-32XB-5DM5-5AL3K"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", help="Task description")
    parser.add_argument("--worker", default=DEFAULT_WORKER)
    parser.add_argument("--amount", type=float, default=1.0)
    parser.add_argument("--network", default="mainnet", help="(passed to daemon; ignored here)")
    args = parser.parse_args()

    items = []
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE) as f:
                items = json.load(f)
        except (json.JSONDecodeError, OSError):
            items = []

    items.append({
        "id":             str(uuid.uuid4()),
        "task":           args.task,
        "worker_address": args.worker,
        "amount":         args.amount,
        "status":         "pending",
        "queued_at":      datetime.now(timezone.utc).isoformat(),
    })

    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        json.dump(items, f, indent=2)

    print("Task queued. The daemon will create the escrow — receipt via Telegram in ~6-10 minutes.")


if __name__ == "__main__":
    main()
