#!/bin/bash
# SignaAI Skill Setup — configures exec approvals, listener daemon, and cron job

set -e

SKILL_DIR="${SKILL_DIR:-$HOME/.openclaw/workspace/skills/signaai}"
APPROVALS_FILE="$HOME/.openclaw/exec-approvals.json"
CRON_FILE="$HOME/.openclaw/cron/jobs.json"
PLIST_FILE="$HOME/Library/LaunchAgents/io.signaai.listener.plist"
LOG_FILE="$HOME/.openclaw/logs/signaai-listener.log"
WORKER_ADDRESS=""

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --address) WORKER_ADDRESS="$2"; shift 2 ;;
    *) shift ;;
  esac
done

echo "SignaAI Skill Setup"
echo "==================="

# Find python3
PYTHON3=$(which python3 2>/dev/null || echo "")
if [ -z "$PYTHON3" ]; then
  echo "ERROR: python3 not found. Install it first."
  exit 1
fi
echo "python3: $PYTHON3"

# Find git
GIT=$(which git 2>/dev/null || echo "")
if [ -z "$GIT" ]; then
  echo "ERROR: git not found. Install it first."
  exit 1
fi
echo "git:     $GIT"

# Find ls
LS=$(which ls 2>/dev/null || echo "/bin/ls")
echo "ls:      $LS"

# Check approvals file exists
if [ ! -f "$APPROVALS_FILE" ]; then
  echo "ERROR: $APPROVALS_FILE not found. Is OpenClaw installed?"
  exit 1
fi

# ── 1. Exec approvals ─────────────────────────────────────────────────────────
echo ""
echo "Configuring exec approvals..."
python3 - <<PYEOF
import json, uuid, os

path = os.path.expanduser("$APPROVALS_FILE")
with open(path) as f:
    data = json.load(f)

data.setdefault("defaults", {})
data["defaults"].update({
    "security": "allowlist",
    "ask": "on-miss",
    "askFallback": "deny",
    "autoAllowSkills": True
})

data.setdefault("agents", {})
data["agents"].setdefault("main", {})
data["agents"]["main"].update({
    "security": "allowlist",
    "ask": "on-miss",
    "askFallback": "deny",
    "autoAllowSkills": True
})
data["agents"]["main"].setdefault("allowlist", [])

allowlist = data["agents"]["main"]["allowlist"]

entries = [
    ("$LS",      "ls ~/.openclaw/workspace/skills/"),
    ("$PYTHON3", "python3 $SKILL_DIR/scripts/wallet.py"),
    ("$GIT",     "git -C $SKILL_DIR pull origin main"),
]

for pattern, cmd in entries:
    already = any(e.get("pattern") == pattern for e in allowlist)
    if not already:
        allowlist.append({
            "id": str(uuid.uuid4()).upper(),
            "pattern": pattern,
            "lastUsedAt": 0,
            "lastUsedCommand": cmd,
            "lastResolvedPath": pattern
        })
        print(f"  Added: {pattern}")
    else:
        print(f"  Already approved: {pattern}")

with open(path, "w") as f:
    json.dump(data, f, indent=2)

print("exec-approvals.json updated.")
PYEOF

# ── 2. Resolve tilde paths in SKILL.md ────────────────────────────────────────
echo ""
sed -i '' "s|~/.openclaw|$HOME/.openclaw|g" "$SKILL_DIR/SKILL.md" 2>/dev/null || \
  sed -i "s|~/.openclaw|$HOME/.openclaw|g" "$SKILL_DIR/SKILL.md"
echo "SKILL.md paths resolved to: $HOME/.openclaw"

# ── 3. Listener launchd daemon (macOS only) ───────────────────────────────────
if [ "$(uname)" = "Darwin" ]; then
  echo ""
  echo "Setting up listener daemon..."

  # Prompt for wallet address only if running interactively and none provided
  if [ -z "$WORKER_ADDRESS" ] && [ -t 0 ]; then
    echo ""
    echo "Which wallet should this machine monitor for incoming tasks?"
    echo "  (Find yours: python3 $SKILL_DIR/scripts/wallet.py --network mainnet myaddress <passphrase>)"
    echo -n "Wallet address [leave blank to skip]: "
    read -r WORKER_ADDRESS
  fi

  if [ -n "$WORKER_ADDRESS" ]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.signaai.listener</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON3</string>
        <string>-u</string>
        <string>$SKILL_DIR/scripts/listener.py</string>
        <string>--address</string>
        <string>$WORKER_ADDRESS</string>
        <string>--network</string>
        <string>mainnet</string>
        <string>--poll-interval</string>
        <string>120</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>
    <key>WorkingDirectory</key>
    <string>$SKILL_DIR/scripts</string>
</dict>
</plist>
PLIST

    # Unload existing if present, then load
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load "$PLIST_FILE"
    echo "  Listener started — watching $WORKER_ADDRESS"
    echo "  Log: $LOG_FILE"
  else
    echo "  Skipped. To set up later: re-run setup.sh"
  fi
fi

# ── 4. OpenClaw cron job ───────────────────────────────────────────────────────
echo ""
echo "Configuring OpenClaw cron job..."
python3 - <<PYEOF
import json, uuid, os, time

cron_path = "$CRON_FILE"
if not os.path.exists(cron_path):
    print("  Skipped — $CRON_FILE not found (OpenClaw not fully initialized yet).")
    print("  Re-run setup.sh after first OpenClaw launch to add the cron job.")
    exit(0)

with open(cron_path) as f:
    data = json.load(f)

data.setdefault("jobs", [])

JOB_NAME = "SignaAI — Process Pending Tasks (every 5 min)"
already = any(j.get("name") == JOB_NAME for j in data["jobs"])

if already:
    print(f"  Already configured: {JOB_NAME}")
else:
    pending_file = os.path.expanduser("$HOME/.openclaw/workspace/signaai-pending-tasks.json")
    now_ms = int(time.time() * 1000)
    data["jobs"].append({
        "id": str(uuid.uuid4()),
        "agentId": "main",
        "sessionKey": "agent:main:main",
        "name": JOB_NAME,
        "enabled": True,
        "createdAtMs": now_ms,
        "updatedAtMs": now_ms,
        "schedule": {
            "kind": "cron",
            "expr": "*/5 * * * *",
            "tz": "America/New_York"
        },
        "sessionTarget": "isolated",
        "wakeMode": "now",
        "payload": {
            "kind": "agentTurn",
            "message": f"Check {pending_file} for any tasks with status 'pending'. If none exist or the file doesn't exist, do nothing and stop. If a pending task is found, process the first one using the SignaAI skill: research the task topic thoroughly, stamp your answer on-chain with verify.py, wait 4 minutes for block confirmation, self-verify the stamp, submit the result to escrow with escrow.py, then mark the task complete in the file. Do NOT fabricate TX IDs — if any script fails, stop and report which step failed.",
            "timeoutSeconds": 600
        },
        "delivery": {"mode": "announce"},
        "state": {
            "lastRunAtMs": 0,
            "lastStatus": "ok",
            "lastDurationMs": 0,
            "consecutiveErrors": 0
        }
    })

    with open(cron_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Added: {JOB_NAME}")

PYEOF

echo ""
echo "Done. Restart OpenClaw to apply:"
echo "  openclaw gateway restart"
