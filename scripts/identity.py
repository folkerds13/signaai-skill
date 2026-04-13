#!/usr/bin/env python3
"""
Signum Agent Identity — register AI agents on-chain, look them up, query history.

Every agent gets:
  - A Signum address (their immutable identity)
  - A human-readable alias (e.g. "sig-agent-mk")
  - An on-chain metadata record (capabilities, version, owner)

Reputation is derived from transaction history — every completed task
is a permanent on-chain record. No central server required.

Usage:
  python3 identity.py register <passphrase> <agent_name> [--capabilities "trading,research"]
  python3 identity.py lookup <agent_name>
  python3 identity.py profile <address>
  python3 identity.py list
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(__file__))
from signum_api import get_api, signa, ts, fmt_address, FEE_ALIAS, FEE_MESSAGE, ok
from wallet import get_my_address, get_transactions


# ── Registry ─────────────────────────────────────────────────────────────────
# Aliases for AI agents follow the pattern: "sig-agent-<name>"
ALIAS_PREFIX = ""

# On-chain reputation messages follow a structured format:
# TASK_COMPLETE:<task_id>:<result_hash>:<rating_1_to_5>
TASK_COMPLETE_PREFIX = "TASK_COMPLETE:"


def register_agent(passphrase, agent_name, capabilities=None, version="1.0",
                   description="", network=None):
    """
    Register an AI agent identity on Signum.
    Uses the alias system to map a human-readable name to an address.
    Metadata stored in the alias URI as JSON.
    """
    api = get_api(network)

    # Get our address
    address, err = get_my_address(passphrase, network)
    if err:
        return None, f"Could not derive address: {err}"

    alias = f"{ALIAS_PREFIX}{agent_name.lower().replace(' ', '').replace('-', '').replace('_', '')}"

    metadata = {
        "type": "ai-agent",
        "name": agent_name,
        "address": address,
        "version": version,
        "capabilities": capabilities or [],
        "description": description,
    }

    # Store as JSON in alias URI
    alias_uri = f"acct:{address};sig-agent:{json.dumps(metadata, separators=(',', ':'))}"

    result = api.post("setAlias",
                      secretPhrase=passphrase,
                      aliasName=alias,
                      aliasURI=alias_uri,
                      feeNQT=FEE_ALIAS)

    if not ok(result):
        return None, result.get("error", "Alias registration failed")

    return {
        "alias": alias,
        "address": address,
        "tx_id": result.get("transaction"),
        "metadata": metadata,
    }, None


def lookup_agent(agent_name, network=None):
    """
    Look up a registered agent by name.
    Returns address and metadata if found.
    """
    api = get_api(network)
    alias = f"{ALIAS_PREFIX}{agent_name.lower().replace(' ', '').replace('-', '').replace('_', '')}"

    result = api.get("getAlias", aliasName=alias)
    if not ok(result):
        return None, f"Agent '{agent_name}' not found"

    uri = result.get("aliasURI", "")
    metadata = {}

    # Parse metadata from URI
    if "sig-agent:" in uri:
        try:
            json_part = uri.split("sig-agent:")[1]
            metadata = json.loads(json_part)
        except:
            pass

    address = metadata.get("address") or (uri.split(";")[0].replace("acct:", "") if "acct:" in uri else "")

    return {
        "alias": alias,
        "address": address,
        "metadata": metadata,
        "account_id": result.get("account"),
    }, None


def get_agent_profile(address, network=None):
    """
    Get a full agent profile: registration + reputation from on-chain history.
    Reputation = completed tasks recorded as on-chain messages.
    """
    api = get_api(network)

    # Get account info
    account = api.get("getAccount", account=address)
    if not ok(account):
        return None, account.get("error")

    # Get all message transactions to derive reputation
    all_txs = api.get("getAccountTransactions",
                      account=address,
                      firstIndex=0,
                      lastIndex=99,
                      type=1)  # type 1 = messaging transactions

    tasks_completed = []
    total_rating = 0
    rating_count = 0

    for tx in (all_txs.get("transactions") or []):
        msg = tx.get("attachment", {}).get("message", "")
        if msg.startswith(TASK_COMPLETE_PREFIX):
            parts = msg[len(TASK_COMPLETE_PREFIX):].split(":")
            if len(parts) >= 3:
                task_id, result_hash = parts[0], parts[1]
                try:
                    rating = int(parts[2])
                    total_rating += rating
                    rating_count += 1
                except:
                    rating = None

                tasks_completed.append({
                    "task_id": task_id,
                    "result_hash": result_hash,
                    "rating": rating,
                    "timestamp": ts(tx.get("timestamp")),
                    "tx_id": tx.get("transaction"),
                })

    avg_rating = (total_rating / rating_count) if rating_count > 0 else None

    return {
        "address": account.get("accountRS"),
        "balance": signa(account.get("balanceNQT", 0)),
        "tasks_completed": len(tasks_completed),
        "avg_rating": avg_rating,
        "reputation_score": _reputation_score(len(tasks_completed), avg_rating),
        "task_history": tasks_completed[:20],
    }, None


def record_task_completion(passphrase, task_id, result_hash, rating=5, network=None):
    """
    Record a completed task on-chain.
    This is the core reputation primitive — immutable, public, verifiable.

    task_id:     unique identifier for the task
    result_hash: SHA-256 hash of the delivered result (from verify.py)
    rating:      1-5 self-reported or third-party rating
    """
    api = get_api(network)
    address, err = get_my_address(passphrase, network)
    if err:
        return None, err

    message = f"{TASK_COMPLETE_PREFIX}{task_id}:{result_hash}:{rating}"

    result = api.post("sendMessage",
                      secretPhrase=passphrase,
                      recipient=address,  # send to self — public record
                      message=message,
                      messageIsText="true",
                      feeNQT=FEE_MESSAGE)

    if not ok(result):
        return None, result.get("error", "Failed to record task")

    return result.get("transaction"), None


# Well-known registry accounts — agents registered by these accounts appear
# in the public marketplace. Anyone can register; add your account here.
REGISTRY_ACCOUNTS = [
    "S-PS4K-2KE2-8LEV-HD2YE",  # SignaAI dev
    "S-44S7-32XB-5DM5-5AL3K",  # SignaAI worker
]


def list_agents(accounts=None, network=None):
    """
    List all registered SignaAI agents.
    Scans aliases for each account in the registry (or a custom list).
    """
    api = get_api(network)
    accounts_to_scan = accounts or REGISTRY_ACCOUNTS
    agents = []
    seen = set()
    for account in accounts_to_scan:
        result = api.get("getAliases", account=account)
        for alias in (result.get("aliases") or []):
            uri = alias.get("aliasURI", "")
            if "sig-agent:" not in uri:
                continue
            alias_name = alias.get("aliasName")
            if alias_name in seen:
                continue
            seen.add(alias_name)
            metadata = {}
            try:
                metadata = json.loads(uri.split("sig-agent:")[1])
            except:
                pass
            agents.append({
                "alias": alias_name,
                "address": metadata.get("address", ""),
                "name": metadata.get("name", alias_name),
                "capabilities": metadata.get("capabilities", []),
                "description": metadata.get("description", ""),
                "version": metadata.get("version", ""),
            })
    return agents


def search_agents(capability=None, accounts=None, network=None):
    """
    Search the agent marketplace by capability (case-insensitive substring match).
    If no capability given, returns all registered agents.
    Pass accounts=[...] to search agents registered by specific wallets.
    """
    agents = list_agents(accounts=accounts, network=network)
    if not capability:
        return agents
    cap_lower = capability.lower()
    return [
        a for a in agents
        if any(cap_lower in c.lower() for c in a.get("capabilities", []))
    ]


def _reputation_score(tasks, avg_rating):
    """Simple reputation score: tasks completed × avg rating (max 500)."""
    if not tasks:
        return 0
    rating = avg_rating or 3.0
    return min(500, int(tasks * rating * 10))


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Signum Agent Identity")
    parser.add_argument("--network", default=os.environ.get("SIGNUM_NETWORK", "testnet"),
                        choices=["mainnet", "testnet"])
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("register", help="Register a new agent identity")
    p.add_argument("passphrase")
    p.add_argument("agent_name")
    p.add_argument("--capabilities", default="", help="Comma-separated list")
    p.add_argument("--description", default="")
    p.add_argument("--version", default="1.0")

    p = sub.add_parser("lookup", help="Look up an agent by name")
    p.add_argument("agent_name")

    p = sub.add_parser("profile", help="Full agent profile + reputation")
    p.add_argument("address")

    p = sub.add_parser("record", help="Record a completed task (builds reputation)")
    p.add_argument("passphrase")
    p.add_argument("task_id")
    p.add_argument("result_hash")
    p.add_argument("--rating", type=int, default=5)

    sub.add_parser("list", help="List all registered agents")

    p = sub.add_parser("search", help="Search marketplace by capability")
    p.add_argument("capability", nargs="?", default=None,
                   help="Capability to search for (e.g. 'research', 'trading')")

    args = parser.parse_args()
    os.environ["SIGNUM_NETWORK"] = args.network

    if args.cmd == "register":
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        print(f"Registering agent '{args.agent_name}' on {args.network}...")
        result, err = register_agent(args.passphrase, args.agent_name,
                                     capabilities=caps,
                                     description=args.description,
                                     version=args.version,
                                     network=args.network)
        if err:
            print(f"Error: {err}")
        else:
            print(f"✓ Agent registered")
            print(f"  Alias:   {result['alias']}")
            print(f"  Address: {result['address']}")
            print(f"  TX:      {result['tx_id']}")

    elif args.cmd == "lookup":
        result, err = lookup_agent(args.agent_name, args.network)
        if err:
            print(f"Error: {err}")
        else:
            print(f"Agent: {result['metadata'].get('name', args.agent_name)}")
            print(f"Address: {result['address']}")
            caps = result['metadata'].get('capabilities', [])
            if caps:
                print(f"Capabilities: {', '.join(caps)}")

    elif args.cmd == "profile":
        result, err = get_agent_profile(args.address, args.network)
        if err:
            print(f"Error: {err}")
        else:
            print(f"Address:          {result['address']}")
            print(f"Balance:          {result['balance']:,.4f} SIGNA")
            print(f"Tasks Completed:  {result['tasks_completed']}")
            print(f"Avg Rating:       {result['avg_rating']:.1f}/5.0" if result['avg_rating'] else "Avg Rating:       —")
            print(f"Reputation Score: {result['reputation_score']}/500")
            if result['task_history']:
                print(f"\nRecent Tasks:")
                for t in result['task_history'][:5]:
                    print(f"  [{t['timestamp']}] Task {t['task_id']} — rating {t['rating']}/5")

    elif args.cmd == "record":
        tx_id, err = record_task_completion(args.passphrase, args.task_id,
                                            args.result_hash, args.rating,
                                            args.network)
        if err:
            print(f"Error: {err}")
        else:
            print(f"✓ Task completion recorded on-chain")
            print(f"  TX: {tx_id}")

    elif args.cmd == "list":
        agents = list_agents(network=args.network)
        if not agents:
            print("No agents registered yet.")
        else:
            for a in agents:
                caps = ", ".join(a['capabilities']) if a['capabilities'] else "—"
                print(f"  {a['alias']:<30} {a['address']:<20} [{caps}]")

    elif args.cmd == "search":
        agents = search_agents(capability=args.capability, network=args.network)
        label = f"capability '{args.capability}'" if args.capability else "all capabilities"
        if not agents:
            print(f"No agents found for {label}.")
        else:
            print(f"Agent Marketplace — {len(agents)} agent(s) matching {label}:\n")
            for a in agents:
                caps = ", ".join(a['capabilities']) if a['capabilities'] else "—"
                desc = f"  {a['description']}" if a['description'] else ""
                print(f"  Name:         {a['name']}")
                print(f"  Address:      {a['address']}")
                print(f"  Capabilities: {caps}")
                if desc:
                    print(f"  Description:  {a['description']}")
                print()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
