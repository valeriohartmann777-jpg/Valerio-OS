//+------------------------------------------------------------------+
//|                                          XAUUSD_Scalper_Lab.mq5  |
//|   Four public scalping strategies for XAUUSD in one EA, built    |
//|   to be compared in the MT5 optimizer on real ticks.             |
//|                                                                  |
//|   One position at a time, broker-side SL/TP, ATR based exits,    |
//|   signals on CLOSED bars only. No martingale, no grid.           |
//+------------------------------------------------------------------+
#property copyright   "Valerio OS"
#property version     "1.00"
#property description "XAUUSD scalping lab: ORB, BB+RSI reversion, EMA pullback, Donchian breakout."

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| Strategies                                                       |
//+------------------------------------------------------------------+
enum StrategyType
{
   STRAT_SESSION_ORB,       // Session opening range breakout
   STRAT_BB_RSI_REVERSION,  // Bollinger + RSI mean reversion (range regime)
   STRAT_EMA_PULLBACK,      // Pullback to fast EMA in EMA trend
   STRAT_DONCHIAN_BREAKOUT  // N-bar breakout in slow-EMA direction
};

//+------------------------------------------------------------------+
//| Inputs                                                           |
//| Pips: for XAU/GOLD symbols 1 pip = 0.10 price units.             |
//| Hours are BROKER SERVER TIME (most gold brokers: GMT+2/+3, so    |
//| London open = 10, New York open = 16 or 17).                     |
//+------------------------------------------------------------------+
input group "=== Strategy ==="
input StrategyType    Strategy        = STRAT_SESSION_ORB;
input ENUM_TIMEFRAMES SignalTimeframe = PERIOD_M5;
input ulong           MagicNumber     = 888222;

input group "=== Exits (ATR based) ==="
input int    AtrPeriod      = 14;
input double StopLossATR    = 1.5;
input double TakeProfitATR  = 2.0;
input double BreakEvenATR   = 0.0;    // >0: SL to entry after this profit (in ATR)
input int    MaxHoldMinutes = 120;    // 0 = off

input group "=== Position size ==="
input double LotSize     = 0.01;      // used when RiskPercent = 0
input double RiskPercent = 0.0;       // >0: lots from % of balance risked at SL
input double MaxLotSize  = 1.0;

input group "=== Filters / risk ==="
input int    MaxSpreadPips   = 5;     // 5 pips = 0.50 on gold
input int    TradeStartHour  = 8;     // entries only in [start, end), server time
input int    TradeEndHour    = 22;
input int    MaxTradesPerDay = 10;
input double MaxDailyLossUSD = 20.0;  // realized + floating, account currency

input group "=== Session ORB ==="
input int OrbStartHour    = 10;       // server hour the opening range starts
input int OrbRangeMinutes = 30;
input int OrbEntryMinutes = 120;      // breakout must happen within this window after the range

input group "=== BB + RSI reversion ==="
input int    BbPeriod  = 20;
input double BbDev     = 2.0;
input int    RsiPeriod = 7;
input double RsiLow    = 25.0;
input double RsiHigh   = 75.0;
input double AdxMax    = 25.0;        // trade only while ADX below (range regime); 0 = off
input int    AdxPeriod = 14;

input group "=== EMA pullback / Donchian ==="
input int EmaFast      = 20;
input int EmaMid       = 50;
input int EmaSlow      = 200;
input int DonchianBars = 20;

input group "=== Misc ==="
input int  MaxDeviationPoints = 30;
input int  MaxTradeRetries    = 2;
input int  MinTestTrades      = 100;  // OnTester: fewer trades -> criterion 0
input bool DebugLogging       = true;
input bool ShowStatusPanel    = true;

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
CTrade  trade;
MqlTick g_tick;

double g_point    = 0.0;
double g_tickSize = 0.0;
double g_pipSize  = 0.0;
int    g_digits   = 0;

int g_hAtr = INVALID_HANDLE, g_hBb = INVALID_HANDLE, g_hRsi = INVALID_HANDLE, g_hAdx = INVALID_HANDLE;
int g_hEmaFast = INVALID_HANDLE, g_hEmaMid = INVALID_HANDLE, g_hEmaSlow = INVALID_HANDLE;

datetime g_lastBarTime = 0;
double   g_atr         = 0.0;   // ATR of the last closed bar, refreshed on every new bar

// daily accounting (rebuilt from history on new deal / new day -> restart safe)
datetime g_dayStart      = 0;
bool     g_dailyDirty    = true;
double   g_dailyRealized = 0.0;
int      g_tradesToday   = 0;
int      g_buysToday     = 0;
int      g_sellsToday    = 0;

// ORB range of the current day
datetime g_orbDay    = 0;
bool     g_orbReady  = false;
double   g_orbHigh   = 0.0;
double   g_orbLow    = 0.0;
datetime g_orbEnd    = 0;

// edge-triggered log flags
string g_lastBlock = "";
long   g_lastPanelMs = 0;

//+------------------------------------------------------------------+
//| Logging                                                          |
//+------------------------------------------------------------------+
void Log(const string msg)       { if(DebugLogging) Print("[LAB] ", msg); }
void LogAlways(const string msg) { Print("[LAB] ", msg); }

void LogBlock(const string reason)
{
   if(reason != g_lastBlock && reason != "")
      Log("No entry: " + reason);
   g_lastBlock = reason;
}

//+------------------------------------------------------------------+
//| Symbol helpers                                                   |
//+------------------------------------------------------------------+
double DeterminePipSize()
{
   string sym = _Symbol;
   StringToUpper(sym);
   if(StringFind(sym, "XAU") >= 0 || StringFind(sym, "GOLD") >= 0)
      return MathMax(0.1, g_point);
   if(g_digits == 3 || g_digits == 5)
      return g_point * 10.0;
   return g_point;
}

double NormalizePrice(const double price)
{
   if(g_tickSize <= 0.0)
      return NormalizeDouble(price, g_digits);
   return NormalizeDouble(MathRound(price / g_tickSize) * g_tickSize, g_digits);
}

double NormalizeVolumeDown(const double requested)
{
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double v = requested;
   if(step > 0.0)
      v = MathFloor(v / step + 1e-9) * step;
   if(vmax > 0.0 && v > vmax)
      v = vmax;
   if(v < vmin - 1e-9)
      return 0.0;   // caller decides: too small for the broker
   int volDigits = (step > 0.0) ? (int)MathMax(0.0, MathCeil(-MathLog10(step) - 1e-9)) : 2;
   return NormalizeDouble(v, volDigits);
}

double BrokerMinDistance()
{
   long stops  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   return MathMax((double)stops, (double)freeze) * g_point + g_tickSize;
}

double SpreadPips()
{
   return (g_pipSize > 0.0) ? (g_tick.ask - g_tick.bid) / g_pipSize : 0.0;
}

ENUM_ORDER_TYPE_FILLING DetermineFilling()
{
   long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

bool InHourWindow(const int startHour, const int endHour)
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(startHour == endHour)
      return true;
   if(startHour < endHour)
      return (dt.hour >= startHour && dt.hour < endHour);
   return (dt.hour >= startHour || dt.hour < endHour);
}

//+------------------------------------------------------------------+
//| Indicator access (series order: index 0 = shift 'start')         |
//+------------------------------------------------------------------+
bool ReadBuffer(const int handle, const int bufferIndex, const int start, const int count, double &out[])
{
   if(handle == INVALID_HANDLE)
      return false;
   ArraySetAsSeries(out, true);
   return (CopyBuffer(handle, bufferIndex, start, count, out) == count);
}

bool ReadRates(const int start, const int count, MqlRates &out[])
{
   ArraySetAsSeries(out, true);
   return (CopyRates(_Symbol, SignalTimeframe, start, count, out) == count);
}

bool IsNewBar()
{
   datetime t = iTime(_Symbol, SignalTimeframe, 0);
   if(t == 0 || t == g_lastBarTime)
      return false;
   g_lastBarTime = t;
   return true;
}

//+------------------------------------------------------------------+
//| Account state                                                    |
//+------------------------------------------------------------------+
// Returns number of own positions; ticket of the first one in 'ticket'.
int OwnPositions(ulong &ticket, double &floating)
{
   int n = 0;
   ticket = 0;
   floating = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(n == 0)
         ticket = t;
      floating += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      n++;
   }
   return n;
}

int OwnMarketOrdersInFlight()
{
   int n = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong t = OrderGetTicket(i);
      if(t == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || (ulong)OrderGetInteger(ORDER_MAGIC) != MagicNumber)
         continue;
      n++;
   }
   return n;
}

void CheckNewDay()
{
   datetime now = TimeCurrent();
   datetime dayStart = (datetime)(((long)now / 86400) * 86400);
   if(dayStart != g_dayStart)
   {
      g_dayStart   = dayStart;
      g_dailyDirty = true;
   }
}

// Realized net PnL and entry counts of today, own symbol + magic.
// Exit deals without our magic are included when their position was opened by us.
void RecalcDaily()
{
   g_dailyDirty = false;
   if(!HistorySelect(g_dayStart, TimeCurrent() + 86400))
      return;

   int total = HistoryDealsTotal();
   ulong ids[];
   int idCount = 0;
   int trades = 0, buys = 0, sells = 0;

   for(int i = 0; i < total; i++)
   {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol)
         continue;
      if((ulong)HistoryDealGetInteger(d, DEAL_MAGIC) != MagicNumber)
         continue;
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
      {
         trades++;
         if((ENUM_DEAL_TYPE)HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BUY)
            buys++;
         else
            sells++;
         ArrayResize(ids, idCount + 1);
         ids[idCount++] = (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID);
      }
   }

   double sum = 0.0;
   for(int i = 0; i < total; i++)
   {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol)
         continue;
      bool ours = ((ulong)HistoryDealGetInteger(d, DEAL_MAGIC) == MagicNumber);
      if(!ours)
      {
         ulong pid = (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID);
         for(int k = 0; k < idCount; k++)
            if(ids[k] == pid) { ours = true; break; }
      }
      if(ours)
         sum += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_COMMISSION)
              + HistoryDealGetDouble(d, DEAL_SWAP) + HistoryDealGetDouble(d, DEAL_FEE);
   }

   g_dailyRealized = sum;
   g_tradesToday   = trades;
   g_buysToday     = buys;
   g_sellsToday    = sells;
}

//+------------------------------------------------------------------+
//| Session ORB: range is rebuilt from history (restart safe)        |
//+------------------------------------------------------------------+
void UpdateOrbRange()
{
   datetime rangeStart = g_dayStart + OrbStartHour * 3600;
   datetime rangeEnd   = rangeStart + OrbRangeMinutes * 60;

   if(g_orbDay != g_dayStart)
   {
      g_orbDay   = g_dayStart;
      g_orbReady = false;
      g_orbEnd   = rangeEnd;
   }
   if(g_orbReady || TimeCurrent() < rangeEnd)
      return;

   MqlRates r[];
   int n = CopyRates(_Symbol, SignalTimeframe, rangeStart, rangeEnd - 1, r);
   if(n <= 0)
      return;
   double hi = r[0].high, lo = r[0].low;
   for(int i = 1; i < n; i++)
   {
      hi = MathMax(hi, r[i].high);
      lo = MathMin(lo, r[i].low);
   }
   g_orbHigh  = hi;
   g_orbLow   = lo;
   g_orbReady = true;
   Log(StringFormat("ORB range ready: high %s low %s (%.1f pips)",
                    DoubleToString(hi, g_digits), DoubleToString(lo, g_digits), (hi - lo) / g_pipSize));
}

//+------------------------------------------------------------------+
//| Signals: +1 buy, -1 sell, 0 none. Evaluated on closed bars only. |
//+------------------------------------------------------------------+
int SignalOrb()
{
   if(!g_orbReady)
      return 0;
   if(TimeCurrent() >= g_orbEnd + OrbEntryMinutes * 60)
      return 0;
   MqlRates r[];
   if(!ReadRates(1, 1, r))
      return 0;
   if(r[0].close > g_orbHigh && g_buysToday == 0)
      return 1;
   if(r[0].close < g_orbLow && g_sellsToday == 0)
      return -1;
   return 0;
}

int SignalBbRsi()
{
   MqlRates r[];
   double upper[], lower[], rsi[], adx[];
   if(!ReadRates(1, 2, r) || !ReadBuffer(g_hBb, 1, 1, 2, upper) || !ReadBuffer(g_hBb, 2, 1, 2, lower) ||
      !ReadBuffer(g_hRsi, 0, 1, 2, rsi))
      return 0;
   if(AdxMax > 0.0)
   {
      if(!ReadBuffer(g_hAdx, 0, 1, 1, adx))
         return 0;
      if(adx[0] >= AdxMax)
         return 0;
   }
   // index 0 = last closed bar, 1 = bar before. Entry when price closes back inside the band.
   if(r[1].close < lower[1] && r[0].close > lower[0] && rsi[1] < RsiLow)
      return 1;
   if(r[1].close > upper[1] && r[0].close < upper[0] && rsi[1] > RsiHigh)
      return -1;
   return 0;
}

int SignalEmaPullback()
{
   MqlRates r[];
   double fast[], mid[], slow[];
   if(!ReadRates(1, 1, r) || !ReadBuffer(g_hEmaFast, 0, 1, 1, fast) ||
      !ReadBuffer(g_hEmaMid, 0, 1, 1, mid) || !ReadBuffer(g_hEmaSlow, 0, 1, 1, slow))
      return 0;
   bool upTrend   = fast[0] > mid[0] && mid[0] > slow[0];
   bool downTrend = fast[0] < mid[0] && mid[0] < slow[0];
   // bar touched the fast EMA and closed back in trend direction with a body in that direction
   if(upTrend && r[0].low <= fast[0] && r[0].close > fast[0] && r[0].close > r[0].open)
      return 1;
   if(downTrend && r[0].high >= fast[0] && r[0].close < fast[0] && r[0].close < r[0].open)
      return -1;
   return 0;
}

int SignalDonchian()
{
   MqlRates r[];
   double slow[], highs[], lows[];
   if(!ReadRates(1, 1, r) || !ReadBuffer(g_hEmaSlow, 0, 1, 1, slow))
      return 0;
   if(CopyHigh(_Symbol, SignalTimeframe, 2, DonchianBars, highs) != DonchianBars ||
      CopyLow(_Symbol, SignalTimeframe, 2, DonchianBars, lows) != DonchianBars)
      return 0;
   double hh = highs[ArrayMaximum(highs)];
   double ll = lows[ArrayMinimum(lows)];
   if(r[0].close > hh && r[0].close > slow[0])
      return 1;
   if(r[0].close < ll && r[0].close < slow[0])
      return -1;
   return 0;
}

int GetSignal()
{
   switch(Strategy)
   {
      case STRAT_SESSION_ORB:       return SignalOrb();
      case STRAT_BB_RSI_REVERSION:  return SignalBbRsi();
      case STRAT_EMA_PULLBACK:      return SignalEmaPullback();
      case STRAT_DONCHIAN_BREAKOUT: return SignalDonchian();
   }
   return 0;
}

//+------------------------------------------------------------------+
//| Entry                                                            |
//+------------------------------------------------------------------+
bool EntryAllowed(const double floating)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
      { LogBlock("AutoTrading disabled"); return false; }
   if((ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL)
      { LogBlock("symbol not fully tradable"); return false; }
   if(MaxDailyLossUSD > 0.0 && g_dailyRealized + floating <= -MaxDailyLossUSD)
      { LogBlock("daily loss limit reached"); return false; }
   if(MaxTradesPerDay > 0 && g_tradesToday >= MaxTradesPerDay)
      { LogBlock("max trades per day reached"); return false; }
   if(!InHourWindow(TradeStartHour, TradeEndHour))
      { LogBlock("outside trading hours"); return false; }
   if(MaxSpreadPips > 0 && SpreadPips() > MaxSpreadPips)
      { LogBlock(StringFormat("spread %.1f pips > %d", SpreadPips(), MaxSpreadPips)); return false; }
   if(g_atr <= 0.0)
      { LogBlock("ATR not ready"); return false; }
   LogBlock("");
   return true;
}

double CalcVolume(const double slDist)
{
   if(RiskPercent <= 0.0)
      return NormalizeVolumeDown(LotSize);
   double tickValue   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double lossPerLot  = (g_tickSize > 0.0) ? slDist / g_tickSize * tickValue : 0.0;
   if(lossPerLot <= 0.0)
      return 0.0;
   double vol = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0 / lossPerLot;
   return NormalizeVolumeDown(MathMin(vol, MaxLotSize));
}

bool OpenTrade(const bool isBuy)
{
   double minDist = BrokerMinDistance();
   double slDist  = MathMax(StopLossATR * g_atr, minDist);
   double tpDist  = MathMax(TakeProfitATR * g_atr, minDist);
   double vol     = CalcVolume(slDist);
   if(vol <= 0.0)
   {
      LogAlways("Position size below broker minimum for the configured risk - trade skipped");
      return false;
   }

   double margin = 0.0;
   double price  = isBuy ? g_tick.ask : g_tick.bid;
   if(!OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, vol, price, margin) ||
      margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
   {
      LogAlways("Not enough free margin - trade skipped");
      return false;
   }

   for(int attempt = 0; attempt <= MaxTradeRetries; attempt++)
   {
      if(attempt > 0 && !SymbolInfoTick(_Symbol, g_tick))
         break;
      double entry = isBuy ? g_tick.ask : g_tick.bid;
      double sl = NormalizePrice(isBuy ? entry - slDist : entry + slDist);
      double tp = NormalizePrice(isBuy ? entry + tpDist : entry - tpDist);
      string comment = StringFormat("LAB %d", (int)Strategy);

      ResetLastError();
      bool ok = isBuy ? trade.Buy(vol, _Symbol, 0.0, sl, tp, comment)
                      : trade.Sell(vol, _Symbol, 0.0, sl, tp, comment);
      uint rc = trade.ResultRetcode();
      if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL || rc == TRADE_RETCODE_PLACED))
      {
         LogAlways(StringFormat("%s %s %.2f @ %s SL %s TP %s (ATR %.2f, spread %.1f pips)",
                                EnumToString(Strategy), isBuy ? "BUY" : "SELL", vol,
                                DoubleToString(trade.ResultPrice(), g_digits),
                                DoubleToString(sl, g_digits), DoubleToString(tp, g_digits), g_atr, SpreadPips()));
         g_dailyDirty = true;
         return true;
      }
      if(rc != TRADE_RETCODE_REQUOTE && rc != TRADE_RETCODE_PRICE_CHANGED && rc != TRADE_RETCODE_PRICE_OFF)
      {
         LogAlways(StringFormat("Entry rejected: retcode %u (%s), error %d",
                                rc, trade.ResultRetcodeDescription(), GetLastError()));
         return false;
      }
   }
   LogAlways("Entry failed after retries");
   return false;
}

//+------------------------------------------------------------------+
//| Open position management                                         |
//+------------------------------------------------------------------+
void ClosePosition(const ulong ticket, const string reason)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if(trade.PositionClose(ticket))
      LogAlways(StringFormat("Position #%I64u closed: %s", ticket, reason));
   else
      LogAlways(StringFormat("Close #%I64u failed: retcode %u (%s)", ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription()));
}

void ManagePosition(const ulong ticket, const double floating)
{
   if(!PositionSelectByTicket(ticket))
      return;

   // risk first: daily limit closes the open trade as well
   if(MaxDailyLossUSD > 0.0 && g_dailyRealized + floating <= -MaxDailyLossUSD)
   {
      ClosePosition(ticket, "daily loss limit");
      return;
   }

   datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
   if(MaxHoldMinutes > 0 && TimeCurrent() - opened >= MaxHoldMinutes * 60)
   {
      ClosePosition(ticket, "max hold time");
      return;
   }

   if(BreakEvenATR <= 0.0 || g_atr <= 0.0)
      return;

   bool   isBuy = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
   double open  = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl    = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   double minDist = BrokerMinDistance();
   double cushion = 2.0 * g_tickSize;

   if(isBuy)
   {
      double newSl = NormalizePrice(open + cushion);
      if(g_tick.bid - open >= BreakEvenATR * g_atr && (sl < newSl || sl == 0.0) && newSl <= g_tick.bid - minDist)
         if(trade.PositionModify(ticket, newSl, tp))
            Log(StringFormat("Break-even set #%I64u @ %s", ticket, DoubleToString(newSl, g_digits)));
   }
   else
   {
      double newSl = NormalizePrice(open - cushion);
      if(open - g_tick.ask >= BreakEvenATR * g_atr && (sl > newSl || sl == 0.0) && newSl >= g_tick.ask + minDist)
         if(trade.PositionModify(ticket, newSl, tp))
            Log(StringFormat("Break-even set #%I64u @ %s", ticket, DoubleToString(newSl, g_digits)));
   }
}

//+------------------------------------------------------------------+
//| Panel                                                            |
//+------------------------------------------------------------------+
void UpdatePanel(const int positions, const double floating)
{
   if(!ShowStatusPanel)
      return;
   long nowMs = (long)g_tick.time_msc;
   if(g_lastPanelMs != 0 && nowMs - g_lastPanelMs < 500)
      return;
   g_lastPanelMs = nowMs;

   string orb = "";
   if(Strategy == STRAT_SESSION_ORB)
      orb = g_orbReady ? StringFormat("\nORB: %s - %s", DoubleToString(g_orbLow, g_digits), DoubleToString(g_orbHigh, g_digits))
                       : "\nORB: building / waiting";

   Comment(StringFormat("XAUUSD Scalper Lab\nStrategy: %s (%s)\nSpread: %.1f pips | ATR: %.2f\n"
                        "Position: %d | Floating: %+.2f\nTrades today: %d / %d\nDaily PnL: %+.2f (limit -%.2f)\n"
                        "Last block: %s%s",
                        EnumToString(Strategy), EnumToString(SignalTimeframe), SpreadPips(), g_atr,
                        positions, floating, g_tradesToday, MaxTradesPerDay,
                        g_dailyRealized, MaxDailyLossUSD, g_lastBlock == "" ? "-" : g_lastBlock, orb));
}

//+------------------------------------------------------------------+
//| Event handlers                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   g_point    = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(g_tickSize <= 0.0)
      g_tickSize = g_point;
   g_pipSize = DeterminePipSize();

   if(StopLossATR <= 0.0 || TakeProfitATR <= 0.0 || AtrPeriod <= 0 || LotSize <= 0.0 ||
      OrbStartHour < 0 || OrbStartHour > 23 || OrbRangeMinutes <= 0 || DonchianBars < 2 ||
      EmaFast <= 0 || EmaMid <= EmaFast || EmaSlow <= EmaMid)
   {
      LogAlways("Invalid input parameters");
      return INIT_PARAMETERS_INCORRECT;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints((ulong)MaxDeviationPoints);
   trade.SetTypeFilling(DetermineFilling());
   trade.SetMarginMode();
   trade.LogLevel(LOG_LEVEL_ERRORS);

   g_hAtr = iATR(_Symbol, SignalTimeframe, AtrPeriod);
   bool ok = (g_hAtr != INVALID_HANDLE);
   switch(Strategy)
   {
      case STRAT_BB_RSI_REVERSION:
         g_hBb  = iBands(_Symbol, SignalTimeframe, BbPeriod, 0, BbDev, PRICE_CLOSE);
         g_hRsi = iRSI(_Symbol, SignalTimeframe, RsiPeriod, PRICE_CLOSE);
         ok = ok && g_hBb != INVALID_HANDLE && g_hRsi != INVALID_HANDLE;
         if(AdxMax > 0.0)
         {
            g_hAdx = iADX(_Symbol, SignalTimeframe, AdxPeriod);
            ok = ok && g_hAdx != INVALID_HANDLE;
         }
         break;
      case STRAT_EMA_PULLBACK:
         g_hEmaFast = iMA(_Symbol, SignalTimeframe, EmaFast, 0, MODE_EMA, PRICE_CLOSE);
         g_hEmaMid  = iMA(_Symbol, SignalTimeframe, EmaMid, 0, MODE_EMA, PRICE_CLOSE);
         g_hEmaSlow = iMA(_Symbol, SignalTimeframe, EmaSlow, 0, MODE_EMA, PRICE_CLOSE);
         ok = ok && g_hEmaFast != INVALID_HANDLE && g_hEmaMid != INVALID_HANDLE && g_hEmaSlow != INVALID_HANDLE;
         break;
      case STRAT_DONCHIAN_BREAKOUT:
         g_hEmaSlow = iMA(_Symbol, SignalTimeframe, EmaSlow, 0, MODE_EMA, PRICE_CLOSE);
         ok = ok && g_hEmaSlow != INVALID_HANDLE;
         break;
      default:
         break;
   }
   if(!ok)
   {
      LogAlways(StringFormat("Indicator creation failed, error %d", GetLastError()));
      return INIT_FAILED;
   }

   if(!SymbolInfoTick(_Symbol, g_tick))
      ZeroMemory(g_tick);
   g_lastBarTime = iTime(_Symbol, SignalTimeframe, 0);   // first signal on the NEXT bar close
   CheckNewDay();
   RecalcDaily();

   LogAlways(StringFormat("Initialized: %s on %s %s | pip=%s | SL %.1f ATR, TP %.1f ATR | hours %d-%d server",
                          EnumToString(Strategy), _Symbol, EnumToString(SignalTimeframe),
                          DoubleToString(g_pipSize, g_digits), StopLossATR, TakeProfitATR,
                          TradeStartHour, TradeEndHour));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   int handles[7];
   handles[0] = g_hAtr; handles[1] = g_hBb; handles[2] = g_hRsi; handles[3] = g_hAdx;
   handles[4] = g_hEmaFast; handles[5] = g_hEmaMid; handles[6] = g_hEmaSlow;
   for(int i = 0; i < 7; i++)
      if(handles[i] != INVALID_HANDLE)
         IndicatorRelease(handles[i]);
   Comment("");
   LogAlways(StringFormat("Deinitialized (reason %d)", reason));
}

void OnTick()
{
   if(!SymbolInfoTick(_Symbol, g_tick) || g_tick.bid <= 0.0 || g_tick.ask <= 0.0)
      return;

   CheckNewDay();
   if(g_dailyDirty)
      RecalcDaily();

   bool newBar = IsNewBar();
   if(newBar)
   {
      double atr[];
      if(ReadBuffer(g_hAtr, 0, 1, 1, atr))
         g_atr = atr[0];
      if(Strategy == STRAT_SESSION_ORB)
         UpdateOrbRange();
   }

   ulong  ticket   = 0;
   double floating = 0.0;
   int positions = OwnPositions(ticket, floating);

   if(positions > 0)
      ManagePosition(ticket, floating);
   else if(newBar && OwnMarketOrdersInFlight() == 0 && EntryAllowed(floating))
   {
      int signal = GetSignal();
      if(signal != 0)
         OpenTrade(signal > 0);
   }

   UpdatePanel(positions, floating);
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.symbol != _Symbol)
      return;
   g_dailyDirty = true;

   if(!HistoryDealSelect(trans.deal))
      return;
   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
   {
      double net = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) + HistoryDealGetDouble(trans.deal, DEAL_COMMISSION)
                 + HistoryDealGetDouble(trans.deal, DEAL_SWAP) + HistoryDealGetDouble(trans.deal, DEAL_FEE);
      ENUM_DEAL_REASON why = (ENUM_DEAL_REASON)HistoryDealGetInteger(trans.deal, DEAL_REASON);
      Log(StringFormat("Exit %s: net %+.2f", EnumToString(why), net));
   }
}

// Optimizer criterion ("Custom max"): expectancy per trade * sqrt(trades).
// Rewards consistent edge over many trades, not a few lucky ones.
double OnTester()
{
   double trades = TesterStatistics(STAT_TRADES);
   if(trades < MinTestTrades)
      return 0.0;
   return TesterStatistics(STAT_PROFIT) / trades * MathSqrt(trades);
}
//+------------------------------------------------------------------+
