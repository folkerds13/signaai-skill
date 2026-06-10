#!/usr/bin/env python3
"""SignaAI identity — thin wrapper over the signaai SDK (>= 0.3.0).

Phase 1 conversion: registry protocol logic lives in the signaai package;
this script keeps the original CLI surface, including the `verify`
subcommand (alias-owner check), which the SDK does not have yet.
Pre-conversion implementation: identity.py.pre-sdk

Usage:
  python3 identity.py register <passphrase|-|env:VAR|@worker|@file:PATH> <name> [--capabilities a,b] [--description D]
  python3 identity.py lookup <name>
  python3 identity.py profile <address>
  python3 identity.py reputation <address>
  python3 identity.py record <passphrase|...> <task_id> <result_hash> [--rating 1-5]
  python3 identity.py list
  python3 identity.py verify <name>
  python3 identity.py search [capability]
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sdk_compat  # noqa: F401 — patches SDK 0.3.0 get_my_address passphrase leak

from signaai import identity as _identity
from signaai.api import get_api, ok

register_agent         = _identity.register_agent
lookup_agent           = _identity.lookup_agent
get_agent_profile      = _identity.get_agent_profile
get_escrow_reputation  = _identity.get_reputation  # renamed in SDK
record_task_completion = _identity.record_task_completion
list_agents            = _identity.list_agents
search_agents          = _identity.search_agents
ALIAS_PREFIX           = _identity.ALIAS_PREFIX


def verify_agent(agent_name, network=None):
    """Verify an agent's identity — confirm the alias is owned by the claimed
    address. Not in SDK 0.3.0; kept locally until it moves into the package."""
    api = get_api(network)
    name_slug = agent_name.lower().replace(' ', '').replace('-', '').replace('_', '')
    name_hash = hashlib.sha256(name_slug.encode()).hexdigest()[:8]
    alias = f"{ALIAS_PREFIX}{name_slug}-{name_hash}"

    alias_result = api.get("getAlias", aliasName=alias)
    if not ok(alias_result):
        return None, f"Agent '{agent_name}' not found"

    alias_owner_id = alias_result.get("account", "")
    owner_info = api.get("getAccount", account=alias_owner_id) if alias_owner_id else {}
    alias_owner = owner_info.get("accountRS", alias_owner_id)

    uri = alias_result.get("aliasURI", "")
    metadata = {}
    if "sig-agent:" in uri:
        try:
            metadata = json.loads(uri.split("sig-agent:")[1])
        except Exception:
            pass

    claimed_address = metadata.get("address", "")
    verified = bool(claimed_address) and alias_owner == claimed_address

    return {
        "alias":           alias,
        "alias_owner":     alias_owner,
        "claimed_address": claimed_address,
        "verified":        verified,
        "name":            metadata.get("name", agent_name),
        "capabilities":    metadata.get("capabilities", []),
        "description":     metadata.get("description", ""),
    }, None


def _subcommand(argv):
    """First positional token, skipping --network/--board and their values."""
    skip = False
    for tok in argv:
        if skip:
            skip = False
            continue
        if tok in ("--network", "--board"):
            skip = True
            continue
        if tok.startswith("-"):
            continue
        return tok
    return None


def main():
    if _subcommand(sys.argv[1:]) == "verify":
        import argparse
        parser = argparse.ArgumentParser(description="SignaAI identity verify")
        parser.add_argument("--network", default=os.environ.get("SIGNUM_NETWORK", "testnet"),
                            choices=["mainnet", "testnet"])
        sub = parser.add_subparsers(dest="cmd")
        p = sub.add_parser("verify")
        p.add_argument("agent_name")
        args = parser.parse_args()
        os.environ["SIGNUM_NETWORK"] = args.network
        result, err = verify_agent(args.agent_name, args.network)
        if err:
            print(f"Error: {err}")
            return 1
        mark = "✓ VERIFIED" if result["verified"] else "✗ NOT VERIFIED"
        print(f"{mark}  {result['name']}")
        print(f"  Alias:           {result['alias']}")
        print(f"  Alias owner:     {result['alias_owner']}")
        print(f"  Claimed address: {result['claimed_address'] or '(none)'}")
        if result["capabilities"]:
            print(f"  Capabilities:    {', '.join(result['capabilities'])}")
        return 0
    return _identity.main()


if __name__ == "__main__":
    sys.exit(main())
