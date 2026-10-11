# UX fix log — JARVIS 2.0

Each entry: audit id · change · evidence. Audit: `docs/UX_BUG_AUDIT.md`.

## J2-1 — daily-use blockers and false states (2026-10-11)

| Audit | Change | Evidence |
|---|---|---|
| A-01 | Bots: an idle lab whose setup checks fail says **"Setup needed"** (warning), not "Ready"; "Set up test terminal" explains that MetaTrader 5 must be installed first. | crawl `audit1` |
| A-03 | `Database._migrate` makes a consistent copy (`VACUUM INTO`, WAL included) to `data/backups/jarvis-v<old>-<time>.db` before any pending migration; the newest 5 are kept; a failed copy is logged and the additive migration continues. | `tests/test_database.py` (3 tests) |
| A-04 | ULTRON banner points to Settings → AI & Billing (Claude plan or approved API) instead of "add your Anthropic API key". | — |
| A-05 | `Button` takes a `reason` shown as tooltip and accessible description while disabled; disabled buttons keep pointer events so the tooltip can appear (they used `pointer-events: none`). Reasons added to all 19 buttons the crawler found (17 + "Save strategy", "Import & check"). | crawl `audit1`: 0 disabled buttons without a reason |
| A-09 | Top-bar chip says **"AI · not set up"** when neither the plan route nor paid fallback was ever turned on; a real pause still says "Paused". | `ai.test.ts` |
| A-10 | Learning budget text: counts paid API calls only (verified in `learning/service.py`: plan calls book $0). | code read |
| A-11 | "What time is it?", "Wie spät ist es?", "what's the date" are answered locally from the computer clock. | `test_router.py`; crawl: "It's 12:39 on Sunday, 11 October 2026." |
