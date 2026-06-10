#!/usr/bin/env python3
"""SignaAI task board — thin wrapper over the signaai SDK (>= 0.3.0).

Phase 1 conversion: board protocol logic lives in the signaai package; this
script keeps the original CLI surface. Pre-conversion implementation:
board.py.pre-sdk

Usage:
    python3 board.py open   <passphrase|-|env:VAR|@worker|@file:PATH> <task_hash> <capability> <amount_signa> [--deadline-hours 24]
    python3 board.py claim  <passphrase|...> <task_id>
    python3 board.py accept <passphrase|...> <task_id> <worker_address>
    python3 board.py cancel <passphrase|...> <task_id>
    python3 board.py tasks  [--capability research] [--limit 20]
    python3 board.py claims <task_id>

Environment:
    SIGNAAI_BOARD — default board address (avoids passing --board everywhere)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sdk_compat  # noqa: F401 — patches SDK 0.3.0 get_my_address passphrase leak

from signaai import board as _board


def _with_resolve(fn):
    """SDK 0.3.0 board CLI passes the raw passphrase arg through without
    spec resolution (unlike wallet/verify/identity). Rebinding the four
    mutating functions fixes the CLI without putting literals in argv."""
    def wrapped(passphrase, *args, **kwargs):
        return fn(_sdk_compat.resolve_passphrase(passphrase), *args, **kwargs)
    return wrapped


for _name in ("open_task", "claim_task", "accept_claim", "cancel_task"):
    setattr(_board, _name, _with_resolve(getattr(_board, _name)))

open_task        = _board.open_task
claim_task       = _board.claim_task
accept_claim     = _board.accept_claim
cancel_task      = _board.cancel_task
list_tasks       = _board.list_tasks
get_claims       = _board.get_claims
get_board_events = _board.get_board_events
main             = _board.main

if __name__ == "__main__":
    sys.exit(main())
