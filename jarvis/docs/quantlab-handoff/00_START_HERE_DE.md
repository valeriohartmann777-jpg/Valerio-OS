# JARVIS QuantLab — Übergabepaket für Claude Code

**Status:** Fertige Produkt- und Implementierungsspezifikation. **Keine lauffähige QuantLab-App.** Claude Code übernimmt die Implementierung in deinem Windows-Repository.

## In drei Schritten starten

1. Entpacke **den gesamten Inhalt dieses Pakets** in das existierende JARVIS-Repository, z. B. unter `C:\dev\jarvis\docs\quantlab-handoff\`. Vorhandene Dateien im Repo **nicht** ersetzen.
2. Öffne Claude Code **im Root des tatsächlichen JARVIS-Repositories**. Falls `C:\dev\jarvis` nicht dein Repo ist, benutze dessen realen Pfad.
3. Kopiere den vollständigen Inhalt aus `01_PROMPT_TO_PASTE_IN_CLAUDE.md` in Claude Code. Das Paket bleibt im Repo, damit Claude alle Referenzen lesen kann.

**Wichtig:** Wir haben den echten aktuellen JARVIS-Code nicht inspiziert. Der Repo-Pfad und der frühere Stack sind Produktannahmen. Claude muss den echten Stand feststellen und darf keinen bestehenden funktionierenden Teil überschreiben. Das Paket enthält absichtlich KEINEN automatischen Installer und keinen Befehl, der dein Repository verändert.

## Dateiübersicht

| Ressource | Zweck |
|---|---|
| `01_PROMPT_TO_PASTE_IN_CLAUDE.md` | Einziger Startprompt: konkret, ausführbar und nicht nur Planung |
| `02_CLAUDE_PROJECT_RULES.md` | Permanente Regeln; Claude darf sie nach Prüfung vorsichtig in bestehendes `CLAUDE.md` integrieren |
| `spec/01_PRODUCT_REQUIREMENTS.md` | Vision, Personas, Workflows, Umfang, Nicht-Ziele |
| `spec/02_UX_AND_VISUAL_SYSTEM.md` | Screens, Interaktionen, Design-Tokens, Fehlerzustände |
| `spec/03_ARCHITECTURE_AND_API.md` | Module, Abläufe, APIs, Events, Persistenz |
| `spec/04_STRATEGY_AND_EXECUTION.md` | Exakte Trading-Semantik und Ausführungsregeln |
| `spec/05_DATA_POLICY.md` | Herkunft, Zeitzonen, Datenqualität, Datenrechte |
| `spec/06_VALIDATION_PROTOCOL.md` | OOS, Holdout, Walk-forward, Statistik, Evidenz-Gates |
| `spec/07_AI_ASSISTANT_AND_SECURITY.md` | LLM-Grenzen, Permissions, Security, Safety |
| `spec/08_RELEASE_PLAN_AND_TASKS.md` | Lieferreihenfolge R0–R4 samt Akzeptanzkriterien |
| `spec/09_TEST_MATRIX.md` | Goldene Tests und rote Linien |
| `spec/10_DECISIONS_AND_OPEN_ITEMS.md` | Beschlossene Entscheidungen, noch offene reale Inputs |
| `contracts/` | Validierbare JSON-Schema-Beispiele und API-Eventbeispiele |
| `fixtures/` | Nur synthetische Unit-Test-Fixtures; **keine realen Handelsdaten** |
| `references/` | Original-Präsentation (35 Folien) als Design- und Produktreferenz |

## Prioritäten bei Widersprüchen

1. Sicherheit, korrekte Datenherkunft und kausale Zeitsemantik (Specs 04–07).
2. Verbindliche Tests und Akzeptanzkriterien (Specs 08–09).
3. Vertragsschemas und Architektur (Contracts und Spec 03).
4. Produktbedienung, visuelle Sprache (Specs 01–02).
5. Präsentation: inspirierende Visualisierungen, fiktive Beispielmetriken, noch **keine** realen Testresultate.

## Definition: MVP fertig

QuantLab ist im bestehenden JARVIS sichtbar; importiert eine echte vom Nutzer bereitgestellte CSV/Parquet-Datei mit Data Passport; erstellt/bearbeitet eine deterministische StrategySpec; simuliert MA-Crossover long-only kausal mit Fill am nächsten Bar-Open, Handelskosten und Trades; persistiert Version, Snapshot, Ledger, Kennzahlen und Run-Hash; führt eine saubere zeitliche OOS-Trennung aus; visualisiert echte Daten und markiert Warnungen; besteht alle Tests. **Keine Broker-Verbindung, keine erfundenen Daten, keine Versprechen eines Trading-Edges.**
