#!/usr/bin/env python3
"""
SignaAI Task Listener — watches a worker wallet for incoming ESCROW:CREATE messages.

Primary:  WebSocket connection to a local Signum node (ws://localhost:8126/events)
          Detects tasks the moment they hit the mempool — no polling delay.
Fallback: HTTP polling against public nodes if WebSocket is unavailable.

When a task is detected:
  1. Writes it to the pending tasks trigger file for OpenClaw to pick up.
  2. Sends a Telegram message to wake the OpenClaw agent immediately.

Run continuously (launchd / background):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K

Run once (test / cron):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K --once

Force polling mode (no WebSocket):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K --no-websocket
"""

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from signum_api import get_api, ts, ok

ESCROW_PREFIX = "ESCROW:CREATE:"
STATE_FILE    = os.path.expanduser("~/.openclaw/workspace/signaai-listener-state.json")
TRIGGER_FILE  = os.path.expanduser("~/.openclaw/workspace/signaai-pending-tasks.json")
OPENCLAW_CFG  = os.path.expanduser("~/.openclaw/openclaw.json")

WS_HOST = "localhost"
WS_PORT = 8126
WS_PATH = "/events"

POLL_INTERVAL = 120  # seconds, used in fallback polling mode only


# ── Logging ───────────────────────────────────────────────────────────────────

def now():
    return datetime.now().strftime("%H:%M:%S")

def log(msg):
    print(f"[{now()}] {msg}", flush=True)


# ── State / trigger file helpers ──────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"processed_txs": []}

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


# ── Telegram trigger ──────────────────────────────────────────────────────────

def load_telegram_config():
    """Read bot token and chat ID from OpenClaw config."""
    try:
        with open(OPENCLAW_CFG) as f:
            cfg = json.load(f)
        tg = cfg.get("channels", {}).get("telegram", {})
        token = tg.get("botToken", "")
        approvers = tg.get("execApprovals", {}).get("approvers", [])
        chat_id = approvers[0] if approvers else None
        return token or None, chat_id
    except Exception:
        return None, None

def send_telegram(token, chat_id, message):
    """Send a message via Telegram Bot API to wake the OpenClaw agent."""
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log(f"Telegram send failed: {e}")


# ── Task processing ───────────────────────────────────────────────────────────

def handle_transaction(tx, address, state, tg_token, tg_chat_id):
    """
    Check if a transaction is an ESCROW:CREATE destined for our address.
    Returns True if a new task was recorded.
    """
    tx_id = str(tx.get("transaction", tx.get("id", "")))
    if not tx_id:
        return False

    processed = set(state.get("processed_txs", []))
    if tx_id in processed:
        return False

    # Always mark as seen, even if not for us
    processed.add(tx_id)
    state["processed_txs"] = list(processed)[-500:]

    recipient = tx.get("recipientRS", tx.get("recipient", ""))
    if address not in recipient:
        return False

    msg = tx.get("attachment", {}).get("message", "")
    if not msg.startswith(ESCROW_PREFIX):
        return False

    parts = msg[len(ESCROW_PREFIX):].split(":")
    escrow_id = parts[0] if parts else "unknown"
    sender = tx.get("senderRS", tx.get("sender", "unknown"))

    task = {
        "escrow_id": escrow_id,
        "tx_id": tx_id,
        "sender": sender,
        "timestamp": ts(tx.get("timestamp", 0)),
        "raw_message": msg,
        "detected_at": datetime.now().isoformat(),
        "status": "pending",
    }

    pending = load_pending()
    pending.append(task)
    save_pending(pending)

    log(f"New task — escrow {escrow_id} from {sender} (TX {tx_id})")

    send_telegram(tg_token, tg_chat_id, (
        f"*SignaAI: New Task*\n"
        f"Escrow: `{escrow_id}`\n"
        f"From: `{sender}`\n"
        f"TX: `{tx_id}`\n\n"
        f"Process it using the SignaAI skill."
    ))
    return True


def fetch_and_check(tx_id, address, network, state, tg_token, tg_chat_id):
    """Fetch a full transaction by ID then run handle_transaction on it."""
    api = get_api(network)
    result = api.get("getTransaction", transaction=str(tx_id))
    if not ok(result):
        return False
    return handle_transaction(result, address, state, tg_token, tg_chat_id)


# ── Minimal WebSocket client (stdlib only, no dependencies) ───────────────────

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf

def _ws_recv_frame(sock):
    header = _recv_exact(sock, 2)
    opcode  = header[0] & 0x0F
    masked  = (header[1] & 0x80) != 0
    length  = header[1] & 0x7F

    if length == 126:
        length = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(sock, 8))[0]

    mask_key = _recv_exact(sock, 4) if masked else b""
    payload  = _recv_exact(sock, length)

    if masked:
        payload = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))

    return opcode, payload

def _ws_send_pong(sock, payload=b""):
    mask_key = os.urandom(4)
    masked = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))
    sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask_key + masked)

def ws_connect(host, port, path):
    """Open a WebSocket connection. Returns socket or raises ConnectionRefusedError."""
    key = base64.b64encode(os.urandom(16)).decode()
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(90)  # heartbeat is every 30s — allow 3× before timeout

    sock.sendall((
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode())

    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(1)

    if b"101" not in resp:
        sock.close()
        raise ConnectionError(f"WebSocket upgrade failed: {resp[:80]}")

    return sock


# ── WebSocket event loop ──────────────────────────────────────────────────────

def run_websocket(address, network, state, tg_token, tg_chat_id):
    """
    Connect to local node WebSocket and process events.
    Returns True  → reconnect (transient error).
    Returns False → fall back to polling (node not available).
    """
    try:
        sock = ws_connect(WS_HOST, WS_PORT, WS_PATH)
    except (ConnectionRefusedError, OSError):
        return False  # node not running

    log(f"WebSocket connected — ws://{WS_HOST}:{WS_PORT}{WS_PATH}")

    try:
        while True:
            opcode, payload = _ws_recv_frame(sock)

            if opcode == 0x9:  # ping → pong
                _ws_send_pong(sock, payload)
                continue

            if opcode == 0x8:  # close
                log("WebSocket closed by node")
                return True  # reconnect

            if opcode != 0x1:  # ignore non-text frames
                continue

            try:
                event = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            etype   = event.get("e", "")
            epayload = event.get("p", {})

            if etype == "CONNECTED":
                syncing = epayload.get("isSyncing", False)
                local   = epayload.get("localHeight", "?")
                total   = epayload.get("globalHeight", "?")
                pct     = f"{local/total*100:.1f}%" if isinstance(local, int) and isinstance(total, int) and total else "?"
                log(f"Node: {epayload.get('networkName','?')} "
                    f"height={local}/{total} ({pct}) "
                    f"{'— syncing' if syncing else '— synced'}")

            elif etype == "HEARTBEAT":
                pass  # silent

            elif etype == "BLOCK_PUSHED":
                local  = epayload.get("localHeight", 0)
                total  = epayload.get("globalHeight", 0)
                syncing = local < total
                # Only log every 10k blocks during sync, always log when synced
                if not syncing:
                    log(f"Block {local} pushed")
                elif local % 10000 == 0:
                    pct = f"{local/total*100:.1f}%" if total else "?"
                    log(f"Syncing... {local}/{total} ({pct})")

            elif etype == "PENDING_TRANSACTIONS_ADDED":
                tx_ids = epayload.get("transactionIds", [])
                if not tx_ids:
                    continue
                log(f"{len(tx_ids)} pending TX(s) — checking...")
                found = sum(
                    fetch_and_check(tid, address, network, state, tg_token, tg_chat_id)
                    for tid in tx_ids
                )
                save_state(state)
                if not found:
                    log("No new tasks in batch")

    except (ConnectionError, socket.timeout, OSError) as e:
        log(f"WebSocket error: {e}")
        return True  # reconnect

    finally:
        try:
            sock.close()
        except Exception:
            pass


# ── Polling fallback ──────────────────────────────────────────────────────────

def poll_once(address, network, state, tg_token, tg_chat_id):
    api = get_api(network)
    result = api.get("getAccountTransactions",
                     account=address,
                     firstIndex="0",
                     lastIndex="49")
    if not ok(result):
        log(f"API error: {result.get('error')}")
        return

    found = sum(
        handle_transaction(tx, address, state, tg_token, tg_chat_id)
        for tx in (result.get("transactions") or [])
    )
    save_state(state)
    if not found:
        log("No new tasks")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SignaAI Task Listener")
    parser.add_argument("--address",       required=True,  help="Wallet address to monitor")
    parser.add_argument("--network",       default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL,
                        help="Fallback polling interval in seconds (default: 120)")
    parser.add_argument("--once",          action="store_true", help="Poll once and exit")
    parser.add_argument("--no-websocket",  action="store_true", help="Force polling mode")
    args = parser.parse_args()

    tg_token, tg_chat_id = load_telegram_config()

    print(f"SignaAI Listener starting", flush=True)
    print(f"  Address:  {args.address}", flush=True)
    print(f"  Network:  {args.network}", flush=True)
    print(f"  Tasks:    {TRIGGER_FILE}", flush=True)
    print(f"  Telegram: {'enabled' if tg_token else 'disabled'}", flush=True)
    print(flush=True)

    state = load_state()

    if args.once:
        poll_once(args.address, args.network, state, tg_token, tg_chat_id)
        return

    if args.no_websocket:
        print(f"  Mode:     polling every {args.poll_interval}s", flush=True)
        while True:
            state = load_state()
            poll_once(args.address, args.network, state, tg_token, tg_chat_id)
            time.sleep(args.poll_interval)

    # WebSocket mode with automatic polling fallback
    print(f"  Mode:     WebSocket → polling fallback", flush=True)
    print(flush=True)

    ws_available = True
    reconnect_delay = 5

    while True:
        state = load_state()

        if ws_available:
            should_reconnect = run_websocket(args.address, args.network, state, tg_token, tg_chat_id)
            if should_reconnect:
                log(f"Reconnecting in {reconnect_delay}s...")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue
            else:
                log(f"Node unavailable — polling every {args.poll_interval}s")
                ws_available = False

        # Polling fallback
        poll_once(args.address, args.network, state, tg_token, tg_chat_id)
        time.sleep(args.poll_interval)

        # Periodically check if node came back
        try:
            s = socket.create_connection((WS_HOST, WS_PORT), timeout=3)
            s.close()
            log("Node back online — switching to WebSocket")
            ws_available = True
            reconnect_delay = 5
        except OSError:
            pass


if __name__ == "__main__":
    main()
