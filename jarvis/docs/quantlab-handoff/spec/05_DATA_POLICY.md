# 05 — Market Data Policy / Data Passport

## Unverhandelbar
Keine „richtigen Daten“ behaupten, wenn Datenquelle, Granularität, Lizenz, Instrumentenidentität oder Qualitätsniveau ungeklärt sind. JARVIS soll **Daten-Eignung prüfen, bevor** ein Ergebnis zum Einsatz in der Entscheidungsfindung angeboten wird.

## MVP Input
- CSV/Parquet vom Nutzer über ausgewählte Datei oder dokumentierten lokalen Pfad (mit path jail); kein autonomer Internet-Download.
- Schema mapping für Zeit und `open, high, low, close, volume`. Timestamps eindeutig, streng steigend, interpretierbare Zeitzone und Instrument-Symbol/Austauschwährung vorhanden.
- Vorschau von Zeilen, dokumentierte Normalisierung, Data Passport, immutable normalized Parquet snapshot, SHA-256 der Originaldatei und der normalisierten Ausgabe.
- Daten niemals heimlich ersetzen, glätten, synthetisch auffüllen oder unsichtbar forward-fillen.

## Data Passport Feldliste
- `provider`, `license`, `original_filename`, `original_sha256`, `normalized_sha256`, `instrument`, `venue`, `currency`, `asset_class`, `timezone_original`, `normalized_timezone=UTC`, `frequency`, `coverage_start_utc`, `coverage_end_utc`, `row_count`, `missing_periods`, `duplicates`, `sort_fixes`, `invalid_ohlc`, `outliers`, `corporate_actions_or_roll_policy`, `quality_status`, `limitations`.
- Provider kann `user_supplied` sein, **nicht** „Databento“ ohne echten Databento-Exportbeleg.
- Rechte/Lizenz: Nutzerdatensatz muss entsprechend zugelassen sein; QuantLab gibt keine Lizenzrechte selbst aus.

## QA Fehlerklassen
| Check | MVP-Reaktion |
|---|---|
| Datum nicht parsebar / TZ unbekannt | BLOCK und mapping erfragen |
| Doppelte Zeitstempel | BLOCK oder expliziter lösungsbedürftiger Importpreview; niemals implizites Aggregieren |
| Zeit unsortiert | Flag + explizite Normalisierung nur mit Nutzersichtbarkeit |
| Nonpositive, NaN/Inf Preise | BLOCK |
| OHLC-Logik verletzt | BLOCK |
| Time gaps | Warnung/Block je nach Session/Kalender und Signal-Anforderung |
| Illiquide Null-Volume | Warnung; marktbezogene Prüfung |
| Futures roll ambiguities | Für Futures R1 BLOCK; später ausgewiesene Roll-Policy |
| Corporate Actions unklar | Warnung/Block je nach Aktieninstrument und Zeitraum |
| Datensatz zu kurz für MA/split | BLOCK mit Mindestdatenbedarf |
| Tick/Quote für enge Stops benötigt | `INSUFFICIENT_EXECUTION_GRANULARITY` statt präziser SL/TP-Fills |

## Intraday-Perspektive später
- Market session calendar is exchange-specific, not fixed UTC offsets; handle daylight-saving shifts, half-days and holidays.
- Distinguish event timestamp from **when data becomes known** (bar close, delayed publication, revised series).
- Bid/ask vs trade OHLCV matters for execution; roll method matters for futures.
- Preserve raw snapshots and data-change manifests.
- Market data costs may be high; fetch only market-specific necessary granularity with explicit budget later.

## Daten-Eignungsstatus
`ACCEPTED`: use allowed for supported research scope; `WARNING`: run may occur, limitations prominent; `REJECTED`: run cannot happen; `UNSUPPORTED`: e.g. futures in equity R1.

## Demo fixtures
All package fixture files are **synthetic** and strictly for engineering tests, not economic inference. Code and UX must identify them accordingly. Real historical CSV/Parquet is required for real experimental evidence. No stock-market dataset bundled with this handoff.
