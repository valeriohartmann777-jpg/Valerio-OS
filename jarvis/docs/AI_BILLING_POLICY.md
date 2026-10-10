# AI billing policy

_What JARVIS may spend on AI, and how it is counted (D-030, as of 2026-10-10)._
Architecture: [`AI_ROUTING_ARCHITECTURE.md`](AI_ROUTING_ARCHITECTURE.md).

## 1. Defaults

| Setting | Default |
|---|---|
| Strategy | **Plan first** (Claude plan → Claude API → local / pause) |
| Claude plan route | **Off** until the owner turns it on and confirms personal use |
| Paid API fallback | **Off.** Nothing is ever billed until the owner approves it with caps |
| Local AI | Off |
| Display currency | USD (the currency Anthropic bills in) |

With nothing set up, JARVIS's AI is offline. Rules, tools and all deterministic work keep
working, and the brain says how to connect.

## 2. Approving paid API fallback

The owner opens **Settings → AI & Billing → Claude API → Paid fallback** and sets these
values:

| Value | Meaning | Validation |
|---|---|---|
| Monthly budget (USD) | Hard cap for all billed calls in a calendar month (UTC) | > 0, ≤ 5,000 |
| Per mission, at most (USD) | Hard cap per ULTRON or QuantLab mission per month | > 0, ≤ monthly budget |
| Warn at | 50 / 80 / 100 % of the monthly budget | Each threshold warns once per month |
| Paid jobs at once | Maximum concurrent paid calls | 1–8 |
| Stop at budget | On: paid fallback switches itself **off** at the budget until approved again. Off: no new paid calls until the month ends | — |

The owner then ticks the explicit confirmation and presses **Approve paid fallback**. The
backend refuses the request without `confirm: true`. Each approval is written to the
append-only approval log with its terms and time. This includes changed limits and the
automatic switch-off at the budget.

JARVIS never raises a limit, extends a budget or tops up credit. No agent, tool or mission can
reach these endpoints.

## 3. What happens before and after every paid call

1. **Before the call.** The router computes the **worst case** of that call: all input at the
   higher of input and cache-write price, plus the full `max_tokens` output. The call does not
   start if any of these holds:
   - the month's billed total plus that worst case would exceed the monthly budget;
   - the mission's billed total plus that worst case would exceed the per-mission cap;
   - the model has no price in `config/ai.yaml`;
   - all paid slots are busy (the call waits for a slot).

   A blocked call pauses the work with the reason; it is not silently degraded.
2. **After the call.** The actual cost is computed from the reported tokens at list price,
   plus $0.01 per web search, and booked in `ai_usage` with `billed = 1`.
3. A call that was started and is billed by Anthropic is always booked, even if it pushes the
   month past the budget. The UI shows the overrun honestly. New paid calls are blocked from
   that moment.
4. Temporary errors (overload, rate limit, network) are retried a bounded number of times on
   the same route. They never start paid use. A failed attempt is recorded with cost 0. The
   cost of a reply is booked once, when it arrives.

Existing per-mission budgets of ULTRON and QuantLab missions still apply on top of these caps.
They now count only calls that were actually billed (route = API).

## 4. What is not billed, and what can't be known

- **Claude plan calls** are not billed by JARVIS. They count against the owner's plan usage.
  `ai_usage` records them with `billed = 0`. Their *list value* (Claude Code's
  `total_cost_usd`, "what this would cost on the API") is shown for information only.
- **Plan usage and remaining limits** have no official interface. The UI shows *"Not
  retrievable — Anthropic reports a limit when it is reached."* A limit, and its reset time
  when given, is shown only after Claude Code reported it.
- **API credits for Max / Team subscribers.** If the plan's organization is linked to a Claude
  Console organization, Anthropic spends the included monthly API credits before purchased
  credits. JARVIS can't read any credit balance (there is no official interface), so it
  doesn't show one. It counts every API call against the owner's budget at list price, which
  makes the budget conservative: included credits are never "free" in JARVIS's accounting.
  Plan limits (Claude / Claude Code) and API credits are separate, and the UI keeps them
  apart.
- **"Credit balance too low" / spend limit reached** pauses the API route (cooldown). JARVIS
  never buys credit.

## 5. Currency

Anthropic bills in USD, so budgets and caps are kept in USD. The owner can display amounts in
CHF at **their own** exchange-rate assumption (Settings → AI & Billing → Currency). The UI
always labels the converted value as CHF and the rate as an assumption. Without a rate,
amounts stay in USD.

## 6. Data purchases are separate

Databento is an independent paid data provider. Claude plan usage or API credit **never pays
for data**. Data downloads keep their own quote and the owner's approval in QuantLab. A model
failover never triggers a purchase, and a resumed mission never buys the same data twice:
purchases are tied to their approval and the cache comes first.

## 7. What never calls a model

The following are code and run with AI offline:

- backtests and the reference / second engines;
- data import, QA and passports;
- validation suites;
- metrics, plots, ledgers and reports;
- the morning briefing and the trained level model;
- the permission system.

## 8. Secrets

- The Claude API key is kept in the OS keystore; only its last four characters are shown.
- It is never logged, never emitted in events or over the WebSocket, never returned by an
  endpoint, never stored in frontend state and never put into a prompt.
- Claude Code runs without `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or
  `CLAUDE_CODE_OAUTH_TOKEN` in its environment.
- JARVIS never reads Claude Code's credentials.

## 9. Sources (checked 2026-10-10)

- Use the Claude Agent SDK with your Claude plan —
  <https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan>
- Log in to your Claude account (permitted use of subscriptions) —
  <https://support.claude.com/en/articles/13189465-log-in-to-your-claude-account>
- Use Claude Code with your Pro or Max plan —
  <https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan>
- API credits for subscribers —
  <https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers>
- Nutzungsguthaben für bezahlte Claude-Pläne verwalten —
  <https://support.claude.com/de/articles/12429409-nutzungsguthaben-fur-bezahlte-claude-plane-verwalten>
