#!/usr/bin/env python3
"""
SignaAI Task Listener — autonomous worker daemon.

Detection:
  Primary:  WebSocket (ws://localhost:8126/events) — real-time mempool detection
  Fallback: HTTP polling against public nodes (every 120s)

Execution (when signaai-worker.json is configured):
  On new ESCROW:ASSIGN →
    1. Call LLM (Anthropic Claude Haiku) to research the task
    2. Stamp result hash on Signum blockchain
    3. Wait for block confirmation (~4 min, polls every 30s)
    4. Self-verify stamp
    5. Submit result to escrow
    6. Notify payer via Telegram with TX IDs

Fallback (when no worker config):
  Trigger OpenClaw agent via /hooks/agent (requires manual approval)

Worker config: ~/.openclaw/signaai-worker.json
  {
    "passphrase": "your worker passphrase",
    "apiKey": ""    ← optional, falls back to ANTHROPIC_API_KEY env var
  }

Run continuously (launchd):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K

Run once (test):
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K --once

Force polling mode:
  python3 listener.py --address S-44S7-32XB-5DM5-5AL3K --no-websocket
"""

import argparse
import base64
import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from signum_api import get_api, ts, ok

ESCROW_PREFIX = "ESCROW:ASSIGN:"
STATE_FILE    = os.path.expanduser("~/.openclaw/workspace/signaai-listener-state.json")
TRIGGER_FILE  = os.path.expanduser("~/.openclaw/workspace/signaai-pending-tasks.json")

# Single-worker task queue — processes one escrow at a time, no parallel races
_task_queue = queue.Queue()

def _task_worker():
    """Background thread that drains the task queue one at a time."""
    while True:
        fn, args = _task_queue.get()
        try:
            fn(*args)
        except Exception as e:
            log(f"Task worker error: {e}")
        finally:
            _task_queue.task_done()

_worker_thread = threading.Thread(target=_task_worker, daemon=True)
_worker_thread.start()
OPENCLAW_CFG  = os.path.expanduser("~/.openclaw/openclaw.json")
WORKER_CFG    = os.path.expanduser("~/.openclaw/signaai-worker.json")

WS_HOST = "localhost"
WS_PORT = 8126
WS_PATH = "/events"

POLL_INTERVAL    = 120   # seconds between fallback polls
CONFIRM_POLL     = 30    # seconds between confirmation checks
CONFIRM_TIMEOUT  = 600   # max seconds to wait for confirmation (10 min)


# ── Logging ───────────────────────────────────────────────────────────────────

def now():
    return datetime.now().strftime("%H:%M:%S")

def log(msg):
    print(f"[{now()}] {msg}", flush=True)


# ── State / trigger file helpers ──────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"processed_txs": []}
    return {"processed_txs": []}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)

def load_pending():
    if os.path.exists(TRIGGER_FILE):
        try:
            with open(TRIGGER_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []

def save_pending(tasks):
    os.makedirs(os.path.dirname(TRIGGER_FILE), exist_ok=True)
    tmp = TRIGGER_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tasks, f, indent=2)
    os.replace(tmp, TRIGGER_FILE)


# ── Config ────────────────────────────────────────────────────────────────────

def load_openclaw_config():
    """Read Telegram and hooks config from OpenClaw config."""
    try:
        with open(OPENCLAW_CFG) as f:
            cfg = json.load(f)
        tg = cfg.get("channels", {}).get("telegram", {})
        tg_token = tg.get("botToken", "") or None
        approvers = tg.get("execApprovals", {}).get("approvers", [])
        tg_chat_id = approvers[0] if approvers else None

        hooks = cfg.get("hooks", {})
        hook_enabled = hooks.get("enabled", False)
        hook_token = hooks.get("token", "") if hook_enabled else None
        hook_path = hooks.get("path", "/hooks")
        gw_port = cfg.get("gateway", {}).get("port", 18789)

        return tg_token, tg_chat_id, hook_token, hook_path, gw_port
    except Exception:
        return None, None, None, "/hooks", 18789

OPENCLAW_MODELS = os.path.expanduser("~/.openclaw/agents/main/agent/models.json")

ENV_VARS = {
    # provider prefix → env var name (fallback if models.json not present)
    "xai":       "XAI_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq":      "GROQ_API_KEY",
    "ollama":    None,  # no key needed
}

def load_openclaw_llm():
    """
    Read the active LLM provider, model, base URL, and API key from OpenClaw config.

    Primary source: ~/.openclaw/agents/main/agent/models.json
      — OpenClaw stores API keys here, readable by launchd daemons.

    Fallback: env vars (XAI_API_KEY etc.) for interactive shells.

    Returns (provider, model, base_url, api_key) or None if can't determine.
    """
    try:
        with open(OPENCLAW_CFG) as f:
            cfg = json.load(f)

        # e.g. "xai/grok-4" or "groq/llama-3.3-70b-versatile"
        primary = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
        if "/" not in primary:
            return None

        provider, model_id = primary.split("/", 1)

        # Read baseUrl and apiKey from models.json (where OpenClaw stores them)
        base_url = ""
        api_key  = ""
        try:
            with open(OPENCLAW_MODELS) as f:
                models_cfg = json.load(f)
            prov = models_cfg.get("providers", {}).get(provider, {})
            base_url = prov.get("baseUrl", "")
            api_key  = prov.get("apiKey", "")
        except Exception:
            pass

        # Fallback: env var (interactive shells)
        if not api_key:
            env_var = ENV_VARS.get(provider)
            api_key = os.environ.get(env_var, "") if env_var else ""

        if not base_url:
            base_url, _ = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["xai"])

        if not api_key and provider != "ollama":
            return None

        return provider, model_id, base_url, api_key
    except Exception:
        return None


def load_worker_config():
    """
    Load worker passphrase from signaai-worker.json.
    LLM provider/model/key are read from OpenClaw's models.json automatically —
    the same place OpenClaw stores them, readable by launchd daemons.
    Only passphrase is required in signaai-worker.json.
    Returns config dict or None if not configured.
    """
    if not os.path.exists(WORKER_CFG):
        return None
    try:
        with open(WORKER_CFG) as f:
            cfg = json.load(f)
        passphrase = str(cfg.get("passphrase", "")).strip()
        if not passphrase:
            return None

        llm = load_openclaw_llm()
        if not llm:
            log("Could not load LLM config from OpenClaw — is a provider configured?")
            return None

        provider, model_id, base_url, api_key = llm
        cfg["passphrase"] = passphrase
        cfg["provider"]   = provider
        cfg["model"]      = model_id
        cfg["baseUrl"]    = base_url
        cfg["apiKey"]     = api_key
        return cfg
    except Exception as e:
        log(f"Worker config error: {e}")
        return None


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(token, chat_id, message):
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


# ── Hooks fallback (OpenClaw agent trigger) ───────────────────────────────────

def trigger_agent(hook_token, hook_path, gw_port, escrow_id, sender,
                  worker_address, task_hash, task_description=""):
    """POST to OpenClaw /hooks/agent — fallback when no worker config."""
    if not hook_token:
        return
    try:
        skill_dir = os.path.expanduser("~/.openclaw/workspace/skills/signaai/scripts")
        task_hint = f'Task: "{task_description[:200]}"' if task_description else f"task_hash: {task_hash[:16]}..."
        message = (
            f"New SignaAI task assigned to you.\n\n"
            f"Escrow ID: {escrow_id}\n"
            f"Payer: {sender}\n"
            f"Your wallet: {worker_address}\n"
            f"{task_hint}\n\n"
            f"Run these steps IN ORDER. Use ONLY --network mainnet flag. "
            f"NEVER use SIGNUM_NETWORK= prefix. NEVER output placeholder values.\n\n"
            f"1. python3 {skill_dir}/escrow.py --network mainnet status {escrow_id} --address {worker_address}\n"
            f"2. Research the task.\n"
            f"3. python3 {skill_dir}/verify.py --network mainnet stamp \"<passphrase>\" \"<result>\"\n"
            f"4. Wait 4 minutes.\n"
            f"5. python3 {skill_dir}/verify.py --network mainnet verify \"<result>\" <stamp_tx>\n"
            f"6. python3 {skill_dir}/escrow.py --network mainnet submit \"<passphrase>\" {escrow_id} \"<result>\"\n\n"
            f"Show actual TX IDs from script output only."
        )
        url = f"http://127.0.0.1:{gw_port}{hook_path}/agent"
        data = json.dumps({
            "message": message,
            "name": "SignaAI Worker",
            "agentId": "main",
            "timeoutSeconds": 900,
        }).encode()
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Bearer {hook_token}",
            "Content-Type": "application/json",
        })
        urllib.request.urlopen(req, timeout=10)
        log(f"Agent triggered via hooks API for escrow {escrow_id}")
    except Exception as e:
        log(f"Hooks trigger failed: {e}")


# ── Autonomous execution ──────────────────────────────────────────────────────

LLM_PROVIDERS = {
    # provider  → (base_url, default_model)
    "xai":       ("https://api.x.ai/v1",                  "grok-3-mini"),
    "openai":    ("https://api.openai.com/v1",             "gpt-4o-mini"),
    "groq":      ("https://api.groq.com/openai/v1",        "llama-3.3-70b-versatile"),
    "anthropic": (None,                                    "claude-haiku-4-5-20251001"),  # separate handler
    "ollama":    ("http://localhost:11434/v1",             "llama3.1:8b"),               # no key needed
}

def call_llm(task_description, api_key, provider="xai", model=None, base_url=None):
    """
    Call LLM to research a task. Returns result text.

    provider/model/base_url are read from openclaw.json via load_openclaw_llm().
    Falls back to LLM_PROVIDERS defaults if base_url not supplied.

    Supported providers: xai, openai, groq, anthropic, ollama
    All except anthropic use the OpenAI-compatible /chat/completions endpoint.
    """
    prompt = (
        "You are a research assistant. Complete this task thoroughly and accurately. "
        "Cite any specific facts, figures, or sources you use.\n\n"
        f"Task: {task_description}"
    )

    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        data = json.dumps({
            "model": model or "claude-haiku-4-5-20251001",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(url, data=data, headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return resp["content"][0]["text"]

    # All other providers: OpenAI-compatible
    # Use base_url from openclaw.json if available, otherwise fall back to defaults
    if not base_url:
        base_url, _ = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["xai"])
    if not model:
        _, model = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["xai"])

    url = f"{base_url}/chat/completions"
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps({
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return resp["choices"][0]["message"]["content"]


def wait_for_confirmation(tx_id, network):
    """Poll until TX has at least 1 confirmation. Returns True/False."""
    api = get_api(network)
    deadline = time.time() + CONFIRM_TIMEOUT
    attempt = 0
    while time.time() < deadline:
        result = api.get("getTransaction", transaction=str(tx_id))
        if ok(result) and int(result.get("confirmations", 0)) >= 1:
            return True
        elapsed = attempt * CONFIRM_POLL
        log(f"  Waiting for block confirmation... ({elapsed}s elapsed)")
        time.sleep(CONFIRM_POLL)
        attempt += 1
    return False


def execute_task_autonomously(escrow_id, task_description, sender, worker_address,
                               network, worker_cfg, tg_token, tg_chat_id):
    """
    Full autonomous task execution in a background thread.
    The LLM does the research. Python handles all blockchain operations.
    No exec calls, no approval prompts, no hallucinated TX IDs.
    """
    from verify import hash_content, publish_proof, verify_proof
    from escrow import submit_result

    passphrase = worker_cfg["passphrase"]
    api_key    = worker_cfg["apiKey"]
    provider   = worker_cfg.get("provider", "xai")
    model      = worker_cfg.get("model")
    base_url   = worker_cfg.get("baseUrl")

    log(f"[{escrow_id}] Autonomous execution starting")
    log(f"[{escrow_id}] Task: {task_description[:100]}...")

    def fail(reason):
        log(f"[{escrow_id}] FAILED: {reason}")
        send_telegram(tg_token, tg_chat_id,
                      f"❌ *SignaAI Task Failed*\nEscrow: `{escrow_id}`\n{reason}")

    # Step 1: Research via LLM
    if not api_key and provider != "ollama":
        env_var = ENV_VARS.get(provider, "API key")
        fail(f"No API key — set {env_var} environment variable")
        return
    try:
        log(f"[{escrow_id}] Calling LLM ({provider}/{model}) for research...")
        result = call_llm(task_description, api_key, provider, model=model, base_url=base_url)
        log(f"[{escrow_id}] Research complete ({len(result)} chars)")
    except Exception as e:
        fail(f"LLM call failed: {e}")
        return

    # Step 2: Stamp result on-chain
    try:
        log(f"[{escrow_id}] Stamping result on Signum...")
        hashes = hash_content(result)
        proof, err = publish_proof(passphrase, hashes["content_hash"],
                                   hashes["sources_hash"],
                                   label=f"escrow-{escrow_id}",
                                   network=network)
        if err:
            raise Exception(err)
        stamp_tx   = proof["tx_id"]
        result_hash = hashes["content_hash"]
        log(f"[{escrow_id}] Stamp TX: {stamp_tx}")
    except Exception as e:
        fail(f"Stamp failed: {e}")
        return

    # Step 3: Wait for block confirmation
    log(f"[{escrow_id}] Waiting for stamp to confirm (~4 min)...")
    if not wait_for_confirmation(stamp_tx, network):
        fail(f"Stamp TX not confirmed after {CONFIRM_TIMEOUT}s — TX: {stamp_tx}")
        return
    log(f"[{escrow_id}] Stamp confirmed ✓")

    # Step 4: Self-verify
    try:
        log(f"[{escrow_id}] Self-verifying stamp...")
        verified, details = verify_proof(result, stamp_tx, network=network)
        if not verified:
            raise Exception(f"Hash mismatch: {details.get('onchain_content_hash', '?')} vs {details.get('computed_content_hash', '?')}")
        log(f"[{escrow_id}] Self-verified ✓")
    except Exception as e:
        fail(f"Self-verify failed: {e}")
        return

    # Step 5: Submit to escrow
    try:
        log(f"[{escrow_id}] Submitting to escrow...")
        submission, err = submit_result(passphrase, escrow_id, result, network=network)
        if err:
            raise Exception(err)
        submit_tx = submission["submit_tx"]
        log(f"[{escrow_id}] Submit TX: {submit_tx}")
    except Exception as e:
        fail(f"Submit failed: {e}")
        return

    # Step 6: Notify payer with result + TX IDs
    # Truncate result to fit Telegram's 4096 char limit (leave room for header)
    result_preview = result[:3000] + "..." if len(result) > 3000 else result
    send_telegram(tg_token, tg_chat_id, (
        f"✅ *SignaAI Task Complete*\n"
        f"Escrow: `{escrow_id}`\n\n"
        f"*Research Result:*\n{result_preview}\n\n"
        f"Stamp TX: `{stamp_tx}`\n"
        f"Submit TX: `{submit_tx}`\n\n"
        f"Release payment:\n"
        f"`Release escrow {escrow_id}`"
    ))

    # Update pending tasks file
    pending = load_pending()
    for task in pending:
        if task.get("escrow_id") == escrow_id:
            task["status"] = "complete"
            task["stamp_tx"] = stamp_tx
            task["submit_tx"] = submit_tx
            task["result_hash"] = result_hash
    save_pending(pending)

    log(f"[{escrow_id}] Complete ✓  stamp={stamp_tx}  submit={submit_tx}")


# ── Transaction handler ───────────────────────────────────────────────────────

def handle_transaction(tx, address, network, state, tg_token, tg_chat_id,
                       hook_token=None, hook_path="/hooks", gw_port=18789,
                       worker_cfg=None):
    """
    Check if a transaction is an ESCROW:ASSIGN destined for our address.
    If worker config is present, execute autonomously. Otherwise trigger agent.
    Returns True if a new task was recorded.
    """
    tx_id = str(tx.get("transaction", tx.get("id", "")))
    if not tx_id:
        return False

    processed = set(state.get("processed_txs", []))
    if tx_id in processed:
        return False

    processed.add(tx_id)
    state["processed_txs"] = list(processed)[-500:]

    recipient = tx.get("recipientRS", tx.get("recipient", ""))
    if address not in recipient:
        return False

    msg = tx.get("attachment", {}).get("message", "")
    if not msg.startswith(ESCROW_PREFIX):
        return False

    # Format: ESCROW:ASSIGN:<escrow_id>:<task_hash>:<task_description>|TG:<token>~<chat_id>
    # Legacy format (no TG suffix): ESCROW:ASSIGN:<escrow_id>:<task_hash>:<task_description>
    # |TG: suffix avoids colon-in-token parsing issues
    raw = msg[len(ESCROW_PREFIX):]
    parts = raw.split(":", 2)  # escrow_id, task_hash, rest
    escrow_id = parts[0] if len(parts) > 0 else "unknown"
    task_hash = parts[1] if len(parts) > 1 else ""
    rest      = parts[2] if len(parts) > 2 else ""

    if "|TG:" in rest:
        desc_part, tg_part   = rest.rsplit("|TG:", 1)
        task_description     = desc_part
        if "~" in tg_part:
            payer_tg_token, payer_tg_chat = tg_part.split("~", 1)
        else:
            payer_tg_token, payer_tg_chat = "", ""
    else:
        task_description = rest
        payer_tg_token   = ""
        payer_tg_chat    = ""
    sender           = tx.get("senderRS", tx.get("sender", "unknown"))

    task = {
        "escrow_id":        escrow_id,
        "tx_id":            tx_id,
        "sender":           sender,
        "task_description": task_description,
        "timestamp":        ts(tx.get("timestamp", 0)),
        "raw_message":      msg,
        "detected_at":      datetime.now().isoformat(),
        "status":           "pending",
    }

    pending = load_pending()
    pending.append(task)
    save_pending(pending)

    log(f"New task — escrow {escrow_id} from {sender} (TX {tx_id})")

    send_telegram(tg_token, tg_chat_id, (
        f"*SignaAI: New Task*\n"
        f"Escrow: `{escrow_id}`\n"
        f"From: `{sender}`\n\n"
        f"{'Processing autonomously...' if worker_cfg and task_description else 'Triggering agent...'}"
    ))

    if worker_cfg and task_description:
        # Queue task for sequential processing — one at a time, no parallel races
        # Use payer's Telegram (from ASSIGN message) for completion notification
        notify_token   = payer_tg_token or tg_token
        notify_chat_id = payer_tg_chat  or tg_chat_id
        queue_depth = _task_queue.qsize()
        if queue_depth > 0:
            log(f"Task queued (position {queue_depth + 1}) — escrow {escrow_id}")
        _task_queue.put((execute_task_autonomously, (
            escrow_id, task_description, sender, address,
            network, worker_cfg, notify_token, notify_chat_id
        )))
    else:
        # Fallback: trigger OpenClaw agent via hooks API
        if not task_description:
            log(f"  No task description in ASSIGN message — falling back to agent trigger")
        trigger_agent(hook_token, hook_path, gw_port, escrow_id, sender,
                      address, task_hash, task_description)

    return True


def fetch_and_check(tx_id, address, network, state, tg_token, tg_chat_id,
                    hook_token=None, hook_path="/hooks", gw_port=18789,
                    worker_cfg=None):
    """Fetch a full transaction by ID then run handle_transaction on it."""
    api = get_api(network)
    result = api.get("getTransaction", transaction=str(tx_id))
    if not ok(result):
        log(f"Could not fetch TX {tx_id}: {result.get('error', 'unknown error')}")
        return False
    return handle_transaction(result, address, network, state, tg_token, tg_chat_id,
                              hook_token, hook_path, gw_port, worker_cfg)


# ── Minimal WebSocket client (stdlib only) ────────────────────────────────────

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
    opcode = header[0] & 0x0F
    masked = (header[1] & 0x80) != 0
    length = header[1] & 0x7F

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
    key = base64.b64encode(os.urandom(16)).decode()
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(90)
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

def run_websocket(address, network, state, tg_token, tg_chat_id,
                  hook_token=None, hook_path="/hooks", gw_port=18789,
                  worker_cfg=None):
    """
    Connect to local node WebSocket and process events.
    Returns True  → reconnect (transient error).
    Returns False → fall back to polling (node not available).
    """
    try:
        sock = ws_connect(WS_HOST, WS_PORT, WS_PATH)
    except (ConnectionRefusedError, OSError):
        return False

    log(f"WebSocket connected — ws://{WS_HOST}:{WS_PORT}{WS_PATH}")

    try:
        while True:
            opcode, payload = _ws_recv_frame(sock)

            if opcode == 0x9:
                _ws_send_pong(sock, payload)
                continue
            if opcode == 0x8:
                log("WebSocket closed by node")
                return True
            if opcode != 0x1:
                continue

            try:
                event = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            etype    = event.get("e", "")
            epayload = event.get("p", {})

            if etype == "CONNECTED":
                local  = epayload.get("localHeight", "?")
                total  = epayload.get("globalHeight", "?")
                pct    = f"{local/total*100:.1f}%" if isinstance(local, int) and isinstance(total, int) and total else "?"
                syncing = epayload.get("isSyncing", False)
                log(f"Node: {epayload.get('networkName','?')} height={local}/{total} ({pct}) "
                    f"{'— syncing' if syncing else '— synced'}")

            elif etype == "HEARTBEAT":
                pass

            elif etype == "BLOCK_PUSHED":
                local   = epayload.get("localHeight", 0)
                total   = epayload.get("globalHeight", 0)
                syncing = local < total
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
                    fetch_and_check(tid, address, network, state, tg_token, tg_chat_id,
                                    hook_token, hook_path, gw_port, worker_cfg)
                    for tid in tx_ids
                )
                save_state(state)
                if not found:
                    log("No new tasks in batch")

    except (ConnectionError, socket.timeout, OSError) as e:
        log(f"WebSocket error: {e}")
        return True
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ── Polling fallback ──────────────────────────────────────────────────────────

def poll_once(address, network, state, tg_token, tg_chat_id,
              hook_token=None, hook_path="/hooks", gw_port=18789,
              worker_cfg=None):
    api = get_api(network)
    result = api.get("getAccountTransactions",
                     account=address,
                     firstIndex="0",
                     lastIndex="49")
    if not ok(result):
        log(f"API error: {result.get('error')}")
        return

    found = sum(
        handle_transaction(tx, address, network, state, tg_token, tg_chat_id,
                           hook_token, hook_path, gw_port, worker_cfg)
        for tx in (result.get("transactions") or [])
    )
    save_state(state)
    if not found:
        log("No new tasks")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SignaAI Autonomous Worker Daemon")
    parser.add_argument("--address",       required=True,  help="Wallet address to monitor")
    parser.add_argument("--network",       default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL)
    parser.add_argument("--once",          action="store_true", help="Poll once and exit")
    parser.add_argument("--no-websocket",  action="store_true", help="Force polling mode")
    args = parser.parse_args()

    tg_token, tg_chat_id, hook_token, hook_path, gw_port = load_openclaw_config()
    worker_cfg = load_worker_config()

    print(f"SignaAI Listener starting", flush=True)
    print(f"  Address:     {args.address}", flush=True)
    print(f"  Network:     {args.network}", flush=True)
    print(f"  Tasks:       {TRIGGER_FILE}", flush=True)
    print(f"  Telegram:    {'enabled' if tg_token else 'disabled'}", flush=True)
    if worker_cfg:
        print(f"  Mode:        AUTONOMOUS (LLM + blockchain in daemon)", flush=True)
        provider = worker_cfg.get("provider", "?")
        model    = worker_cfg.get("model", "?")
        has_key  = bool(worker_cfg.get("apiKey")) or provider == "ollama"
        env_hint = ENV_VARS.get(provider, "API key")
        print(f"  LLM:         {provider}/{model} ({'ready' if has_key else f'NO API KEY — set {env_hint}'})", flush=True)
    else:
        print(f"  Mode:        agent trigger (configure {WORKER_CFG} for autonomous)", flush=True)
    print(flush=True)

    state = load_state()

    # On startup: re-queue any tasks that were pending when the daemon last stopped
    if worker_cfg:
        pending = load_pending()
        retries = [t for t in pending if t.get("status") == "pending"]
        if retries:
            log(f"Retrying {len(retries)} pending task(s) from previous session...")
            for task in retries:
                escrow_id = task.get("escrow_id", "")
                task_desc = task.get("task_description", "")
                sender    = task.get("sender", "")
                if not escrow_id or not task_desc:
                    continue
                log(f"  Re-queuing escrow {escrow_id}")
                _task_queue.put((execute_task_autonomously, (
                    escrow_id, task_desc, sender, args.address,
                    args.network, worker_cfg, tg_token, tg_chat_id
                )))

    if args.once:
        poll_once(args.address, args.network, state, tg_token, tg_chat_id,
                  hook_token, hook_path, gw_port, worker_cfg)
        return

    if args.no_websocket:
        print(f"  Connection:  polling every {args.poll_interval}s", flush=True)
        while True:
            state = load_state()
            poll_once(args.address, args.network, state, tg_token, tg_chat_id,
                      hook_token, hook_path, gw_port, worker_cfg)
            time.sleep(args.poll_interval)

    print(f"  Connection:  WebSocket → polling fallback", flush=True)
    print(flush=True)

    ws_available  = True
    reconnect_delay = 5

    while True:
        state = load_state()

        if ws_available:
            should_reconnect = run_websocket(
                args.address, args.network, state, tg_token, tg_chat_id,
                hook_token, hook_path, gw_port, worker_cfg
            )
            if should_reconnect:
                log(f"Reconnecting in {reconnect_delay}s...")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue
            else:
                log(f"Node unavailable — polling every {args.poll_interval}s")
                ws_available = False
                reconnect_delay = 5

        poll_once(args.address, args.network, state, tg_token, tg_chat_id,
                  hook_token, hook_path, gw_port, worker_cfg)
        time.sleep(args.poll_interval)

        # Check if node came back online
        try:
            s = socket.create_connection((WS_HOST, WS_PORT), timeout=3)
            s.close()
            log("Node back online — switching to WebSocket")
            ws_available = True
        except OSError:
            pass


if __name__ == "__main__":
    main()
