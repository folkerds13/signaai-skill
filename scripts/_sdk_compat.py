#!/usr/bin/env python3
"""_sdk_compat.py — local fixes for signaai SDK 0.3.0. Remove when 0.3.1 lands.

Fix: signaai.wallet.get_my_address (0.3.0) sends the passphrase to a public
node as a GET query parameter. Six SDK modules bind that symbol at import
time, so importing this module replaces it everywhere with local key
derivation (passphrase never leaves this machine).
"""
import os
import sys

from signaai import (arbitration, at_escrow, board, escrow, identity, verify,
                     wallet)
from signaai.api import get_api, ok
from signaai.crypto import generate_sign_keys

# Fix 2: several SDK 0.3.0 modules carry a leftover
# `sys.path.insert(0, os.path.dirname(__file__))`, which puts the installed
# package directory at the front of sys.path and shadows this skill's own
# top-level modules (verify, escrow, wallet, ...). Strip it back out.
_pkg_dir = os.path.abspath(os.path.dirname(wallet.__file__))
sys.path[:] = [p for p in sys.path
               if os.path.abspath(p or os.getcwd()) != _pkg_dir]

# Fix 3: '@worker' resolves against whichever skill runtime is installed
# (OpenClaw or Hermes); SDK 0.3.0 only checks the OpenClaw path. Also lets a
# worker config hold an env:/@file: spec instead of a literal passphrase.
from signaai import cli_secrets as _cli_secrets

_WORKER_CONFIGS = [
    "~/.openclaw/signaai-worker.json",
    "~/.openclaw/workspace/signaai-worker.json",
    "~/.hermes/signaai-worker.json",
]

_orig_resolve = _cli_secrets.resolve_passphrase


def resolve_passphrase(value):
    if value == "@worker":
        import json
        for candidate in _WORKER_CONFIGS:
            path = os.path.expanduser(candidate)
            if not os.path.exists(path):
                continue
            try:
                with open(path) as f:
                    pp = json.load(f).get("passphrase")
            except Exception:
                continue
            if pp:
                return _orig_resolve(pp)  # config value may itself be a spec
        raise ValueError("'@worker' used but no signaai-worker.json found")
    return _orig_resolve(value)


_cli_secrets.resolve_passphrase = resolve_passphrase
for _mod in (arbitration, at_escrow, escrow, identity, verify, wallet):
    if hasattr(_mod, "resolve_passphrase"):
        _mod.resolve_passphrase = resolve_passphrase


def get_my_address(passphrase, network=None):
    """Derive the account address locally — passphrase never sent to a node."""
    if not passphrase or not str(passphrase).strip():
        return None, "Passphrase cannot be empty"
    keys = generate_sign_keys(passphrase)
    api = get_api(network)
    result = api.get("getAccountId", publicKey=keys["publicKey"])
    if not ok(result):
        return None, result.get("errorDescription") or result.get("error", "lookup failed")
    return result.get("accountRS"), None


for _mod in (arbitration, at_escrow, board, escrow, identity, verify, wallet):
    _mod.get_my_address = get_my_address
