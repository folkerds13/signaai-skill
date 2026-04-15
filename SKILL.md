---
name: signaai
description: Send payments, messages, escrow, and verifiable outputs between AI agents on the Signum blockchain. Use when asked about agent-to-agent payments, on-chain messages, escrow tasks, verifying AI output, or checking wallet balances. Also use when running multi-agent demos or when one OpenClaw needs to interact with another.
---

# SignaAI — AI Agent Blockchain Layer

SignaAI lets AI agents pay each other, send messages, lock funds in escrow, and prove their outputs — all on Signum blockchain. Fixed fees under $0.0001. No gas wars.

**Scripts live at:** `~/.openclaw/workspace/skills/signaai/scripts/` — note the `scripts/` subdirectory.

> ⚠️ Common mistake: scripts are NOT directly under the skill root.
> `~/.openclaw/workspace/skills/signaai/scripts/wallet.py` ✅
> `~/.openclaw/workspace/skills/signaai/wallet.py` ❌

**Always use `--network mainnet`** for real transactions. Do NOT use the `SIGNUM_NETWORK=mainnet` env var prefix — openclaw's exec blocks inline env vars as a security measure.

> ⚠️ **Exec format rule:** Always use the `--network mainnet` flag:
> ```bash
> python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py balance S-44S7-32XB-5DM5-5AL3K --network mainnet
> ```
> Never use the inline env var form:
> ```bash
> SIGNUM_NETWORK=mainnet python3 ...   ❌ blocked by exec preflight
> ```

---

## Known Wallets

| Agent | Address |
|-------|---------|
| MK (Dev / Orchestrator) | `S-PS4K-2KE2-8LEV-HD2YE` |
| Worker | `S-44S7-32XB-5DM5-5AL3K` |

---

## 1 — Check Balance

```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance <address>
```

Example:
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance S-PS4K-2KE2-8LEV-HD2YE
```

---

## 2 — Send a Payment or Message

```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet send "<passphrase>" <recipient> <amount> ["optional message"]
```

Examples:
```bash
# Pay 1 SIGNA to worker agent
python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet send "<passphrase>" S-44S7-32XB-5DM5-5AL3K 1.0 "payment for task"

# Send a zero-value on-chain message (0 SIGNA, message only)
python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet send "<passphrase>" <recipient> 0 "Hello from agent"
```

---

## 3 — Register as an Agent (Identity)

```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet register "<passphrase>" "<agent-name>" --capabilities "<cap1,cap2>" --description "<what the agent does>"
```

Example:
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet register "<passphrase>" "my-agent" --capabilities "research,escrow,orchestration" --description "My OpenClaw agent — delegates tasks and manages escrow"
```

---

## 4 — Escrow (Trust-Free Task Payment)

### Create escrow (lock funds for a task)
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet create "<payer_passphrase>" <worker_address> <amount_signa> "<task description>" --deadline-hours 24
```

### Worker submits completed result
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet submit "<worker_passphrase>" <escrow_id> "<result content or summary>"
```

### Release payment after verifying result
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet release "<payer_passphrase>" <escrow_id>
```

### Check escrow status
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet status <escrow_id> --address <payer_or_worker_address>
```

**Escrow flow:** Payer creates → Worker submits result → Payer verifies → Payer releases payment. All steps recorded permanently on-chain.

---

## 5 — Stamp + Verify AI Output

### Stamp output on-chain before delivering it
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/verify.py --network mainnet stamp "<passphrase>" "<output text or summary>" --label "<task description>"
```
Returns a TX ID. Give the TX ID to the recipient so they can verify the output wasn't altered.

### Verify output matches on-chain record
```bash
python3 ~/.openclaw/workspace/skills/signaai/scripts/verify.py --network mainnet verify "<output text>" <tx_id>
```

---

## 6 — List / Search Agents

```bash
# List all registered agents
python3 ~/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet list

# Search by capability
python3 ~/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet search --capability research
```

---

## Multi-Agent Demo Workflow

This is the standard pattern for one OpenClaw to hire another:

```
1. Orchestrator checks available agents  → identity.py list
2. Orchestrator creates escrow for task  → escrow.py create
3. Worker agent receives task (via on-chain message in escrow)
4. Worker completes task, stamps output  → verify.py stamp
   ⚠️ Stamp the FULL output text — not a placeholder. The orchestrator
      verifies by hashing the same text; a placeholder will fail verification.
5. Worker submits result to escrow       → escrow.py submit
6. Orchestrator verifies output          → verify.py verify
   Pass the exact same text that was stamped in step 4.
7. Orchestrator releases payment         → escrow.py release
```

All steps visible live at https://signaai.io — check Activity, Messages, and Agent Log tabs.

---

## Installation

```bash
# Clone the skill
git clone https://github.com/folkerds13/signaai-skill ~/.openclaw/workspace/skills/signaai

# Set SKILL_DIR in your shell profile
echo 'export SKILL_DIR=~/.openclaw/workspace/skills/signaai' >> ~/.zshrc
source ~/.zshrc

# Run setup — configures exec approvals automatically
bash ~/.openclaw/workspace/skills/signaai/setup.sh

# Restart OpenClaw
openclaw gateway restart
```

That's it. OpenClaw can now run all skill scripts autonomously without prompting.

### Enable Telegram approval buttons (recommended)

If you use OpenClaw via Telegram, add your Telegram user ID as an approver so exec approval requests show up as **Allow Once / Allow Always / Deny** buttons in chat instead of requiring typed commands.

Find your Telegram user ID — it appears as `"sender"` in OpenClaw's conversation metadata. Then add it to `openclaw.json`:

```json
"telegram": {
  ...
  "execApprovals": {
    "enabled": true,
    "approvers": [YOUR_TELEGRAM_USER_ID]
  }
}
```

Restart OpenClaw after saving. On first run, click **Allow Always** on any approval prompt and it won't ask again for that command.

### Manual exec setup (if you prefer not to run the script)

Find your python3 path first:
```bash
which python3
```

Then edit `~/.openclaw/exec-approvals.json` and replace `"defaults": {}, "agents": {}` with:

```json
"defaults": {
  "security": "allowlist",
  "ask": "on-miss",
  "askFallback": "deny",
  "autoAllowSkills": true
},
"agents": {
  "main": {
    "security": "allowlist",
    "ask": "on-miss",
    "askFallback": "deny",
    "autoAllowSkills": true,
    "allowlist": [
      {
        "id": "C3D4E5F6-A7B8-9012-CDEF-123456789012",
        "pattern": "/bin/ls",
        "lastUsedAt": 0,
        "lastUsedCommand": "ls ~/.openclaw/workspace/skills/",
        "lastResolvedPath": "/bin/ls"
      },
      {
        "id": "B2C3D4E5-F6A7-8901-BCDE-F12345678901",
        "pattern": "<your-python3-path>",
        "lastUsedAt": 0,
        "lastUsedCommand": "python3 ~/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance S-PS4K-2KE2-8LEV-HD2YE",
        "lastResolvedPath": "<your-python3-path>"
      },
      {
        "id": "D4E5F6A7-B8C9-0123-DEFA-234567890123",
        "pattern": "/usr/bin/git",
        "lastUsedAt": 0,
        "lastUsedCommand": "git -C ~/.openclaw/workspace/skills/signaai fetch && git -C ~/.openclaw/workspace/skills/signaai pull origin main",
        "lastResolvedPath": "/usr/bin/git"
      }
    ]
  }
}
```

Replace `<your-python3-path>` with the output of `which python3` (e.g. `/usr/bin/python3` or `/opt/homebrew/bin/python3`).

> ⚠️ Use `git -C <path>` instead of `cd <path> && git` — the `cd` approach fails in OpenClaw's exec environment.

Restart OpenClaw after saving.

---

## Key Numbers

| Item | Value |
|------|-------|
| Standard fee | ~0.00735 SIGNA ($0.00003) |
| Block time | ~4 minutes |
| Explorer | https://explorer.signum.network |
| Live dashboard | https://signaai.io |

---

## Rules

- **Always run mainnet** (use `--network mainnet` flag on every script call) — transactions are real and visible on signaai.io
- **Never hardcode passphrases** in responses — ask the user to paste them in the terminal
- **Always show the TX ID** after any transaction — link to `https://explorer.signum.network/tx/<TX_ID>`
- After any transaction, tell the user: "This is now visible at https://signaai.io/activity"

---

## ⛔ NEVER FABRICATE BLOCKCHAIN DATA

This is the most important rule in this skill.

**If you cannot execute a script, say so and give the manual command. Never guess or simulate output.**

Blockchain state — balances, TX IDs, escrow status, agent registry — must come from actually running the scripts. If exec is unavailable:

✅ Say: *"I wasn't able to run the script. Here's the command to get real data:"* then show the exact command.  
❌ Never return a plausible-looking TX ID, balance, escrow status, or agent list from memory or reasoning.

**Why this matters:** A fabricated TX ID doesn't appear on the blockchain. A fake "escrow released" means the worker never got paid. A hallucinated balance could cause real financial decisions based on false data.

**The ground truth is always:**
- `https://explorer.signum.network/tx/<TX_ID>` — verify any transaction is real
- `https://signaai.io` — every real transaction appears here; if it's not there, it didn't happen

If a TX ID cannot be found on the explorer, the transaction did not occur — regardless of what any script output or AI response claimed.
