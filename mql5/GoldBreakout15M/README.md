# Gold Breakout 15M – MT5 Expert Advisor

MQL5-Port der Pine-Strategie „Gold Breakout 15M [WillyAlgoTrader]“.

## Installation
1. `GoldBreakout15M.mq5` nach `MQL5/Experts/` kopieren (MT5: Datei → Datenordner öffnen).
2. In MetaEditor öffnen → F7 (Kompilieren).
3. Auf XAUUSD-Chart ziehen (Timeframe egal, die Range kommt aus `InpRangeTF`, Default M15). Algo-Trading aktivieren.

## Logik (1:1 zum Pine-Script)
| Pine | EA |
|---|---|
| Range 00:00–07:00 Brokerzeit | Gleich, direkt auf **MT5-Serverzeit** (kein Timezone-/NY+7-Setting nötig, DST macht der Broker) |
| Stop-Orders an beiden Grenzen, OCA | BuyStop @ Range-High, SellStop @ Range-Low; erster Fill löscht die Gegenorder |
| 1 Trade pro Tag | Über Deal-History geprüft → überlebt EA-/Terminal-Neustart |
| SL % vom Entry, nach Fill neu gesetzt | Gleich (SL hängt an der Pending, wird nach Fill auf Fillpreis ± % gesetzt) |
| SL Range-Grenze | Gleich |
| Kein TP, Close um 20:00 | Gleich |
| Size = Equity × Risk% / SL-Verlust, Cap Max Leverage, abrunden | Gleich, über Tick-Value (Kontowährung) + zusätzlicher Free-Margin-Check |
| Prop Guard (Daily DD vom Tages-Peak) | Gleich, aber **pro Tick** statt pro Kerzenschluss; Zustand persistiert in GlobalVariables |

## Bewusste Abweichungen
- **Ausführung tick-genau** statt „Bar-Close → Fill nächste Kerze“. Live realistischer, Backtest-Ergebnisse weichen leicht von TradingView ab.
- **Spread ist drin**: BuyStop triggert auf Ask (MT5-Standard). TradingView modelliert keinen Spread.
- **Market-Fallback** (`InpMarketFallback`): Wenn die Pending wegen Stops-Level/Gap nicht platziert werden konnte und der Preis das Level schon durchbrochen hat, wird zu Markt eingestiegen – entspricht dem Pine-Verhalten (Stop-Fill bei Gap). Nur wenn der EA den Preis heute schon innerhalb der Range gesehen hat → kein blinder Einstieg, wenn du den EA spät startest, nachdem der Ausbruch längst passiert ist.
- Erster Tag der History wird nicht übersprungen (Range wird sauber aus der History gebaut).

## Vor dem Live-Gang
- Strategy Tester: „Every tick based on real ticks“, XAUUSD, mehrere Jahre. Ergebnisse mit TradingView vergleichen.
- Prüfen, ob deine Broker-Serverzeit zum Setup passt (die meisten: GMT+2/+3 → Range 00–07 = wie im Original).
- Fixe Lots / Risiko erst auf Demo testen. Kommission, Spread, Slippage kommen oben auf das Risiko drauf.
- Nicht kompiliert in dieser Umgebung (kein MetaEditor) – Compiler-Meldungen bitte zurückmelden.
