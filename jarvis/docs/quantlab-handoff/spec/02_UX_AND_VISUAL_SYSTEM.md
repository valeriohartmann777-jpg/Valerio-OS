# 02 — UX und visuelles System

## Ästhetik
JARVIS × Linear × Trading Terminal, aber reduziert. Graphit/Schwarz, keine 3D-Show, 1 dezentes Türkis/Cyan als Markenakzent, sachliche Typografie, hohe Lesbarkeit, präzise Kanten und Linien. Ruhiger Screen im Idle. **Präsentation ist eine visuelle Referenz, keine echte Performance.**

## Tokens (Vorschlag, an bestehendes Designsystem anpassen)
```
--bg: #090D11
--surface: #111820
--surface-raised: #17212A
--border: #27323A
--text: #EDF5F7
--muted: #91A3AE
--accent: #4EC7BB
--warning: #D8AA60
--danger: #E37D84
--positive: #76BFA9
radius: 12–16px cards / 8px controls
font: Inter / vorhandene Sans; mono nur für IDs, Preise, Logs
```
Kontrast für Text prüfen. Farben nie als einziges Statussignal benutzen. Trend/Profit rot-grün nicht als alleinigen Informationsträger nutzen.

## Navigationshierarchie
JARVIS / QuantLab / [Overview | Strategies | Experiments | Datasets | Reports] 

### Overview
- oben: QuantLab + Status/aktive Aufgabe + `New Strategy`
- Reihe 1: aktuelle Hypothese/letzte Experimente und *Evidence verdict* mit erklärter Bedeutung.
- Reihe 2: Equity net of costs, Train/OOS segmentiert, Render nur nach echtem Run.
- Reihe 3: „Validation Matrix“, wichtigste 3 Warnungen, Explorer-Zugang.
- JARVIS Text/Eingabe als persistenter kontextbezogener Co-Pilot.

### Strategy Architect
- links: Strategie als einfache Karte, Inputs als editierbare Controls.
- rechts: JARVIS Fragen/Warnungen; *Unknown / Assumed / Confirmed* sichtbar.
- vor Run: explizite Zusammenfassung „Entry ab nächstem Bar-Open; Fees X, OOS Y“.
- V1: Standardformular + MA-Crossover preset; NL -> Spec erst bei echtem LLM mit validem JSON-Contract.

### Data Lab
- Import CSV/Parquet oder gespeicherten Dataset-Snapshot auswählen.
- Preview: Schema-Mapping, Date/Time-Zone, Instrument, Currency, Coverage, Gaps, Duplikate, monotone Zeit, OHLC-Konsistenz.
- Status: ACCEPTED / WARNING / REJECTED, mit Schweregrad und Handlungsoption.
- Synthetic fixture muss permanent als `SYNTHETIC / TEST ONLY` gekennzeichnet sein.

### Experiment Detail
- Titel, StrategySpec version/hash, Data Passport, Reproduzierbarkeit.
- Kennzahlen: net P&L, Prozentbasis + Nenner, Drawdown, Trades, Fees, OOS getrennt, Benchmark wenn vorhanden.
- Charts: Equity+DD; nur echte berechnete Kurven. Keine Fake-Linie im leeren Zustand.
- Tabs: Overview | Trades | Assumptions | Validation | Audit.
- Trade-Klick -> Zeit, Signalbar, Fillbar, Preise, Fees, PnL, Datenzeilen, offene Ambiguitäten.

### Status und Fehler
- `not_configured`: klares Formular statt 0-Werte.
- `data_invalid`: erklärter Grund und Datensatz ohne bewerteten Run.
- `running`: Fortschrittsereignisse realer Schritte, keine erfundenen Prozentwerte.
- `complete`: Ergebnis, Unsicherheit, Quelle.
- `failed`: verständliche Fehlerursache, Retry ohne Duplikat/versehentlichen Folgeeffekt.
- `empty`: ruhige CTA, keine fiktive positive Equity.
- `insufficient_data`: „OOS nicht aussagekräftig“ statt beliebig gut/schlecht.

## Interaktionsdetails
- Desktop-first, min 1280px optimiert, sinnvoll responsiv.
- Keyboard: Enter sendet nur bewusst; Fokus-Indikator, Browserstandard-Shortcuts, ARIA labels.
- Kontextbezogene „Why this matters“-Info bei Sharpe, DD, OOS und Data Passport.
- Vergleiche: stets gleiche Daten-/Kostenbasis oder große Warnung „nicht vergleichbar“.
- Finanzergebnisse mit eindeutigen Einheiten und Rundung (P&L USD vs %, Drawdown % vs Geld).

## Referenzfolien
05 Cockpit, 06 Strategy Architect, 07 StrategySpec, 09 Data Passport, 17 Result Dashboard, 18 Trade Explorer, 20 Evidence Gates, 21 Research Modes.
