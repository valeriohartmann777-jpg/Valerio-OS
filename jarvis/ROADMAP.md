# JARVIS — Roadmap

Build vertically: every phase ends with a runnable system, passing tests and
docs that match reality.

| Phase | Name                    | Outcome                                                                 | Status        |
|-------|-------------------------|-------------------------------------------------------------------------|---------------|
| 1     | Foundation              | Dashboard ↔ backend event system; command → route → tool → verify → UI   | **done**      |
| 2     | Real system control     | Volume, window management, file reading, richer context (Win + macOS)   | next          |
| 3     | Model intelligence      | Provider abstraction live, intent classification, tool calling, persona | **done**      |
| 4     | Missions                | Model planning, multi-step delegation, redirect, per-mission state      | planned       |
| 5     | Screen vision           | Screenshot capture, vision provider, observe-act-verify with pixels     | planned       |
| 6     | Browser                 | Playwright service, persistent session, DOM-aware actions               | planned       |
| 7     | Memory                  | Structured + semantic memory, scoring, people/projects, Memory UI       | planned       |
| 8     | Voice                   | Local wake word, streaming STT/TTS, barge-in                            | planned       |
| 9     | Specialist agents       | Atlas, Forge, Archive fully online when missions justify them           | planned       |
| 10    | Advanced                | War Room, MCP, calendar/email/GitHub, companion app, proactive engine   | planned       |

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

## Phase 2 — Real system control (next)

1. Dashboard polish from real-world use on Windows
2. `get_volume` / `set_volume` (Core Audio via `pycaw`), mute
3. Window management: focus, minimize, maximize, close (UI Automation first)
4. `list_directory`, `read_file`, `search_files` (L0, path allowlist)
5. Context: clipboard metadata, cursor, monitor layout, available windows list
6. Router rules for the above; Settings page edits permission policy

## Order after the first demo

dashboard polish → Windows context → richer tool framework → model integration
→ mission engine → browser → vision → memory → voice → specialist agents.
