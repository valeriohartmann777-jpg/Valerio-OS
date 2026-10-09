# CLAUDE CODE — BUILD JARVIS QUANTLAB (START NOW)

Du bist Lead Quant Engineer, Full-Stack Engineer und Product Designer für mein bestehendes JARVIS-Projekt. Baue **QuantLab** als integriertes Research-Modul. Die fertigen Spezifikationen, Verträge, Test-Fixtures und das 35-Folien-Design befinden sich im Ordner `docs/quantlab-handoff/` relativ zum JARVIS-Repo. Sollte ich den Ordner anders abgelegt haben, finde `00_START_HERE_DE.md` im Workspace und nutze dessen tatsächlichen Pfad. **Lies zunächst die Dateien dieses Pakets und die vorhandenen Repo-Instruktionen (besonders CLAUDE.md).**

AUFTRAG: Du sollst SOFTWARE BAUEN, nicht nur einen Plan schreiben. Arbeite schrittweise und hinterlasse nach jedem Schritt einen funktionierenden Zustand.

## Phase 0 — Zustand aufnehmen (kurz, dann handeln)
1. Prüfe die reale Repo-Struktur, den git-Status, Frameworks, Tests und existierende JARVIS-Navigation, Backend-Services und UI-Komponenten.
2. Verändere keine fremden Änderungen, lösche nichts und überschreibe keine bestehende CLAUDE.md ungeprüft.
3. Vergleiche IST und SOLL. Erstelle einen maximal einseitigen Integrationsplan und lege eine Umsetzungsliste im Repo an. Passe die Paketpfade an den echten Stack an, ohne die Domänenverträge zu verwässern.
4. Wenn es kein bestehendes JARVIS-Repo gibt, lege ein klar gekennzeichnetes eigenständiges QuantLab-Scaffold an. Behaupte nicht, es sei integriert.

## Phase 1 — IMPLEMENTIEREN (nicht bei Planung stoppen)
Baue einen **vertikalen R0+R1-Slice**:
- QuantLab-Route/Navigation im bestehenden JARVIS + Premium-Dark-Shell aus dem Design.
- Strategy Workspace: Strategie erstellen, Regeln sehen/editieren und versionieren; AI-Interpretation kann zunächst ein klar gekennzeichneter optionaler Adapter sein, aber kein Fake-Ergebnis.
- Echte CSV/Parquet-Daten lokal importieren, Preflight-Validierung und Data Passport mit Checksums und Warnungen; niemals synthetische Daten als echte Marktdaten anzeigen.
- Deterministischer, nachvollziehbarer long-only Cash-Equity-Referenzsimulator für MA-Crossover: Signal erst am Bar-Close verfügbar, Fill zum nächsten zulässigen Open; Gebühren, Cash und Trades korrekt buchen; keine Shorts/Leverage in R1.
- Deterministische experiment_id/run manifest inkl. StrategyVersion, DataSnapshot, Engine-Version, Kostenannahmen, Seed/Code-Version; Ergebnisse rekonstruierbar machen.
- Chronologischer Train/OOS-Split mit unberührtem Holdout; keine Parameteroptimierung auf OOS.
- Ergebnisscreen mit Equity, Trades, P&L, Drawdown, Split, Datenqualität, Warnungen und **sichtbar nicht validiertem** Research-Status.
- Status-/Fehler-/Loading-UI; vorhandene JARVIS-Eventbus/API-Muster nutzen.

## Phase 2 — VERIFIZIEREN
- Implementiere die Tests in `spec/09_TEST_MATRIX.md` und nutze die Fixtures aus `fixtures/`.
- Insbesondere: Signal t -> Fill nicht vor t+1; Gebühren; Cash-Bilanz; keine Future-Leaks; gleiches Run-Manifest -> gleiche Ergebnisse; data QA blockiert kaputte Daten; Train/OOS getrennt; keine Behauptung „validiert“ durch nur positive Equity.
- Frontend typecheck/build und Backend tests ausführen. Fehler beheben. Nutzerfluss lokal prüfen (Playwright/Screenshot sofern verfügbar, sonst begründete Einschränkung festhalten).
- Ausgaben nur als „real backtest“ kennzeichnen, wenn reale lizenzierte Daten samt Provenienz vorliegen; sonst „synthetischer Testfixture“.

## Phase 3 — ABSCHLIESSEN
Erstelle/aktualisiere `docs/quantlab/IMPLEMENTATION_STATUS.md` mit: implementiert, getestet, ausgelassen, noch offen, exakten Run-Befehlen, Testresultaten, Screenshots soweit möglich. Teile die nächsten 3 konkreten Schritte mit. Lege keine API-Keys ab und führe keine echten Orders aus.

## Produkt- und Engineeringregeln
- **Wissenschaftliche Korrektheit vor Funktionsmenge.** Keine Look-ahead-Effekte, konservative OHLC-Ambiguität, Kosten, unveränderliche Daten/Specs, Reproduzierbarkeit.
- **Schlanke UX vor Quant-Fachjargon.** Nutzer gibt Idee ein; System klärt Regeln, zeigt Belege und Schwächen.
- **JARVIS-Design vor Standard-Dashboard.** Schwarz/Graphit, dezentes Petrol/Türkis, feine Typografie, wenig Animation, klare Informationshierarchie. Verwende die Referenzfolien 05, 06, 17, 18 und 20 besonders.
- **Keine fertige Story vortäuschen.** Keine Fake-Werte auf Produktionsscreens; keine Versprechen erfolgreicher Handelsstrategien.
- **Kein Auto-Trading.** Keine Brokerzugriffe, Echtgeldaktionen oder unsandboxed KI-Codeausführung.
- **Kein Overengineering.** Noch keine 8 echten Agenten, keine 5 Datenprovider und kein Mass-Optimization-Cluster. Zuerst eine solide End-to-End-Implementierung.
- Hole Informationen nur dann vom Nutzer ein, wenn sie wirklich blockieren. Ein realer Intraday-Futures-Researchmodus bleibt bis passenden Daten und Futures-Accounting explizit „noch nicht unterstützt“.

Starte mit Repo-Inspektion und implementiere anschliessend unmittelbar den ersten MVP. Arbeite nicht nur theoretisch. Bestehende JARVIS-Funktionalität darf nicht kaputtgehen.
