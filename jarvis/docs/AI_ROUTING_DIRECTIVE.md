<!-- The owner's brief for D-030, kept verbatim (uploaded 2026-10-10). -->

# JARVIS / ULTRON / QUANTLAB — SMART AI ROUTING & BILLING
## Claude Code: Verbindlicher Implementierungsauftrag

**Projekt:** bestehendes JARVIS-Repository (vermutlich `C:\dev\jarvis`)
**Modul:** AI Access, Provider Routing, Fallback, Usage & Cost Control
**Priorität:** Hoch – betrifft sämtliche JARVIS- und ULTRON-Agenten sowie QuantLab.
**Stand:** 10. Oktober 2026. Authentifizierung und Anthropic-Nutzungsregeln vor Umsetzung nochmals anhand offizieller aktueller Dokumentation prüfen.

### 0. Auftrag

Erweitere das bestehende JARVIS-System um einen robusten **Smart AI Router**. Das Ziel ist, dass JARVIS und ULTRON möglichst ohne Unterbrechung weiterarbeiten, während Modellnutzung, Gebühren, Authentifizierung und Limits **transparent, sicher und regelkonform** behandelt werden.

Der Nutzer programmiert nicht selbst. Untersuche die existierende Codebasis und implementiere die Funktion vollständig mit UI, Backend, Tests und Dokumentation, ohne JARVIS, ULTRON oder QuantLab zu beschädigen.

**Gewünschte Präferenzreihenfolge:**

1. **Claude-Plan (subscription-first)**: Nutze die offiziell zulässige, für die konkrete Anwendung verfügbare Claude-Abonnement-Integration, wenn unterstützt. Der Nutzer soll keine zusätzliche API-Rechnung bezahlen, solange erlaubte Abo-Nutzung verfügbar ist.
2. **Claude API**: Fallback über eigenen Claude Console API-Key, nur wenn dieser vom Nutzer hinterlegt wurde und der Nutzer API-Fallback innerhalb eines konkreten Ausgabenlimits aktiviert hat. In berechtigten Claude Max-/Team-Organisationen werden die verknüpften monatlichen API-Gutschriften durch Anthropic vor gekauften API-Credits verrechnet. Verwende keine selbst gebaute Kontostands-Umbuchung.
3. **Local / offline mode**: Falls beide Remote-Wege nicht funktionieren oder das Kostenlimit erreicht ist, den Auftrag persistent pausieren und deterministische lokale Funktionen weiterhin anbieten. Ein explizit eingerichtetes lokales LLM darf als kompatibler, klar gekennzeichneter Fallback für geeignete Aufgaben verwendet werden. Falls keine passende KI verfügbar ist, keine Antworten oder Tests erfinden.

**Kritische Compliance-Regel:** Die öffentlich dokumentierten Anthropic-Bestimmungen sind nicht überall gleich formuliert: Anthropic hat am 7. Oktober 2026 erklärt, dass Claude Agent SDK, `claude -p` und Drittanbieter-Apps weiterhin Abo-Limits verwenden können. Das Login-/Policy-Dokument warnt zugleich davor, Drittanbieter-Traffic durch Client-Imitation oder Umgehungen auf Abo-Limits zu routen. Verwende **ausschliesslich offiziell erlaubte Authentifizierungswege und dokumentierte SDK-/CLI-Funktionen für den tatsächlich gewählten Anwendungsfall**. Keine kopierten OAuth-Tokens, kein Scraping von Anmeldeinformationen, keine manipulierten Headers/Clients, keine verkleideten API-Anfragen. Wenn der Abo-Zugriff für die eigene JARVIS-Anwendung nicht eindeutig autorisiert bzw. unterstützt ist, in JARVIS den Abo-Provider als nicht verfügbar kennzeichnen und direkt den legitimen API-Weg anbieten. Implementiere keine Umgehung.

**Wichtig: Entkopple Claude Code als Entwicklungswerkzeug von JARVIS als eigenständiger Laufzeitanwendung.** Die Tatsache, dass Claude Code mit einem Abo läuft, bedeutet nicht automatisch, dass jede API-Anfrage einer eigenen Desktop-App vom Abo gedeckt ist. In Claude Code hat eine gesetzte `ANTHROPIC_API_KEY`-Umgebungsvariable gegenüber der Login-Session Vorrang; verhindere unbeabsichtigte API-Belastungen durch saubere Prozess-/Umgebungsisolation.

### 1. Produktverhalten

- Der Nutzer stellt eine Anfrage in JARVIS oder QuantLab oder startet eine ULTRON-Mission.
- Der Router ermittelt die benötigte Fähigkeit (einfache Sprache, multimodale Analyse, Planning, Coding, Quant-Research, Validierung) und prüft zulässige Anbieter.
- Subscription-First: **nur wenn verfügbar und entsprechend den offiziellen Nutzungsbedingungen zulässig**.
- Bei nachweisbarem Abo-Nutzungslimit/Quotenfehler werden laufende Missionen mit Checkpoints angehalten und auf den **bereits aktivierten** Claude-API-Provider umgeleitet. Beim erneuten Aufruf werden die Ergebnisse idempotent behandelt; keine doppelte Tool-Ausführung, kein erneuter Databento-Kauf, kein doppelter Datei-Schreibvorgang.
- Nach einer nachgewiesenen Wiederverfügbarkeit kann der Router bei einer neuen Task-Grenze automatisch auf die Abo-Priorität zurückwechseln. Keine Authentifizierungsmischung innerhalb einer ungesicherten Transaktion.
- Bei Timeouts, 429s oder Serverfehlern differenzieren zwischen *temporärer Überlastung*, *Rate-Limit*, *Abo-Nutzungslimit*, *Authentifizierungsfehler*, *ungültigem Key*, *unzureichendem API-Guthaben*, *Policy nicht verfügbar* und *lokaler Netzunterbrechung*.
- Nutze begrenzte Retries und Backoff. Schalte **nicht** wegen eines einzelnen Netzwerkfehlers kostenpflichtig um, ohne einen klaren Umschaltgrund und aktiviertes Budget.

### 2. Router-Architektur

Schlage die konkrete Architektur nach Repository-Inspektion vor und implementiere danach mindestens:

- `AIProvider`-Interface mit `capabilities`, `health`, `invoke`, `stream`, `usage`, `error_mapping`.
- `ClaudeSubscriptionAdapter` **nur falls offiziell und technisch unterstützt**; separate Ausführungsidentität und keine API-Key-Leaks.
- `ClaudeApiAdapter` mit offiziellem Anthropic-SDK; API-Key in Windows Credential Manager oder gleichwertiger OS-geschützter Speicherung.
- `LocalModelAdapter` optional, sauber als installiert/nicht installiert kennzeichnen.
- `SmartRouter` mit Capability-Matching, Priorität, verfügbaren Modellen, Quoten-/Health-State, Policy-Checks und `failover`.
- `UsageAccounting` mit pro Task/Mission/Agent gebuchtem Provider, Modell, Input-/Output-Token, Kosten-Schätzung, tatsächlichem Preis, Limit und Status.
- `ApprovalAndBudgetPolicy` mit hard caps und separater Freigabe zum Übergang auf kostenpflichtige API-Nutzung.
- `JobCheckpointStore` mit Persistenz, Wiederaufnahme, Idempotency-Keys und Tool-Action-Ledger.
- `RouterEventBus` mit Live-Meldungen an JARVIS Dashboard und ULTRON Mission Control.

Verwende abstrahierte Verträge. Keine versteckten globalen Switches oder hardcodierten Keys.

### 3. Konfiguration und UX

Ergänze in **JARVIS → Settings → AI & Billing** ein professionelles, minimalistisches Panel:

```
AI ROUTING
Strategy: Subscription first
Claude Plan: CONNECTED / NOT SUPPORTED / LIMIT REACHED / UNKNOWN
Claude API: CONNECTED / NO KEY / INSUFFICIENT CREDIT / UNAVAILABLE
Local AI: READY / NOT INSTALLED

Fallback Priority
1. Claude Plan (when permitted)
2. Claude API (authorized budget)
3. Local / Pause

API FALLBACK
[ ] Enable automatic paid API fallback
Monthly budget: [CHF/USD Betrag]
Per-mission cap: [Betrag]
Warn at: 50% / 80% / 100%
Maximum concurrent paid jobs: [n]
[ ] Stop automatically when budget is reached

Current Route: CLAUDE PLAN | CLAUDE API | LOCAL | PAUSED
Reason: ...
```

- Voreinstellung: **kostenpflichtiger API-Fallback AUS**, bis der Nutzer ihn ausdrücklich aktiviert und ein Budget gesetzt hat.
- Wenn einmal explizit freigegeben, kann innerhalb der festgelegten Obergrenzen automatisch umgeschaltet werden – aber keine stillschweigende Erhöhung des Limits oder Auto-Top-up.
- Provider-/Abo-Verfügbarkeit nur anzeigen, wenn technisch geprüft; sonst `Unknown` statt erfundener Kontostände/Resttokens. Nutze nur offiziell verfügbare Status-/Abrechnungsschnittstellen. Bei fehlenden Kontostand-APIs statt exakter Prozentanzeige: `Limit von Anbieter gemeldet` / `Planverbrauch nicht abrufbar`.
- Ständige Statusanzeige, welcher Weg aktiv ist. API-Kosten sichtbar und nachvollziehbar. Warnungen in CHF/USD mit klarer Währung und Wechselkursannahmen, falls umgerechnet.
- Optional mehrere Arbeitsprofile: `Economy` (günstige Modelle), `Balanced`, `Deep Research`. Auswahl darf die Budget- und Policy-Schranken nicht umgehen.

### 4. Agent- und QuantLab-Integration

- Alle ULTRON-Arbeiter (AXIOM, FORGE, CIPHER, ATLAS, PRISM, SENTINEL usw.) nutzen denselben zentralen Router und erhalten separate Agent-/Mission-Budgets sowie priorisierte Work Queues.
- JARVIS bleibt orchestrierender Hauptagent. Teure Modelle für Aufgaben einsetzen, bei denen ihre Fähigkeiten gebraucht werden. Für Deterministisches (Backtest-Rechnung, Datenvalidierung, Plotting, Ledger-Abgleich) **kein LLM aufrufen**.
- QuantLab Strategy-Ingestion (Text/Video/TikTok), Strategy Architect, Review, Research Reports und ULTRON-Analyse verwenden AI-Router für Modell-Anfragen; eigentlicher Backtest ist reproduzierbarer lokaler Code.
- **Databento ist ein unabhängiger kostenpflichtiger Datenanbieter.** Claude-Abo/API-Guthaben bezahlt niemals Databento-Daten. Download weiterhin mit separater Kostenabschätzung und Freigabe; cache-first, kein automatischer Kauf beim Provider-Failover.
- In langen Projekten checkpointen; bei Limitwechsel muss die gesamte Mission inklusive Dateien, Versionen und Experiment-IDs konsistent bleiben.

### 5. Fehlerfälle und Sicherheitsanforderungen

- 401/403 Auth invalid: klare Meldung, keine unendliche Retry-Schleife.
- Abo-Quota erreicht: API nur bei bestehendem ausdrücklichem Opt-in und Budget; sonst Pause oder lokaler kompatibler Fallback.
- API insufficient_credit: lokal/pausieren, nie neue Käufe ohne explizite Vorgaben.
- Netzwerkfehler: begrenzte Retries, kein unkontrolliertes Provider-Flapping.
- 429: sauber zwischen Rate- und Quota-Limit unterscheiden; Retry-After beachten.
- Task capability mismatch: keine stille Degradierung von Vision/Coding-Sicherheitsprüfung auf ein ungeeignetes Modell.
- Berechtigungen für Tool-Aufrufe und externe Aktionen beim Fallback unverändert durchsetzen.
- Secrets niemals im Frontend-State persistent speichern, loggen oder in Prompts einfügen. Keine OAuth-Imitation. Separate Umgebungen, damit `ANTHROPIC_API_KEY` nicht die gewünschte Abo-Autorisierung überschreibt.
- Spooling/Replay so planen, dass keine mehrfachen Datenkäufe, Börsenorders, externen Nachrichten oder destruktiven Operationen ausgeführt werden.

### 6. Definition of Done – automatisierte Tests

Schreibe und führe mindestens Tests für folgende Szenarien aus:

1. Subscription available, permitted → nutze Abo, kein API-Charge.
2. Subscription unavailable/unsupported → API nur bei aktiviertem Kosten-Opt-in.
3. Subscription quota exhausted → Mission-Checkpoint, legitim autorisierter API-Fallback, korrekte Statusanzeige.
4. Kein explizites API-Opt-in → niemals bezahlte API-Ausführung.
5. API-Key als Umgebungsvariable würde Subscription überschreiben → per-process isolation verhindert unbeabsichtigte Kosten.
6. Max/Team API-Promotional-Credits: UI unterscheidet Provider-seitige Gutschriften von Claude-Code-Planlimits, ohne Guthaben zu erfinden.
7. Paid budget exceeded → sofortige Sperre neuer kostenpflichtiger Jobs; laufende vertragliche Kosten ehrlich abrechnen/ausweisen.
8. API credit exhausted → Pause/local, keine Endlosschleife.
9. Network failure → begrenzte Retries; keine Doppelbelastung durch unkontrollierte Replays.
10. Restart während einer Mission → Fortsetzung aus persistiertem Checkpoint, Tool-Idempotenz.
11. Databento Download Approval weiterhin separat und verpflichtend.
12. Alle Agenten verwenden denselben Router; Agent-Budgets und Permission Gates greifen.
13. AI offline → vorhandene historische Daten, QuantLab Backtest und Reports ohne LLM weiterhin ausführbar.
14. UI-Billing-Status entspricht echten Events, kein Mock als reale Verbindung ausgegeben.
15. Kein Schlüssel, Token oder geheimer Header in Log, Trace, Websocket oder Crash Report.

Verwende Offline-Mocks für Auth- und Fehlerfälle, aber bezeichne sie als Tests; teste die wirkliche Abo-Verbindung ausschliesslich über offiziell unterstützte Wege und mit Zustimmung, wenn Kontozugriff nötig wird.

### 7. Dokumentation und Start

Erstelle/aktualisiere:
- `docs/AI_ROUTING_ARCHITECTURE.md`
- `docs/AI_BILLING_POLICY.md`
- `docs/AI_FALLBACK_RUNBOOK.md`
- `docs/AI_ROUTER_TEST_RESULTS.md`

Referenzdokumentation (Stand 10.10.2026):
- https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- https://support.claude.com/en/articles/13189465-log-in-to-your-claude-account
- https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
- https://support.claude.com/de/articles/12429409-nutzungsguthaben-fur-bezahlte-claude-plane-verwalten

**Jetzt handeln:** JARVIS-Repository analysieren, bestehenden Modellzugang prüfen, legale verfügbare Abo-Integration bewerten und implementieren, dann API-Fallback mit sicherer Credential-Verwaltung, Budgets, UI und Wiederaufnahme ausrollen. Abnahmetests ausführen, Fehler korrigieren und tatsächlichen Implementierungsstatus berichten. Keine bloße Architekturplanung, aber auch keine gefälschten Verbindungen. Die Verfügbarkeit muss nicht garantiert werden, wenn der Anbieter/Plan/Netzwerk nicht verfügbar ist.
