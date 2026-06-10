#!/usr/bin/env python3
"""SignaAI wallet — thin wrapper over the signaai SDK (>= 0.3.0).

Phase 1 conversion: protocol logic lives in the signaai package; this script
keeps the original CLI surface and module-level functions so sibling scripts
(`from wallet import get_my_address, send_signa, get_transactions`) keep
working unchanged. Pre-conversion implementation: wallet.py.pre-sdk

Usage:
  python3 wallet.py balance <address>
  python3 wallet.py send <passphrase|-|env:VAR|@worker|@file:PATH> <recipient> <amount> [message]
  python3 wallet.py history <address> [--limit 10]
  python3 wallet.py myaddress <passphrase|-|env:VAR|@worker|@file:PATH>
  python3 wallet.py status
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sdk_compat  # noqa: F401 — patches SDK 0.3.0 get_my_address passphrase leak

from signaai import wallet as _wallet

get_account      = _wallet.get_account
get_balance      = _wallet.get_balance
send_signa       = _wallet.send_signa
get_transactions = _wallet.get_transactions
get_my_address   = _sdk_compat.get_my_address
main             = _wallet.main

if __name__ == "__main__":
    sys.exit(main())
