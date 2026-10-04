# JARVIS — Roadmap

Build vertically: every phase ends with a runnable system, passing tests and
docs that match reality.

| Phase | Name                    | Outcome                                                                 | Status        |
|-------|-------------------------|-------------------------------------------------------------------------|---------------|
| 1     | Foundation              | Dashboard ↔ backend event system; command → route → tool → verify → UI   | **done**      |
| 2     | Real system control     | Volume, window management, file reading, richer context (Win + macOS)   | **mostly done** |
| 3     | Model intelligence      | Provider abstraction live, intent classification, tool calling, persona | **done**      |
| 4     | Missions                | Model planning, multi-step delegation, redirect, per-mission state      | planned       |
| 5     | Screen vision           | Screenshot capture, vision provider, observe-act-verify with pixels     | planned       |
| 6     | Browser                 | Playwright service, persistent session, DOM-aware actions               | planned       |
| 7     | Memory                  | Structured + semantic memory, scoring, people/projects, Memory UI       | **first version** |
| 8     | Voice                   | Local wake word, streaming STT/TTS, barge-in                            | **first version** (pulled ahead) |
| 9     | Specialist agents       | Atlas, Forge, Archive fully online when missions justify them           | planned       |
| 10    | Advanced                | War Room, MCP, calendar/email/GitHub, companion app, proactive engine   | planned       |
| —     | Trading research        | Self-directed learning on NQ / XAUUSD with honest out-of-sample scoring | **first version** |

## Phase 7 — Memory (first version) and the morning briefing

- [x] Memories (preference, fact, routine, correction) in every request; remember / forget
- [x] Memory panel: see, add, forget
- [x] Planted memories need approval after reading files or research notes
- [x] Morning briefing with today's NQ / gold levels and research notes; market_levels in the chat
- [ ] Retrieval instead of the full list once memories outgrow the context
- [ ] People, projects and decisions as structured memory
- [ ] Briefing: calendar and news; spoken version

## Trading research (first version)

- [x] Dukascopy minute data for NQ (Nasdaq-100 CFD) and XAUUSD, cached, validated
- [x] Strategy rule language, look-ahead-free features, conservative backtest with costs
- [x] In-sample / out-of-sample (Bonferroni) / holdout scoring done by code
- [x] Background loop with Claude: backtests, notes, web search; $3/day limit; stops itself after 20 rounds without a finding
- [x] Learning page; "what have you learned?" in the chat (learning_report)
- [ ] Tick data and order flow (Bookmap) for real scalping research
- [ ] Walk-forward re-validation of findings as the holdout grows
- [ ] NQ futures data instead of the CFD proxy

## Phase 8 — Voice (first version, pulled ahead)

- [x] "Hey JARVIS" detected locally (openWakeWord models via onnxruntime)
- [x] Push-to-talk with the microphone button; request end by pause
- [x] ElevenLabs Scribe v2 (speech → text), ElevenLabs voice (text → speech)
- [x] Replies to spoken requests are spoken; optional for typed ones
- [x] Key, wake word and spoken replies set from Settings → Voice
- [ ] Streaming speech (start speaking before the whole reply is synthesized)
- [ ] Barge-in: "Hey JARVIS" interrupts JARVIS while it speaks
- [ ] Voice choice in Settings; local fallback voice without a key

## Phase 1 — Foundation (done)

- [x] FastAPI server, WebSocket event stream, event bus with history
- [x] State service with explicit JARVIS states
- [x] Mission, agent, tool, permission models; SQLite persistence; audit log
- [x] Structured logging with trace/mission ids
- [x] Rule-based router (query / action / conversation / unsupported)
- [x] Real Windows backend (launch + window/process observation) and a
      clearly-labelled simulated backend for non-Windows development
- [x] Electron app with backend supervisor, React dashboard, JARVIS Core,
      mission panel, agents, context, activity stream, command bar,
      approval card, mission detail, settings
- [x] End-to-end: "Open Notepad" → mission → Operator → Sentinel → UI

## Phase 3 — Model intelligence (done, pulled ahead of Phase 2)

- [x] Provider-neutral model interface (`llm/`), Anthropic implementation
- [x] Fast brain (Claude Sonnet 5.5, effort low) and THINK mode (Claude Opus 5.5)
- [x] Instant rule path stays model-free; open-ended requests go to the brain
- [x] Tool calling through Operator → permission gate → Sentinel; side effects
      become verified open-ended missions
- [x] Personality-driven system prompt, working memory (recent exchanges)
- [x] Prompt caching, refusal fallbacks, typed error handling, untrusted-data rules

## Phase 2 — Real system control (in progress)

- [x] Web: `open_url`, `search_web` (browser-in-front verification)
- [x] Files: `find_files`, `list_folder`, `read_file` (text, PDF, Word/RTF),
      `open_file` — folder allowlist, secret patterns, programs never opened
- [x] Windows: `hide_application`, `quit_application` (graceful, approval)
- [x] Sound: `get_volume`, `set_volume`, `media_control`, `now_playing`
- [x] Exposure rule: after reading private data, outbound actions need approval
- [ ] Context: clipboard metadata, monitor layout, window list in the UI
- [ ] Instant rules for the common ones ("lauter", "pause") without a model call
- [ ] Settings page edits permission policy and file roots
- [x] JARVIS as a real Mac app (dock icon, no terminal) with one-click updates

## Order after the first demo

dashboard polish → Windows context → richer tool framework → model integration
→ mission engine → browser → vision → memory → voice → specialist agents.
