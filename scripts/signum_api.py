#!/usr/bin/env python3
"""
Signum API Client — base layer for all Signum skill scripts.
Handles node communication, NQT conversion, and error handling.
"""
import json
import urllib.request
import urllib.parse
from datetime import datetime

# ── Constants ────────────────────────────────────────────────────────────────
NQT = 100_000_000          # 1 SIGNA = 100,000,000 NQT (Nano-Quant)
FEE_STANDARD  = 735_000     # 0.00735 SIGNA — standard transaction fee
FEE_MESSAGE   = 10_000_000  # 0.1 SIGNA — message transactions (node minimum)
FEE_ALIAS     = 20_000_000  # 0.2 SIGNA — alias registration fee
DEADLINE      = 1440       # minutes — max transaction validity window

NODES = {
    "mainnet": [
        "https://europe.signum.network",
        "https://us.signum.network",
        "https://brazil.signum.network",
    ],
    "testnet": [
        "https://europe3.testnet.signum.network",
    ]
}

# ── Client ───────────────────────────────────────────────────────────────────
class SignumAPI:
    def __init__(self, network="testnet"):
        self.network = network
        self.nodes = NODES[network]
        self.active_node = self.nodes[0]

    def _call(self, params, method="GET", retries=2):
        url = f"{self.active_node}/api"
        for attempt in range(retries):
            try:
                if method == "GET":
                    query = urllib.parse.urlencode(params)
                    req = urllib.request.Request(
                        f"{url}?{query}",
                        headers={"User-Agent": "SigSkill/1.0"}
                    )
                else:
                    data = urllib.parse.urlencode(params).encode()
                    req = urllib.request.Request(
                        url, data=data,
                        headers={"User-Agent": "SigSkill/1.0",
                                 "Content-Type": "application/x-www-form-urlencoded"}
                    )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                    if "errorDescription" in result:
                        return {"error": result["errorDescription"], "errorCode": result.get("errorCode")}
                    return result
            except Exception as e:
                if attempt == retries - 1:
                    # Try next node
                    idx = self.nodes.index(self.active_node)
                    if idx + 1 < len(self.nodes):
                        self.active_node = self.nodes[idx + 1]
                        return self._call(params, method, retries=1)
                    return {"error": str(e)}
        return {"error": "All nodes failed"}

    def get(self, request_type, **params):
        params["requestType"] = request_type
        return self._call(params, "GET")

    def post(self, request_type, **params):
        params["requestType"] = request_type
        params["deadline"] = DEADLINE
        return self._call(params, "POST")

# ── Helpers ──────────────────────────────────────────────────────────────────
def signa(nqt):
    """Convert NQT to SIGNA float."""
    return int(nqt) / NQT if nqt else 0

def nqt(amount_signa):
    """Convert SIGNA float to NQT int."""
    return int(float(amount_signa) * NQT)

def ts(timestamp_signum):
    """Convert Signum genesis-relative timestamp to datetime string.
    Signum genesis: 2014-08-11 02:00:00 UTC"""
    genesis = 1407715200  # Unix timestamp of Signum genesis
    if not timestamp_signum:
        return "—"
    try:
        unix = genesis + int(timestamp_signum)
        return datetime.fromtimestamp(unix).strftime("%Y-%m-%d %H:%M")
    except:
        return str(timestamp_signum)

def fmt_address(addr):
    """Ensure address has SIGNA- prefix."""
    if addr and not str(addr).startswith("SIGNA-"):
        return f"SIGNA-{addr}"
    return addr

def ok(result):
    """Check if API result is successful."""
    return result and "error" not in result

# ── Singleton ────────────────────────────────────────────────────────────────
_api_instance = None

def get_api(network=None):
    global _api_instance
    if _api_instance is None or (network and _api_instance.network != network):
        import os
        net = network or os.environ.get("SIGNUM_NETWORK", "testnet")
        _api_instance = SignumAPI(net)
    return _api_instance

if __name__ == "__main__":
    api = get_api("mainnet")
    status = api.get("getBlockchainStatus")
    print(f"Network:  {api.network}")
    print(f"Node:     {api.active_node}")
    print(f"Blocks:   {status.get('numberOfBlocks', '?'):,}")
    print(f"Version:  {status.get('version', '?')}")
