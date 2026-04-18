#!/bin/bash
# SignaAI Skill Setup — configures exec approvals for autonomous operation

set -e

SKILL_DIR="${SKILL_DIR:-$HOME/.openclaw/workspace/skills/signaai}"
APPROVALS_FILE="$HOME/.openclaw/exec-approvals.json"

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

# Inject allowlist entries using python3
python3 - <<PYEOF
import json, uuid, os

path = os.path.expanduser("$APPROVALS_FILE")
with open(path) as f:
    data = json.load(f)

# Ensure defaults
data.setdefault("defaults", {})
data["defaults"].update({
    "security": "allowlist",
    "ask": "on-miss",
    "askFallback": "deny",
    "autoAllowSkills": True
})

# Ensure agents.main
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

# Binaries to pre-approve
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

echo ""
# Replace ~ with actual home directory in SKILL.md so exec paths resolve correctly
sed -i '' "s|~/.openclaw|$HOME/.openclaw|g" "$SKILL_DIR/SKILL.md" 2>/dev/null || \
  sed -i "s|~/.openclaw|$HOME/.openclaw|g" "$SKILL_DIR/SKILL.md"
echo "SKILL.md paths resolved to: $HOME/.openclaw"

echo ""
echo "Done. Restart OpenClaw to apply:"
echo "  openclaw gateway restart"
