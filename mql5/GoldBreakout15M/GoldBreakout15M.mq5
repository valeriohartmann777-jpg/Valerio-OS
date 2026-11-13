//+------------------------------------------------------------------+
//|                                              GoldBreakout15M.mq5 |
//|  MT5-Port der Pine-Strategie "Gold Breakout 15M" (WillyAlgoTrader)|
//|                                                                  |
//|  Logik:                                                          |
//|   - Range: High/Low zwischen RangeStart und RangeEnd (Serverzeit)|
//|   - Entry: BuyStop am Range-High + SellStop am Range-Low (OCA)   |
//|            erster Fill loescht die Gegenorder, 1 Trade pro Tag    |
//|   - SL:    % vom Entry (nach Fill vom echten Fillpreis neu gesetzt)|
//|            oder gegenueberliegende Range-Grenze. Kein TP.         |
//|   - Exit:  SL oder Zwangs-Close zur CloseHour                    |
//|   - Size:  % Equity Risiko, gedeckelt durch Max Leverage,        |
//|            abgerundet auf Volume-Step                            |
//|   - Optional: Prop-Firm Guard (Daily DD vom Tages-Equity-Peak)   |
//+------------------------------------------------------------------+
#property copyright "Valerio OS"
#property version   "1.00"
#property description "Gold Overnight-Range Breakout (Port von Gold Breakout 15M)"

#include <Trade\Trade.mqh>

enum ENUM_GBO_SL_MODE
  {
   GBO_SL_PERCENT = 0, // % vom Entry-Preis
   GBO_SL_RANGE   = 1  // Gegenueberliegende Range-Grenze
  };

enum ENUM_GBO_SIZE_MODE
  {
   GBO_SIZE_RISK  = 0, // % Equity Risiko
   GBO_SIZE_FIXED = 1  // Fixe Lots
  };

//--- Session (alle Stunden in BROKER-SERVERZEIT, DST macht MT5 selbst)
input group "Session (Broker-Serverzeit)"
input ENUM_TIMEFRAMES InpRangeTF        = PERIOD_M15; // Timeframe fuer Range-Berechnung
input int             InpRangeStartHour = 0;          // Range Start Stunde
input int             InpRangeEndHour   = 7;          // Range Ende / Trading Start Stunde
input int             InpCloseHour      = 20;         // Alle Positionen schliessen um

//--- Risiko
input group "Risk Management"
input ENUM_GBO_SL_MODE   InpSLMode       = GBO_SL_PERCENT; // SL Modus
input double             InpSLPercent    = 1.0;            // SL in % vom Entry
input ENUM_GBO_SIZE_MODE InpSizeMode     = GBO_SIZE_RISK;  // Positionsgroessen-Modus
input double             InpRiskPercent  = 1.0;            // Risiko pro Trade in % Equity
input double             InpFixedLots    = 0.10;           // Fixe Lots (bei Fixe Lots)
input double             InpMaxLeverage  = 10.0;           // Max Hebel (Notional / Equity), 0 = aus
input bool               InpGuardOn      = false;          // Prop Guard: Close bei Daily Drawdown
input double             InpGuardDD      = 4.0;            // Max DD vom Tages-Equity-Peak in %

//--- Ausfuehrung
input group "Execution"
input ulong  InpMagic          = 2611130;   // Magic Number
input int    InpDeviationPts   = 30;        // Max Slippage (Points) fuer Market-Orders
input bool   InpMarketFallback = true;      // Market-Entry wenn Level ueberschritten und Pending nicht moeglich war
input string InpComment        = "GBO15M";  // Order-Kommentar

//--- Anzeige
input group "Visual"
input bool  InpShowPanel  = true;           // Status-Panel (Comment) anzeigen
input bool  InpDrawRange  = true;           // Range auf Chart zeichnen
input color InpRangeColor = clrDodgerBlue;  // Range-Farbe

//--- Globals
CTrade   trade;
string   g_prefix;          // Objekt-/GlobalVariable-Prefix
int      g_dayKey      = -1;
datetime g_dayStart    = 0;
bool     g_rangeReady  = false;
bool     g_noRangeToday= false;
double   g_rangeHigh   = 0.0;
double   g_rangeLow    = 0.0;
datetime g_lastRangeTry= 0;
bool     g_seenInside  = false;  // Preis wurde heute innerhalb der Range beobachtet
bool     g_halted      = false;  // Guard ausgeloest -> bis morgen gesperrt
double   g_dayPeak     = 0.0;
bool     g_histDirty   = true;
bool     g_tradedToday = false;
ulong    g_slFixedTicket = 0;
datetime g_lastTry[2];           // Throttle fuer Order-Versuche [0]=long [1]=short
bool     g_sideSkipped[2];       // Seite heute uebersprungen (Size < Min-Lot etc.)
string   g_status      = "";
double   g_planLots[2];
double   g_planSL[2];

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
int DayKeyOf(datetime t)
  {
   MqlDateTime d;
   TimeToStruct(t, d);
   return d.year * 10000 + d.mon * 100 + d.day;
  }

datetime DayStartOf(datetime t)
  {
   MqlDateTime d;
   TimeToStruct(t, d);
   d.hour = 0; d.min = 0; d.sec = 0;
   return StructToTime(d);
  }

int HourOf(datetime t)
  {
   MqlDateTime d;
   TimeToStruct(t, d);
   return d.hour;
  }

double NormPrice(double p)
  {
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0.0) tick = _Point;
   return NormalizeDouble(MathRound(p / tick) * tick, _Digits);
  }

double StopsDistance()
  {
   long lvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   return (double)lvl * _Point;
  }

//--- eigene Position finden
bool FindPosition(ulong &ticket, bool &isLong, double &openPrice, double &sl, double &tp)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      ticket    = t;
      isLong    = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      sl        = PositionGetDouble(POSITION_SL);
      tp        = PositionGetDouble(POSITION_TP);
      return true;
     }
   return false;
  }

ulong FindPending(ENUM_ORDER_TYPE type)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) == type) return t;
     }
   return 0;
  }

void DeletePendings()
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      if(!trade.OrderDelete(t))
         PrintFormat("GBO: OrderDelete %I64u fehlgeschlagen: %d %s", t, trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
  }

void CloseAllPositions(string reason)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(trade.PositionClose(t, (ulong)InpDeviationPts))
         PrintFormat("GBO: Position %I64u geschlossen (%s)", t, reason);
      else
         PrintFormat("GBO: PositionClose %I64u fehlgeschlagen (%s): %d %s", t, reason, trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
  }

//--- wurde heute schon ein Trade eroeffnet? (ueberlebt EA-Neustarts)
bool TradedToday()
  {
   if(!g_histDirty) return g_tradedToday;
   g_tradedToday = false;
   if(HistorySelect(g_dayStart, TimeTradeServer() + 86400))
     {
      int n = HistoryDealsTotal();
      for(int i = 0; i < n; i++)
        {
         ulong d = HistoryDealGetTicket(i);
         if(d == 0) continue;
         if(HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
         if((ulong)HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic) continue;
         if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
         if((datetime)HistoryDealGetInteger(d, DEAL_TIME) < g_dayStart) continue;
         g_tradedToday = true;
         break;
        }
      g_histDirty = false;
     }
   return g_tradedToday;
  }

//--- High/Low der Bars mit Open-Zeit in [from, to]
bool ComputeRange(datetime from, datetime to, double &hi, double &lo)
  {
   MqlRates r[];
   int n = CopyRates(_Symbol, InpRangeTF, from, to, r);
   if(n <= 0) return false;
   hi = r[0].high;
   lo = r[0].low;
   for(int i = 1; i < n; i++)
     {
      if(r[i].high > hi) hi = r[i].high;
      if(r[i].low  < lo) lo = r[i].low;
     }
   return true;
  }

//--- Lots: Risiko-basiert (oder fix), gedeckelt durch Max Leverage + freie Margin, abgerundet
double CalcLots(bool isLong, double entry, double sl)
  {
   double dist = MathAbs(entry - sl);
   if(dist <= 0.0 || entry <= 0.0) return 0.0;

   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickLoss = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickLoss <= 0.0) tickLoss = tickVal;
   if(tickSize <= 0.0 || tickVal <= 0.0) return 0.0;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double lots;
   if(InpSizeMode == GBO_SIZE_FIXED)
      lots = InpFixedLots;
   else
     {
      double lossPerLot = dist / tickSize * tickLoss;   // Kontowaehrung
      lots = equity * InpRiskPercent / 100.0 / lossPerLot;
     }

   // Max Leverage Cap: Notional pro Lot in Kontowaehrung
   double notionalPerLot = entry / tickSize * tickVal;
   if(InpMaxLeverage > 0.0 && notionalPerLot > 0.0)
      lots = MathMin(lots, equity * InpMaxLeverage / notionalPerLot);

   // Margin-Check
   double marginPerLot = 0.0;
   if(OrderCalcMargin(isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, 1.0, entry, marginPerLot) && marginPerLot > 0.0)
      lots = MathMin(lots, AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.95 / marginPerLot);

   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0) step = vmin;
   lots = MathFloor(lots / step + 1e-9) * step;
   if(lots < vmin) return 0.0;
   if(lots > vmax) lots = MathFloor(vmax / step + 1e-9) * step;
   int volDigits = (int)MathMax(0.0, MathCeil(-MathLog10(step)));
   return NormalizeDouble(lots, volDigits);
  }

double PlannedSL(bool isLong, double entry)
  {
   if(InpSLMode == GBO_SL_RANGE)
      return isLong ? g_rangeLow : g_rangeHigh;
   return NormPrice(isLong ? entry * (1.0 - InpSLPercent / 100.0)
                           : entry * (1.0 + InpSLPercent / 100.0));
  }

//--- eine Seite scharf machen: Pending, oder Market falls Level schon durchbrochen
void ArmSide(bool isLong)
  {
   int idx = isLong ? 0 : 1;
   if(g_sideSkipped[idx]) return;
   if(FindPending(isLong ? ORDER_TYPE_BUY_STOP : ORDER_TYPE_SELL_STOP) != 0) return;

   datetime now = TimeTradeServer();
   if(now - g_lastTry[idx] < 5) return;   // Throttle

   double level  = isLong ? g_rangeHigh : g_rangeLow;
   double sl     = PlannedSL(isLong, level);
   double lots   = CalcLots(isLong, level, sl);
   double stops  = StopsDistance();
   g_planLots[idx] = lots;
   g_planSL[idx]   = sl;

   if(lots <= 0.0)
     {
      g_sideSkipped[idx] = true;
      PrintFormat("GBO: %s uebersprungen - Size unter Min-Lot (Range %.2f-%.2f)", isLong ? "Long" : "Short", g_rangeLow, g_rangeHigh);
      return;
     }
   if(MathAbs(level - sl) < stops)
     {
      g_sideSkipped[idx] = true;
      PrintFormat("GBO: %s uebersprungen - SL-Distanz kleiner als Stops-Level", isLong ? "Long" : "Short");
      return;
     }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bool ok = false;
   g_lastTry[idx] = now;

   if(isLong)
     {
      if(ask < level - stops)
         ok = trade.BuyStop(lots, level, _Symbol, sl, 0.0, ORDER_TIME_GTC, 0, InpComment + " long");
      else if(ask >= level && InpMarketFallback && g_seenInside)
        {
         double mSl   = PlannedSL(true, ask);
         double mLots = CalcLots(true, ask, mSl);
         if(mLots > 0.0)
            ok = trade.Buy(mLots, _Symbol, 0.0, mSl, 0.0, InpComment + " long mkt");
        }
      else
         return; // zu nah am Level oder Preis noch nie in der Range gesehen -> warten
     }
   else
     {
      if(bid > level + stops)
         ok = trade.SellStop(lots, level, _Symbol, sl, 0.0, ORDER_TIME_GTC, 0, InpComment + " short");
      else if(bid <= level && InpMarketFallback && g_seenInside)
        {
         double mSl   = PlannedSL(false, bid);
         double mLots = CalcLots(false, bid, mSl);
         if(mLots > 0.0)
            ok = trade.Sell(mLots, _Symbol, 0.0, mSl, 0.0, InpComment + " short mkt");
        }
      else
         return;
     }

   if(!ok)
      PrintFormat("GBO: %s Order fehlgeschlagen: %d %s", isLong ? "Long" : "Short", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   else
      g_histDirty = true;
  }

//--- %-Modus: SL einmalig vom echten Fillpreis neu setzen (wie im Pine-Script)
void ManageOpenPosition(ulong ticket, bool isLong, double openPrice, double curSl, double curTp)
  {
   if(InpSLMode != GBO_SL_PERCENT || ticket == g_slFixedTicket) return;

   double want = PlannedSL(isLong, openPrice);
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0.0) tick = _Point;
   if(MathAbs(curSl - want) < tick * 0.5) { g_slFixedTicket = ticket; return; }

   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double stops = StopsDistance();

   // Preis schon jenseits des neuen SL -> sofort schliessen (Pine fuellt den Stop am naechsten Bar)
   if((isLong && bid <= want) || (!isLong && ask >= want))
     {
      CloseAllPositions("SL nach Fill-Reset bereits erreicht");
      g_slFixedTicket = ticket;
      return;
     }
   if((isLong && bid - want < stops) || (!isLong && want - ask < stops))
      return; // Stops-Level verletzt -> naechster Tick

   if(trade.PositionModify(ticket, want, curTp))
      g_slFixedTicket = ticket;
   else
      PrintFormat("GBO: SL-Reset fehlgeschlagen: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
  }

//--- Guard-Zustand persistieren (ueberlebt Neustart des Terminals)
void SaveGuardState()
  {
   GlobalVariableSet(g_prefix + "day",    g_dayKey);
   GlobalVariableSet(g_prefix + "peak",   g_dayPeak);
   GlobalVariableSet(g_prefix + "halted", g_halted ? 1.0 : 0.0);
  }

void OnNewDay(datetime now)
  {
   g_dayKey       = DayKeyOf(now);
   g_dayStart     = DayStartOf(now);
   g_rangeReady   = false;
   g_noRangeToday = false;
   g_rangeHigh    = 0.0;
   g_rangeLow     = 0.0;
   g_lastRangeTry = 0;
   g_seenInside   = false;
   g_histDirty    = true;
   g_lastTry[0]   = 0; g_lastTry[1] = 0;
   g_sideSkipped[0] = false; g_sideSkipped[1] = false;
   g_planLots[0]  = 0; g_planLots[1] = 0;
   g_planSL[0]    = 0; g_planSL[1]   = 0;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_halted  = false;
   g_dayPeak = equity;
   if(GlobalVariableCheck(g_prefix + "day") && (int)GlobalVariableGet(g_prefix + "day") == g_dayKey)
     {
      g_dayPeak = MathMax(equity, GlobalVariableGet(g_prefix + "peak"));
      g_halted  = GlobalVariableGet(g_prefix + "halted") > 0.5;
     }
   SaveGuardState();
  }

//--- Range auf dem Chart
void DrawRange(double hi, double lo, bool isFinal)
  {
   if(!InpDrawRange) return;
   string key = IntegerToString(g_dayKey);
   datetime t1 = g_dayStart + InpRangeStartHour * 3600;
   datetime t2 = g_dayStart + InpRangeEndHour * 3600;
   datetime t3 = g_dayStart + InpCloseHour * 3600;

   string box = g_prefix + "box_" + key;
   if(ObjectFind(0, box) < 0)
     {
      ObjectCreate(0, box, OBJ_RECTANGLE, 0, t1, hi, t2, lo);
      ObjectSetInteger(0, box, OBJPROP_COLOR, InpRangeColor);
      ObjectSetInteger(0, box, OBJPROP_FILL, true);
      ObjectSetInteger(0, box, OBJPROP_BACK, true);
      ObjectSetInteger(0, box, OBJPROP_SELECTABLE, false);
     }
   ObjectSetDouble(0, box, OBJPROP_PRICE, 0, hi);
   ObjectSetDouble(0, box, OBJPROP_PRICE, 1, lo);

   if(!isFinal) return;
   string names[2] = {"hi_", "lo_"};
   double prices[2];
   prices[0] = hi; prices[1] = lo;
   for(int i = 0; i < 2; i++)
     {
      string n = g_prefix + names[i] + key;
      if(ObjectFind(0, n) >= 0) continue;
      ObjectCreate(0, n, OBJ_TREND, 0, t2, prices[i], t3, prices[i]);
      ObjectSetInteger(0, n, OBJPROP_COLOR, InpRangeColor);
      ObjectSetInteger(0, n, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, n, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
     }
  }

void UpdatePanel(int hour, bool inPos, bool posLong)
  {
   if(!InpShowPanel) return;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double dd     = g_dayPeak > 0 ? (g_dayPeak - equity) / g_dayPeak * 100.0 : 0.0;
   string rng    = g_rangeReady ? StringFormat("%s - %s  (Breite %s)", DoubleToString(g_rangeLow, _Digits), DoubleToString(g_rangeHigh, _Digits), DoubleToString(g_rangeHigh - g_rangeLow, _Digits))
                                : (g_noRangeToday ? "keine Daten heute" : "-");
   string txt = "Gold Breakout 15M  |  " + _Symbol + "  |  Magic " + (string)InpMagic + "\n";
   txt += StringFormat("Serverzeit: %s   Session %02d:00-%02d:00, Close %02d:00\n",
                       TimeToString(TimeTradeServer(), TIME_DATE | TIME_MINUTES), InpRangeStartHour, InpRangeEndHour, InpCloseHour);
   txt += "Status: " + g_status + "\n";
   txt += "Range: " + rng + "\n";
   if(g_rangeReady)
      txt += StringFormat("Plan Long: %.2f Lots, SL %s   |   Plan Short: %.2f Lots, SL %s\n",
                          g_planLots[0], DoubleToString(g_planSL[0], _Digits), g_planLots[1], DoubleToString(g_planSL[1], _Digits));
   txt += StringFormat("Equity: %.2f   Tages-Peak: %.2f   Tages-DD: %.2f%%%s\n", equity, g_dayPeak, dd,
                       InpGuardOn ? StringFormat("  (Guard %.1f%%%s)", InpGuardDD, g_halted ? " AUSGELOEST" : "") : "");
   Comment(txt);
  }

//+------------------------------------------------------------------+
//| Kernlogik (OnTick + OnTimer)                                     |
//+------------------------------------------------------------------+
void Process()
  {
   datetime now = TimeTradeServer();
   if(DayKeyOf(now) != g_dayKey) OnNewDay(now);
   int hour = HourOf(now);

   //--- Equity / Guard
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity > g_dayPeak) { g_dayPeak = equity; SaveGuardState(); }
   if(InpGuardOn && !g_halted && g_dayPeak > 0.0 && (g_dayPeak - equity) / g_dayPeak * 100.0 >= InpGuardDD)
     {
      DeletePendings();
      CloseAllPositions("Equity Guard");
      g_halted = true;
      SaveGuardState();
      PrintFormat("GBO: Equity Guard ausgeloest (DD %.2f%% >= %.2f%%) - Trading bis morgen gestoppt",
                  (g_dayPeak - equity) / g_dayPeak * 100.0, InpGuardDD);
     }

   bool inWindow = (hour >= InpRangeEndHour && hour < InpCloseHour);
   bool inRange  = (hour >= InpRangeStartHour && hour < InpRangeEndHour);

   //--- offene Position
   ulong ticket; bool posLong; double openPrice, curSl, curTp;
   bool inPos = FindPosition(ticket, posLong, openPrice, curSl, curTp);
   if(inPos)
     {
      DeletePendings();                       // OCA
      if(!inWindow)
        {
         CloseAllPositions(StringFormat("Time Exit %02d:00", InpCloseHour));
         g_status = "Time Exit";
        }
      else
        {
         ManageOpenPosition(ticket, posLong, openPrice, curSl, curTp);
         g_status = posLong ? "Long offen" : "Short offen";
        }
      UpdatePanel(hour, inPos, posLong);
      return;
     }

   //--- Range waehrend des Aufbaus (nur Anzeige)
   if(inRange)
     {
      g_seenInside = true;   // EA lief waehrend des Range-Aufbaus
      static datetime lastBar = 0;
      datetime bar = iTime(_Symbol, InpRangeTF, 0);
      if(bar != lastBar)
        {
         double hi, lo;
         if(ComputeRange(g_dayStart + InpRangeStartHour * 3600, now, hi, lo))
           {
            DrawRange(hi, lo, false);
            lastBar = bar;
           }
        }
     }

   bool traded = TradedToday();
   if(g_halted || traded || !inWindow)
     {
      DeletePendings();
      g_status = g_halted ? "Gestoppt: Equity Guard" : traded ? "Fertig fuer heute" : inRange ? "Range wird gebaut" : "Ausserhalb Session";
      UpdatePanel(hour, false, false);
      return;
     }

   //--- finale Range bauen (einmal pro Tag)
   if(!g_rangeReady && !g_noRangeToday && now - g_lastRangeTry >= 10)
     {
      g_lastRangeTry = now;
      double hi, lo;
      datetime from = g_dayStart + InpRangeStartHour * 3600;
      datetime to   = g_dayStart + InpRangeEndHour * 3600 - 1;
      if(ComputeRange(from, to, hi, lo))
        {
         if(hi > lo)
           {
            g_rangeHigh  = NormPrice(hi);
            g_rangeLow   = NormPrice(lo);
            g_rangeReady = true;
            DrawRange(g_rangeHigh, g_rangeLow, true);
            PrintFormat("GBO: Range %d fix: %s - %s", g_dayKey, DoubleToString(g_rangeLow, _Digits), DoubleToString(g_rangeHigh, _Digits));
           }
         else
            g_noRangeToday = true;
        }
      else if(SeriesInfoInteger(_Symbol, InpRangeTF, SERIES_SYNCHRONIZED) &&
              iTime(_Symbol, InpRangeTF, 0) >= to)
         g_noRangeToday = true;   // Daten synchron, aber keine Bars im Fenster (Feiertag)
     }
   if(!g_rangeReady)
     {
      g_status = g_noRangeToday ? "Keine Range heute" : "Warte auf Range-Daten";
      UpdatePanel(hour, false, false);
      return;
     }

   //--- wurde Preis innerhalb der Range gesehen? (Schutz gegen spaeten EA-Start nach Ausbruch)
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(bid > g_rangeLow && bid < g_rangeHigh) g_seenInside = true;

   ArmSide(true);
   ArmSide(false);
   g_status = g_seenInside ? "Warte auf Breakout" : "Warte bis Preis zurueck in Range";
   UpdatePanel(hour, false, false);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!(InpRangeStartHour >= 0 && InpRangeStartHour < InpRangeEndHour && InpRangeEndHour < InpCloseHour && InpCloseHour <= 23))
     {
      Print("GBO: Stunden muessen gelten: 0 <= Range Start < Range Ende < Close Hour <= 23");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpSLPercent <= 0.0 || InpRiskPercent <= 0.0 || InpFixedLots <= 0.0)
     {
      Print("GBO: SL%, Risiko% und Fixe Lots muessen > 0 sein");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(PeriodSeconds(InpRangeTF) > 3600 || 3600 % PeriodSeconds(InpRangeTF) != 0)
      Print("GBO: Warnung - Range-Timeframe teilt die Stunde nicht sauber, Range kann ungenau sein");

   g_prefix = StringFormat("GBO_%s_%I64u_", _Symbol, InpMagic);
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPts);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetMarginMode();

   g_dayKey = -1;
   g_slFixedTicket = 0;
   EventSetTimer(5);   // Time-Exit / Guard auch ohne Ticks
   Process();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
   if(reason == REASON_REMOVE)
      ObjectsDeleteAll(0, g_prefix);
  }

void OnTick()  { Process(); }
void OnTimer() { Process(); }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD) g_histDirty = true;
  }
//+------------------------------------------------------------------+
