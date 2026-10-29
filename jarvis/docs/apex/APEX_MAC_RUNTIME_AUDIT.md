# APEX — Mac runtime audit

**Status: BLOCKED: MAC ACCESS.** The build session ran in a cloud container; the owner's Mac was
not reachable (no folder shared through Remote Control — `list_computer_folders` returned none).
Nothing below was verified on the installed Mac app.

## What was verified instead (Linux container, same code)

| Check | How | Result |
|---|---|---|
| Electron app starts, replaces an outdated backend, exactly one backend | E2E `an outdated backend from an earlier run is replaced`, `app launches, backend starts` (xvfb) | see `APEX_TEST_EVIDENCE.md` |
| Readiness reports a failing component by name (no false "online") | `tests/test_health_ready.py` | passed |
| Backend supervisor restarts after a crash, no duplicates | `apps/desktop/electron/backend.test.ts` (vitest) | passed |
| APEX workstation renders, all tabs work, no orders anywhere | E2E step `APEX: the workstation …` | see `APEX_TEST_EVIDENCE.md` |

## To verify on the Mac (owner, ~10 minutes)

1. Pull branch `claude/jarvis-foundation-mxnsz4` and rebuild the app as before
   (`scripts/install-mac-app.sh`), open JARVIS.
2. The header shows the commit (`v0.1.0 · <short sha>`); it must equal the pushed commit.
3. QuantLab → APEX: choose XAUUSD, press *Start analysis*. Expected: the eight checkpoints, the
   mode chip (DELAYED or HISTORICAL with real data — never LIVE), the chart with S1–S4 / R1–R4,
   and the decision panel showing WAIT or NO TRADE with reasons (no qualified signal can exist
   before a validated, promoted probability head).
4. Activity Monitor: exactly one `python -m jarvis` process.
5. Quit the backend process manually — the app shows the failure with a reason and restarts it.

To let the next session verify this itself: share the JARVIS folder via Remote Control (Claude
desktop app → Settings → Claude Code → Remote Control).
