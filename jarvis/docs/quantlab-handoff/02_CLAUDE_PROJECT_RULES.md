# JARVIS QuantLab — Projektinstruktionen für Claude

**Hinweis:** Dieses Dokument inhaltlich in das bestehende `CLAUDE.md` aufnehmen oder von dort referenzieren. Nicht ungeprüft die bisherige Datei ersetzen. Kein automatisch angenommener Monorepo-Stack.

## Mission
QuantLab ist die wissenschaftliche Trading-Research-Oberfläche von JARVIS: Idee -> eindeutige StrategySpec -> geeignete Daten -> kausaler Backtest -> unabhängige Validierung -> evidenzbasierte Entscheidung. Ziel ist weniger Forschungsfriktion, nicht sichere Profite.

## Immer
- Repo und bestehende Tests erst inspizieren; kein `git reset --hard`, kein blindes Löschen/Umbauen.
- Small vertical slices; nach jedem Slice typecheck, unit tests und UI smoke test; den Stand in `docs/quantlab/IMPLEMENTATION_STATUS.md` fortschreiben.
- Finanzkennzahlen gegen unabhängige Fixtures prüfen und auf Kosten nachrechnen.
- Alle Strategieinputs, Entscheidungen und Datenstände versionieren und hashen.
- No future leakage: Signal nach Bar-Close; frühester Fill nächste handelbare Bar, es sei denn explizites kausales Tickmodell.
- Marktwerte, Renditen, Signale und Qualitätsbewertungen niemals fälschen. Demo-/Fixture-Daten sichtbar labeln.
- Kein Echtgeldhandel, keine Broker-Orders, kein automatisiertes Kauf-/Verkaufssystem.
- Keine arbiträre Python-Ausführung aus ungeprüften LLM-Ausgaben; Strategie zunächst per wohldefinierter DSL/Templates.
- API-Keys aus Environment/Secret Store; keine Secrets in Logs oder Repo.
- Fehler, Unsicherheit, unklare Daten oder unbekannte Intrabar-Reihenfolge transparent anzeigen.

## Prioritäten
1. Sicherheit und Quant-Korrektheit; 2. Reproduzierbare Tests; 3. Produktfunktion; 4. UI-Qualität; 5. Performanceoptimierung.

## Anwendungsumfang R0/R1
MVP unterstützt CSV/Parquet OHLCV, eine Cash-Equity-Long-Only-MA-Crossover-Strategie und nächstes-Bar-Open-Fills. Futures, Optionen, Tick/Quote-Fills, Shorts, Portfolio-Sizing, PBO-Metriken und live Broker bleiben Follow-ups, nicht halbimplementierte Optionen.

## Referenz
Lies `00_START_HERE_DE.md` und die Dateien in `spec/` (relativ zum Handoff-Ordner), bevor du wesentliche Quant-Lösungen wählst. Designquelle: `references/JARVIS_QuantLab_Masterkonzept.pptx`.
