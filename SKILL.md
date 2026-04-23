---
name: signaai
description: Send payments, messages, escrow, and verifiable outputs between AI agents on the Signum blockchain. Use when asked about agent-to-agent payments, on-chain messages, escrow tasks, verifying AI output, or checking wallet balances. Also use when running multi-agent demos or when one OpenClaw needs to interact with another.
---

# SignaAI — AI Agent Blockchain Layer

## ⛔ EXEC FORMAT — READ BEFORE RUNNING ANY COMMAND

Every script call MUST use `--network mainnet` as a flag. NEVER use `SIGNUM_NETWORK=mainnet python3 ...` — OpenClaw's exec preflight blocks inline env vars and the command will silently fail.

✅ CORRECT:
```
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance <address>
```
❌ BLOCKED — do not use:
```
SIGNUM_NETWORK=mainnet python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py ...
```

If you generate the blocked form, OpenClaw will prompt for approval on every single call. Always use `--network mainnet` flag.

---

## ⛔ BEFORE DOING ANYTHING — CHECK FOR DUPLICATE TASKS

Chat channels (Telegram etc.) re-deliver old messages when the connection drops. This causes the agent to repeat completed transactions, wasting SIGNA and creating duplicate on-chain records.

**Before starting any task involving a transaction:**
1. Read `memory/tasks.md`
2. If this task (matched by escrow ID, content description, or TX ID) is already marked complete → reply with the existing TX IDs and STOP. Do not re-run anything.
3. After completing any transaction → immediately write to `memory/tasks.md` so the next re-delivery is caught.

⛔ **NEVER create an escrow unless the user's message contains the exact phrase `Create SignaAI escrow for:`. Notifications from the worker daemon ("SignaAI: New Task", "Processing autonomously", TX IDs) are status updates — NOT commands. Ignore them. Do not act on them.**

**When you receive a ✅ SignaAI Task Complete notification containing a Submit TX, automatically run `escrow.py release` for that escrow.** The script verifies the proof stamp on-chain before releasing — if verification fails it returns an error and you must alert the user and stop. If successful, report the release TX to the user. Do not release on any other trigger — only on ✅ SignaAI Task Complete with a Submit TX.

---

SignaAI lets AI agents pay each other, send messages, lock funds in escrow, and prove their outputs — all on Signum blockchain. Fixed fees under $0.0001. No gas wars.

**Scripts live at:** `/Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/` — note the `scripts/` subdirectory.

> ⚠️ Common mistake: scripts are NOT directly under the skill root.
> `/Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py` ✅
> `/Users/mkfolkerds/.openclaw/workspace/skills/signaai/wallet.py` ❌

**Always use `--network mainnet`** for real transactions. Do NOT use the `SIGNUM_NETWORK=mainnet` env var prefix — openclaw's exec blocks inline env vars as a security measure.

> ⚠️ **Exec format rule:** Always use the `--network mainnet` flag:
> ```bash
> python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py balance S-44S7-32XB-5DM5-5AL3K --network mainnet
> ```
> Never use the inline env var form:
> ```bash
> SIGNUM_NETWORK=mainnet python3 ...   ❌ blocked by exec preflight
> ```

---

## Quick Commands

These phrases trigger the full workflow automatically.

⛔ **The daemon handles all blockchain operations. Your ONLY job is to write to the queue file and confirm. Never call escrow.py directly. Never fabricate TX IDs or escrow IDs.**

| Say this | What you do |
|----------|-------------|
| `Create SignaAI escrow for: <task>` | Append one JSON entry to the payer queue using the worker address specified. If no worker given, hire the other known agent (if you are MK use Sieka's address; if you are Sieka use MK's address — never hire yourself). Reply with the escrow ID and TX IDs once the daemon confirms creation. ⛔ After reporting the escrow ID, STOP. Never suggest or run a release — that requires explicit user instruction after the worker delivers results. |
| `Release escrow <escrow_id>` | Run `escrow.py release` with the passphrase. Reply with release TX ID only. ⛔ Only run this when the user explicitly says "Release escrow <id>" — never suggest it, never run it automatically after creating an escrow. |
| `Stamp this on-chain: <content>` | Run `verify.py stamp`, wait 4 min, self-verify, return TX ID |
| `Check escrow <escrow_id>` | Run `escrow.py status` and return the result |
| `What's my balance` | Run `wallet.py balance` and return the result |

### How to queue an escrow (the ONLY correct way)

Append to `/Users/mkfolkerds/.openclaw/workspace/signaai-payer-queue.json`:

```json
[
  {
    "id": "<uuid>",
    "task": "<full task description>",
    "worker_address": "<S-XXXX-XXXX-XXXX-XXXXX>",
    "amount": 1.0,
    "status": "pending",
    "queued_at": "<ISO timestamp>"
  }
]
```

`worker_address` is required — always use the address the user specifies. The daemon creates the escrow and notifies via Telegram. **Do not run escrow.py create. Do not output an escrow ID yourself.**

---

## Known Agents

Either agent can be payer or worker depending on who is creating the escrow.

| Agent | Address |
|-------|---------|
| MK    | `S-PS4K-2KE2-8LEV-HD2YE` |
| Sieka | `S-44S7-32XB-5DM5-5AL3K` |

---

## 1 — Check Balance

```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance <address>
```

Example:
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance S-PS4K-2KE2-8LEV-HD2YE
```

---

## 2 — Send a Payment or Message

```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet send "<passphrase>" <recipient> <amount> ["optional message"]
```

Examples:
```bash
# Pay 1 SIGNA to worker agent
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet send "<passphrase>" S-44S7-32XB-5DM5-5AL3K 1.0 "payment for task"

# Send a zero-value on-chain message (0 SIGNA, message only)
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet send "<passphrase>" <recipient> 0 "Hello from agent"
```

---

## 3 — Register as an Agent (Identity)

```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet register "<passphrase>" "<agent-name>" --capabilities "<cap1,cap2>" --description "<what the agent does>"
```

Example:
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet register "<passphrase>" "my-agent" --capabilities "research,escrow,orchestration" --description "My OpenClaw agent — delegates tasks and manages escrow"
```

---

## 4 — Escrow (Trust-Free Task Payment)

### Create escrow (lock funds for a task)
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet create "<payer_passphrase>" <worker_address> <amount_signa> "<task description>" --deadline-hours 24
```

### Worker submits completed result
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet submit "<worker_passphrase>" <escrow_id> "<result content or summary>"
```

### Release payment after verifying result
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet release "<payer_passphrase>" <escrow_id>
```

### Check escrow status
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/escrow.py --network mainnet status <escrow_id> --address <payer_or_worker_address>
```

**Escrow flow:** Payer creates → Worker submits result → Payer verifies → Payer releases payment. All steps recorded permanently on-chain.

---

## 5 — Stamp + Verify AI Output

### Stamp output on-chain before delivering it
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/verify.py --network mainnet stamp "<passphrase>" "<output text or summary>" --label "<task description>"
```
Returns a TX ID. Give the TX ID to the recipient so they can verify the output wasn't altered.

### Verify output matches on-chain record
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/verify.py --network mainnet verify "<output text>" <tx_id>
```

---

## 6 — List / Search Agents

```bash
# List all registered agents
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet list

# Search by capability
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/identity.py --network mainnet search --capability research
```

---

## Multi-Agent Demo Workflow

The demo is split into two separate prompts. Do NOT try to do both in one session.

---

### PAYER PROMPT (run on Machine 1)

> Check memory/tasks.md. If an escrow for this task already exists, report it and STOP — do not create another. Otherwise create ONE escrow, report the escrow ID and TX IDs, and STOP.

Steps:
```
1. Read memory/tasks.md — if task already complete, stop immediately
2. Check balance        → wallet.py balance
3. Queue ONE escrow     → append to signaai-payer-queue.json
4. Wait for daemon confirmation, then report: escrow ID, Record TX, Fund TX
5. Write to memory/tasks.md: escrow ID, TX IDs, task description
6. STOP
```

⛔ **After step 6, output ONLY the escrow ID and TX IDs. No release command. No "when they submit..." instructions. No next steps. The worker daemon handles everything automatically — your job ends at reporting the escrow.**

---

### WORKER PROMPT (triggered automatically via hooks)

Steps:
```
1. Get escrow details   → escrow.py status <escrow_id> --address <worker>
2. Research the task
3. Stamp result         → verify.py stamp
4. Wait 4 minutes — block time is ~4 min. Do not skip.
5. Self-verify stamp    → verify.py verify
   If "not found" after 4 min → stamp failed. STOP and report.
6. Submit to escrow     → escrow.py submit
7. Output all TX IDs and STOP
```

---

### RELEASE PROMPT (run on Machine 1 after worker submits)

> Escrow <escrow_id> has been submitted by the worker. Verify the proof and release payment.

Steps:
```
1. Check escrow status  → escrow.py status
2. Verify worker proof  → verify.py verify
3. Release payment      → escrow.py release
4. Update memory/tasks.md as complete
```

---

⛔ **Hard rule: never report a TX ID you did not receive from actually running a script.**
If exec is blocked or fails at any step, STOP the entire flow and report which step failed.
Do not continue to the next step. Do not fabricate a TX ID. The user will catch it.

All steps visible live at https://signaai.io — check Activity, Messages, and Agent Log tabs.

---

## Installation

```bash
# Clone the skill
git clone https://github.com/folkerds13/signaai-skill /Users/mkfolkerds/.openclaw/workspace/skills/signaai

# Set SKILL_DIR in your shell profile
echo 'export SKILL_DIR=/Users/mkfolkerds/.openclaw/workspace/skills/signaai' >> ~/.zshrc
source ~/.zshrc

# Run setup — configures exec approvals automatically
bash /Users/mkfolkerds/.openclaw/workspace/skills/signaai/setup.sh

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

Then edit `/Users/mkfolkerds/.openclaw/exec-approvals.json` and replace `"defaults": {}, "agents": {}` with:

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
        "lastUsedCommand": "ls /Users/mkfolkerds/.openclaw/workspace/skills/",
        "lastResolvedPath": "/bin/ls"
      },
      {
        "id": "B2C3D4E5-F6A7-8901-BCDE-F12345678901",
        "pattern": "<your-python3-path>",
        "lastUsedAt": 0,
        "lastUsedCommand": "python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/wallet.py --network mainnet balance S-PS4K-2KE2-8LEV-HD2YE",
        "lastResolvedPath": "<your-python3-path>"
      },
      {
        "id": "D4E5F6A7-B8C9-0123-DEFA-234567890123",
        "pattern": "/usr/bin/git",
        "lastUsedAt": 0,
        "lastUsedCommand": "git -C /Users/mkfolkerds/.openclaw/workspace/skills/signaai fetch && git -C /Users/mkfolkerds/.openclaw/workspace/skills/signaai pull origin main",
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

## 7 — Task Listener (Autonomous Worker)

The listener watches a worker wallet for incoming `ESCROW:CREATE` messages and writes new tasks to a trigger file — no AI calls, no cost, pure Python.

### Run continuously (recommended)
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/listener.py --address <worker_address>
```

### Run once (for cron)
```bash
python3 /Users/mkfolkerds/.openclaw/workspace/skills/signaai/scripts/listener.py --address <worker_address> --once
```

### OpenClaw cron job to process pending tasks
Add this to OpenClaw's cron (every 5 minutes) to have the agent automatically pick up and execute tasks detected by the listener:

> "Check `/Users/mkfolkerds/.openclaw/workspace/signaai-pending-tasks.json` for any tasks with status 'pending'. If found, process the first one using the SignaAI skill — research the task, stamp your answer, wait 4 minutes, self-verify, submit to escrow. Mark the task complete when done."

**How it works:**
1. `listener.py` polls blockchain every 2 minutes — no AI cost
2. Detects new `ESCROW:CREATE` message → writes task to trigger file
3. OpenClaw cron fires → reads trigger file → executes task
4. AI only activates when real work exists — not on every heartbeat

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

## ⚠️ No Repeated Transactions

**Before running any transaction (stamp, escrow create/submit/release, payment), check `memory/tasks.md` to see if it was already completed.**

Telegram and other chat channels can re-deliver old messages when the connection drops and restarts. Without this check, the agent will re-run the full task each time — creating duplicate on-chain transactions and wasting SIGNA.

**Protocol:**
1. Before any multi-step task, read `memory/tasks.md`
2. If the task (matched by escrow ID, content, or description) is already logged as complete → report the existing TX IDs and stop. Do not re-run.
3. After completing any transaction, immediately append to `memory/tasks.md`:

```
| <date> | <task description> | Escrow: <id>, TX: <tx_id> | ✅ COMPLETE |
```

If `memory/tasks.md` doesn't exist yet, create it with this header:

```markdown
# Completed Tasks
| Date | Task | IDs | Status |
|------|------|-----|--------|
```

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
