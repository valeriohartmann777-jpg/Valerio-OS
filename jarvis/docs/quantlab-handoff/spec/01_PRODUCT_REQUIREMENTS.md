# 01 — Product Requirements (PRD)

## North Star
**Eine Idee rein. Prüffähige Evidenz raus.** Ein Solo-Trader ohne Programmierkenntnisse soll Hypothesen in Alltagssprache entwerfen, formal prüfen und kritisch vergleichen können. Er muss nicht manuell Python, Backtest-Libraries, Datenparsing, Equity-Kurven und Statistiktools verkabeln.

## Outcome und Abgrenzung
- Mission: Research und Entscheidungen über *weitere Tests* verbessern; keinen positiven Trading-Erfolg garantieren.
- Primärer Job: Idee formalisieren -> fehlende Regeln klären -> passende Daten laden -> Methodik testen -> Fehler diagnostizieren -> nachvollziehbaren Ergebnisbericht erzeugen.
- Kein „Backtest mit +20% = Edge“. Aussage „promising“ nur nach daten- und methodengerechten Evidenz-Gates; MVP grundsätzlich vorläufig.
- Hauptproblem: komplexe vorhandene Tools und vorschnelle Schlussfolgerungen aus einem Backtest.

## Nutzerführung
1. **Workspace**: alle Strategien, Ideen, letzte Experimente und Datenstatus.
2. **Neue Idee**: in Deutsch/Englisch Freitext oder Guided Form, z. B. „Was passiert, wenn ...?“
3. **Strategy Architect**: zeigt klar die Hypothese, Handelsinstrument, Session, Zeiteinheit, Trigger, Order, Exit, Stop, Sizing. Blockierende Unklarheiten sind visuell markiert. Nichts erfinden.
4. **Data Passport**: Quelle, Coverage, Granularität, Qualität, Rechte, Kosten-/Fill-Eignung. Keine validen Daten => Test blockiert.
5. **Run Experiment**: Methodik vorschlagen; Fortschritt live anzeigen; Ergebnisse speichern.
6. **Evidence Dashboard**: Netto-Performance, Trade-Liste, Verluste, OOS, Stress, Fallstricke; Erklärung: Was ist belegt, was fehlt?
7. **Nächster Test**: Empfehlung zur Falsifikation; Variante erstellen; alte Version nicht überschreiben.

## Interaktionsprinzipien
- Zwei Ebenen: Default (Hypothese, Urteil, Hauptrisiken), Details (Trade Ledger, Run-Konfiguration, Assumptions, Statistiken).
- JARVIS darf nie ohne deutliche Sichtbarkeit wichtige Annahmen einführen (z. B. Stop, Slippage, Handelszeit, Kontraktrolle).
- Ein Test ist immer verbunden mit Strategieversion + Datensnapshot + Modellversion + Ausführungsannahmen + Codeversion.
- Versionsvergleich muss auch gescheiterte Versuche zeigen; nie nur Gewinner cherry-picken.
- „In Bearbeitung / Fehler / Eingaben fehlen / Datenqualität ungenügend“ sind volle UX-Zustände.

## Scope nach Release
| Release | Liefern | Explizit nicht liefern |
|---|---|---|
| R0 Foundation | Navigation, Spec-Editor, Importer, Data Passport, System-Events | reale Research-Bewertungen |
| R1 Trusted Backtest | kausaler long-only MA-Test, Fees, Ledger, Reproduktion, OOS-Split, Basisdashboard | Echtzeithandel, Forex/Futures-Fillmodell |
| R2 Evidence Lab | Walk-Forward, Kostenstress, Parameterkarte, Bootstrap (geeignet), Regimes | automatische Edge-Garantie |
| R3 AI Quant Researcher | Prompt->editierbare Spec, kritische Report-Synthese, Experimentvergleich | autonomes Trading |
| R4 Forward Research | Paper Trade, Drift, unabhängige Live-Validierung | unbeaufsichtigte Echtgeldorders |

## MVP Job Stories
- Als Nutzer will ich CSV/Parquet importieren und sofort erfahren, ob die Historie überhaupt brauchbar ist.
- Als Nutzer will ich eine einfache Strategie ohne Code konfigurieren und vor dem Start die exakten Handelsregeln sehen.
- Als Nutzer will ich die Equity-Kurve und jeden Trade gegen die Daten zurückverfolgen.
- Als Nutzer will ich OOS nicht mit Trainingsdaten verwechseln können.
- Als Nutzer will ich einen Test identisch wiederholen und Ergebnisse versionieren.
- Als Nutzer will ich hören „nicht genug Evidenz“, wenn das die richtige Antwort ist.

## Produktmetrik nach Launch
- Zeit von vollständigen, unterstützten Inputs zum ersten reproduzierbaren Test (getrennt von Download-/Computezeit).
- Prozentsatz der Runs mit vollem Provenienz-Manifest und verifizierten Invarianten.
- Wie viele userseitige Ambiguitäten vor Start erkannt werden.
- Anteil der gescheiterten Tests, die verständliche Fehlerursachen zeigen.
- UI-Friktion (Anzahl obligatorischer Nutzeraktionen), Test-Reproduktionsquote.
