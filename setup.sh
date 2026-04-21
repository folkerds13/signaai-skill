#!/bin/bash
# SignaAI Skill Setup — configures exec approvals, listener daemon, and cron job

set -e

SKILL_DIR="${SKILL_DIR:-$HOME/.openclaw/workspace/skills/signaai}"
APPROVALS_FILE="$HOME/.openclaw/exec-approvals.json"
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

# ── 4. Worker config (autonomous daemon) ─────────────────────────────────────
WORKER_CFG="$HOME/.openclaw/signaai-worker.json"
PASSPHRASE=""

echo ""
echo "Worker configuration"
echo "--------------------"

# Load existing passphrase if file exists
if [ -f "$WORKER_CFG" ]; then
  PASSPHRASE=$(python3 -c "import json; d=json.load(open('$WORKER_CFG')); print(d.get('passphrase',''))" 2>/dev/null || echo "")
fi

# Prompt for passphrase if running interactively
if [ -t 0 ]; then
  echo ""
  echo "Your wallet passphrase is needed for autonomous escrow creation and task submission."
  if [ -n "$PASSPHRASE" ]; then
    echo -n "Passphrase [leave blank to keep existing]: "
  else
    echo -n "Passphrase: "
  fi
  read -r INPUT_PASSPHRASE
  [ -n "$INPUT_PASSPHRASE" ] && PASSPHRASE="$INPUT_PASSPHRASE"
fi

# Write config
python3 - <<PYEOF
import json, os
path = "$WORKER_CFG"
data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {}
data["passphrase"] = "$PASSPHRASE"
data["apiKey"]     = data.get("apiKey", "")
# Remove legacy default_worker if present
data.pop("default_worker", None)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
print(f"Worker config saved: {path}")
if data["passphrase"]:
    print("  passphrase: set")
else:
    print("  passphrase: NOT SET — autonomous mode disabled")
PYEOF

echo ""
echo "Done. Restart OpenClaw to apply:"
echo "  openclaw gateway restart"
