# AI fallback runbook

_For the owner: set up JARVIS's AI routes once, and know what to do when the chip in the top
bar changes colour (D-030, as of 2026-10-10)._

The chip at the top right shows the route AI work takes right now:

| Chip | Meaning |
|---|---|
| `● AI · PLAN` (green) | Your Claude plan answers. No API bill. |
| `$ AI · API` (amber) | Your Claude API key answers. This is billed, within the budget you approved. |
| `◐ AI · LOCAL` | Your local model answers. Conversation only; missions wait. |
| `‖ AI · PAUSED` (red) | No allowed route. Missions are paused and continue by themselves once a route is back. |

Click the chip to open **Settings → AI & Billing**. The line under *Route now* says why.

## 1. First setup on the MacBook (once)

### 1a. Your Claude plan (recommended, no extra bill)

1. Settings → AI & Billing → **1 · Claude plan**.
2. If it says *Claude Code not installed*: click **Install Claude Code…**. Terminal opens with
   Anthropic's official installer (`curl -fsSL https://claude.ai/install.sh | bash`). Let it
   finish.
3. Click **Sign in with Claude…**. Terminal runs `claude auth login`. Choose **Claude.ai
   (Pro / Max)** and sign in with *your* account in the browser.
4. Back in JARVIS, press **Check**. The status should read *Connected* or *Not checked yet*,
   and *Claude Code* should show *signed in via claude.ai*.
5. Tick *"Only I use this JARVIS, with my own Claude account"* and press **Use my Claude
   plan**. The chip turns `AI · PLAN`.
6. Optional: **Test** sends one tiny message on your plan.

If the status says **Not supported for JARVIS**, Claude Code is signed in some other way (an
API key, a token, a cloud provider). JARVIS uses the plan only with a claude.ai sign-in. Run
`claude auth logout`, then repeat step 3.

### 1b. Paid fallback with your API key (optional)

Only needed if work should continue when your plan's limit is reached.

1. Create a key at <https://console.anthropic.com/settings/keys>. Set a **spend limit** there
   too (Console → Limits); it is a second safety net.
2. Settings → AI & Billing → **2 · Claude API** → paste the key → **Connect**. JARVIS checks it
   (free) and stores it in the macOS Keychain (*"JARVIS AI"*).
3. Under **Paid fallback**:
   1. Set the *Monthly budget* (e.g. 20) and the *Per mission* cap (e.g. 5).
   2. Keep warnings at 50 / 80 / 100 %.
   3. Keep *Paid jobs at once* = 1.
   4. Keep *Stop at budget* on.
   5. Tick the confirmation and press **Approve paid fallback**.

To stop paying at any time: **Turn off** under Paid fallback, or **Remove** the key.

### 1c. Local model (optional)

Install [Ollama](https://ollama.com), run `ollama pull llama3.2`, then in **3 · Local AI**
enter the model name, turn it on and press **Check**. It is used for conversation only.

## 2. Situations

| What you see | What happened | What JARVIS does | What you do |
|---|---|---|---|
| Plan: *Usage limit reached · until …* | Your plan's session or weekly limit | With paid fallback on: switches to the API within your budget; the chip turns `AI · API`. Otherwise it pauses, and missions continue after the reset. | Nothing. Optionally approve paid fallback. |
| Plan: *Not signed in* | Claude Code's sign-in expired | Fails over, or pauses | **Sign in with Claude…**, then **Check** |
| Plan: *Claude Code not installed* | `claude` not found | Plan skipped | **Install Claude Code…** |
| API: *Credit too low / spend limit* | Console credit used up or the Console spend limit reached | Stops using the API for a while; never buys credit | Add credit or raise the limit in the Console, then **Check key** |
| API: *Key rejected* | Key revoked or wrong | API skipped | Paste a new key |
| API: *Your budget is used up* | Monthly budget reached | No new paid calls; with *Stop at budget* paid fallback switches itself off | Wait for the next month, or approve a new budget deliberately |
| *Unavailable for now* | Overload, rate limit or network after several retries | Waits a short cooldown, then tries again; never switches to paid because of this | Nothing; check your internet |
| Chip `AI · PAUSED`, ULTRON mission *BLOCKED · AI route* | No allowed route right now | Mission keeps its checkpoint; no tool runs twice | Nothing. It resumes by itself, or press *Resume* on the mission. |
| Brain says *"My AI is paused right now."* | As above, for the conversation | Answers again as soon as a route is back | — |

JARVIS re-checks sign-in, limits and the local model every 5 minutes for free. **Check**
buttons do the same immediately.

## 3. Things to know

- **API key in your shell.** If `ANTHROPIC_API_KEY` is set in your shell profile, Claude Code
  in *your* terminal bills the API instead of your plan. JARVIS never passes it to Claude Code,
  so JARVIS's plan route is unaffected.
- **Plan usage isn't shown as a number.** Anthropic offers no interface for it, so JARVIS
  shows a limit only when Claude Code reports one.
- **Max / Team API credits.** Anthropic spends them before purchased credit. JARVIS can't see
  the balance and counts every API call against your budget at list price.
- **Databento** data is bought only after your approval in QuantLab, independent of all of
  this.
- **Everything off:** turn off the plan route and paid fallback. Rules, tools, backtests,
  QuantLab reports and the briefing keep working without AI.

## 4. Where to look

| What | Where |
|---|---|
| Month usage per route and area, recent routing events | Settings → AI & Billing |
| Raw data | `GET http://127.0.0.1:8765/ai/status` and `/ai/usage` (no secrets) |
| Logs | `jarvis/data/logs/` (no keys or tokens are ever logged) |
| Approvals log (append-only) | `ai_approvals` table in `jarvis/data/jarvis.db` |
