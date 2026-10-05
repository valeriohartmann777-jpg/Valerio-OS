//+------------------------------------------------------------------+
//|                                  XAUUSD_Dynamic_Straddle_EA.mq5  |
//|        Dynamic Trailing Straddle / Micro-Basket EA for MT5       |
//|                                                                  |
//|  One market position + one trailed counter STOP order.           |
//|  Basket is closed at a small net profit and immediately rebuilt. |
//|  No martingale, no lot increase, no unlimited grid.              |
//+------------------------------------------------------------------+
#property copyright   "Valerio OS"
#property version     "1.10"
#property description "Dynamic trailing straddle basket EA for XAUUSD. No martingale, no grid."

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| Enums                                                            |
//+------------------------------------------------------------------+
enum InitialDirectionMode
{
   INITIAL_BUY,
   INITIAL_SELL,
   INITIAL_ALTERNATING,
   INITIAL_TREND          // direction from EMA trend filter; no trade while flat
};

enum EAState
{
   STATE_IDLE,
   STATE_SETUP,
   STATE_ACTIVE,
   STATE_CLOSING,
   STATE_DAILY_STOP,
   STATE_ERROR_RECOVERY
};

//+------------------------------------------------------------------+
//| Inputs                                                           |
//| "Pips": see PipsToPrice(). For XAU/GOLD symbols 1 pip = 0.10     |
//| price units (10 cents) unless PipSizeOverride is set.            |
//+------------------------------------------------------------------+
input group "=== Core ==="
input double               LotSize                    = 0.01;
input int                  DistancePips               = 15;     // counter STOP distance
input double               TargetProfitUSD            = 0.80;   // net basket target (account currency)
input int                  MaxSpreadPips              = 5;      // blocks only NEW baskets (5 pips = 0.50 on gold)
input int                  StopLossPips               = 100;    // broker-side SL per position
input ulong                MagicNumber                = 888111;
input InitialDirectionMode InitialDirection           = INITIAL_TREND;

input group "=== Risk ==="
input double MaxDailyLossUSD            = 20.0;   // realized day PnL + floating basket
input double EmergencyBasketLossUSD     = 5.0;    // 0 = off
input int    MaxPositionsPerBasket      = 2;      // hard cap, no extra grid layers
input int    HedgeLockTimeoutSec        = 300;    // >0: close a fully hedged basket after N seconds (0 = wait for SL)
input double CommissionPerLotPerSideUSD = 0.0;    // estimated exit commission for open positions

input group "=== Entry filter ==="
input ENUM_TIMEFRAMES TrendTimeframe   = PERIOD_M5;  // INITIAL_TREND: timeframe of the EMAs
input int             TrendFastEMA     = 20;
input int             TrendSlowEMA     = 50;
input double          TrendMinGapPips  = 5.0;        // min EMA distance, below = flat market -> no entry
input bool            UseSessionFilter = false;      // only start baskets inside the window (server time)
input int             SessionStartHour = 8;
input int             SessionEndHour   = 20;         // exclusive; start > end wraps over midnight
input int             MinTestBaskets   = 100;        // OnTester: fewer baskets -> criterion 0

input group "=== Execution ==="
input int  MaxDeviationPoints  = 20;
input int  MinModifyStepPoints = 5;
input int  MinModifyIntervalMs = 100;
input int  MaxTradeRetries     = 2;
input bool AllowNettingMode    = false;

input group "=== Pip definition ==="
input bool   UsePointsInsteadOfPips = false;  // true: every "Pips" input is read as points
input double PipSizeOverride        = 0.0;    // >0: explicit pip size in price units

input group "=== Lifecycle / UI ==="
input bool DeletePendingOrdersOnDeinit = true;
input bool ClosePositionsOnDeinit      = false;
input bool ShowStatusPanel             = true;
input bool DebugLogging                = true;
input bool LogPendingModifies          = false;  // very verbose in the tester

//+------------------------------------------------------------------+
//| Constants                                                        |
//+------------------------------------------------------------------+
#define SETUP_TIMEOUT_MS          5000
#define CLOSE_RETRY_INTERVAL_MS   100
#define PENDING_RETRY_INTERVAL_MS 250
#define REQUEST_COOLDOWN_MS       1000
#define RECOVERY_INTERVAL_MS      500
#define SL_FIX_INTERVAL_MS        500
#define PANEL_INTERVAL_MS         500

//+------------------------------------------------------------------+
//| Account snapshot (rebuilt once per tick, only own orders)        |
//+------------------------------------------------------------------+
struct BasketSnapshot
{
   int    positions;
   int    buyPositions;
   int    sellPositions;
   int    pendings;          // all own pending orders
   int    buyStops;
   int    sellStops;
   int    otherPendings;     // own pending orders of unexpected types
   int    marketInFlight;    // own market orders still being processed
   double floatingProfit;    // POSITION_PROFIT + POSITION_SWAP
   double openVolume;
   ulong  buyStopTicket;
   double buyStopPrice;
   ulong  sellStopTicket;
   double sellStopPrice;
   long   earliestOpenMsc;
   long   latestOpenMsc;
   int    earliestType;      // ENUM_POSITION_TYPE of the oldest position
   ulong  missingSlTicket;   // first own position without SL
};

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
CTrade         trade;
EAState        g_state = STATE_IDLE;
BasketSnapshot g_snap;
MqlTick        g_tick;
long           g_nowMs = 0;              // tick time in ms (works in tester and live)

double g_point    = 0.0;
double g_tickSize = 0.0;
double g_pipSize  = 0.0;
double g_volume   = 0.0;
int    g_digits   = 0;
ENUM_ORDER_TYPE_TIME g_orderTime = ORDER_TIME_GTC;
bool   g_closeByAllowed = false;
bool   g_tradingBlocked = false;         // permanent block (e.g. netting account)

// basket bookkeeping
bool     g_nextIsBuy          = true;
bool     g_basketIsBuy        = true;
bool     g_counterTriggered   = false;   // counter STOP filled in this basket -> never re-arm
long     g_basketStartMsc     = 0;
datetime g_basketStartTime    = 0;
long     g_entrySentMs        = 0;
long     g_hedgeLockMs        = 0;
ulong    g_basketPosIds[];
double   g_basketRealized     = 0.0;
bool     g_basketRealizedDirty = false;
double   g_basketSpreadAtStart = 0.0;
bool     g_basketWasHedged    = false;
string   g_closeReason        = "";

// throttles
long g_lastModifyMs           = 0;
long g_lastPendingAttemptMs   = 0;
long g_lastCloseAttemptMs     = 0;
long g_lastRecoveryMs         = 0;
long g_lastSlFixMs            = 0;
long g_lastPanelMs            = 0;
long g_lastOrphanDeleteMs     = 0;
long g_requestCooldownUntilMs = 0;
long g_closeRetryIntervalMs   = CLOSE_RETRY_INTERVAL_MS;   // backs off after a failed close round

// daily accounting
datetime g_dayStart      = 0;
double   g_dailyRealized = 0.0;
bool     g_dailyDirty    = true;

// edge-triggered log flags
bool   g_spreadBlocked = false;
bool   g_trendBlocked   = false;
bool   g_sessionBlocked = false;
int    g_trendDir       = 0;       // +1 up, -1 down, 0 flat (last evaluation)
int    g_maFastHandle   = INVALID_HANDLE;
int    g_maSlowHandle   = INVALID_HANDLE;
bool   g_envBlocked    = false;
string g_lastEnvReason = "";

// statistics (printed in OnDeinit)
int    g_statBaskets   = 0;
int    g_statWins      = 0;
int    g_statLosses    = 0;
int    g_statHedged    = 0;
double g_statGrossWin  = 0.0;
double g_statGrossLoss = 0.0;
double g_statSpreadSum = 0.0;
double g_statHoldSum   = 0.0;

//+------------------------------------------------------------------+
//| Logging                                                          |
//+------------------------------------------------------------------+
void Log(const string message)
{
   if(DebugLogging)
      Print("[DSEA] ", message);
}

void LogAlways(const string message)
{
   Print("[DSEA] ", message);
}

string StateToString(const EAState s)
{
   switch(s)
   {
      case STATE_IDLE:           return "IDLE";
      case STATE_SETUP:          return "SETUP";
      case STATE_ACTIVE:         return "ACTIVE";
      case STATE_CLOSING:        return "CLOSING";
      case STATE_DAILY_STOP:     return "DAILY_STOP";
      case STATE_ERROR_RECOVERY: return "ERROR_RECOVERY";
   }
   return "UNKNOWN";
}

//+------------------------------------------------------------------+
//| Price / volume helpers                                           |
//+------------------------------------------------------------------+
double DeterminePipSize()
{
   if(UsePointsInsteadOfPips)
      return g_point;
   if(PipSizeOverride > 0.0)
      return PipSizeOverride;

   string sym = _Symbol;
   StringToUpper(sym);
   // Market convention for gold: 1 pip = 0.10 price units, independent of 2 or 3 quote digits.
   if(StringFind(sym, "XAU") >= 0 || StringFind(sym, "GOLD") >= 0)
      return MathMax(0.1, g_point);
   // FX-style fractional quotes
   if(g_digits == 3 || g_digits == 5)
      return g_point * 10.0;
   return g_point;
}

double PipsToPrice(const double pips)
{
   return pips * g_pipSize;
}

double PointsToPrice(const int points)
{
   return points * g_point;
}

double NormalizePriceToTickSize(const double price)
{
   if(g_tickSize <= 0.0)
      return NormalizeDouble(price, g_digits);
   return NormalizeDouble(MathRound(price / g_tickSize) * g_tickSize, g_digits);
}

double NormalizeVolume(const double requestedVolume)
{
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double v = requestedVolume;
   if(step > 0.0)
      v = MathFloor(v / step + 1e-9) * step;
   if(v < vmin)
      v = vmin;
   if(vmax > 0.0 && v > vmax)
      v = vmax;

   int volDigits = 2;
   if(step > 0.0)
      volDigits = (int)MathMax(0.0, MathCeil(-MathLog10(step) - 1e-9));
   return NormalizeDouble(v, volDigits);
}

double GetCurrentSpreadPips()
{
   if(g_pipSize <= 0.0)
      return 0.0;
   return (g_tick.ask - g_tick.bid) / g_pipSize;
}

bool IsSpreadAcceptable()
{
   if(MaxSpreadPips <= 0)
      return true;
   return GetCurrentSpreadPips() <= (double)MaxSpreadPips;
}

//+------------------------------------------------------------------+
//| Broker limits (stops level / freeze level)                       |
//+------------------------------------------------------------------+
double StopsLevelPrice()
{
   return (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * g_point;
}

double FreezeLevelPrice()
{
   return (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * g_point;
}

// Minimum distance between market and any order/SL price, plus one tick of safety.
double BrokerMinDistance()
{
   return MathMax(StopsLevelPrice(), FreezeLevelPrice()) + g_tickSize;
}

bool IsPendingPriceValid(const ENUM_ORDER_TYPE type, const double price)
{
   double minDist = MathMax(StopsLevelPrice(), FreezeLevelPrice());
   if(type == ORDER_TYPE_BUY_STOP)
      return (price > g_tick.ask && price - g_tick.ask >= minDist);
   if(type == ORDER_TYPE_SELL_STOP)
      return (price < g_tick.bid && g_tick.bid - price >= minDist);
   return false;
}

double AdjustPriceToBrokerLimits(const ENUM_ORDER_TYPE type, const double price)
{
   double minDist = BrokerMinDistance();
   double p = price;
   if(type == ORDER_TYPE_BUY_STOP)
      p = MathMax(p, g_tick.ask + minDist);
   else if(type == ORDER_TYPE_SELL_STOP)
      p = MathMin(p, g_tick.bid - minDist);
   p = NormalizePriceToTickSize(p);

   // rounding must never pull the price back inside the broker limit
   if(!IsPendingPriceValid(type, p))
   {
      if(type == ORDER_TYPE_BUY_STOP)
         p = NormalizePriceToTickSize(p + g_tickSize);
      else
         p = NormalizePriceToTickSize(p - g_tickSize);
   }
   return p;
}

// SL for a position opened at entryPrice (also used for pending orders).
double CalcStopLossFromEntry(const bool isBuy, const double entryPrice)
{
   double dist = MathMax(PipsToPrice(StopLossPips), BrokerMinDistance());
   double sl = isBuy ? entryPrice - dist : entryPrice + dist;
   return NormalizePriceToTickSize(sl);
}

// SL for a market order sent now: respects distance to the current market.
double CalcMarketStopLoss(const bool isBuy)
{
   double minDist = BrokerMinDistance();
   double sl;
   if(isBuy)
   {
      sl = CalcStopLossFromEntry(true, g_tick.ask);
      sl = MathMin(sl, g_tick.bid - minDist);
   }
   else
   {
      sl = CalcStopLossFromEntry(false, g_tick.bid);
      sl = MathMax(sl, g_tick.ask + minDist);
   }
   return NormalizePriceToTickSize(sl);
}

//+------------------------------------------------------------------+
//| Environment checks                                               |
//+------------------------------------------------------------------+
bool IsHedgingAccount()
{
   return ((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE) == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING);
}

bool IsInTradeSession()
{
   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);
   long secOfDay = (long)dt.hour * 3600 + (long)dt.min * 60 + (long)dt.sec;

   datetime from = 0, to = 0;
   bool anySession = false;
   for(uint i = 0; i < 10; i++)
   {
      if(!SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)dt.day_of_week, i, from, to))
         break;
      anySession = true;
      if(secOfDay >= (long)from && secOfDay < (long)to)
         return true;
   }
   // no session info published -> do not block
   return !anySession;
}

bool IsTradingEnvironmentValid(string &reason)
{
   if(g_tradingBlocked)                                   { reason = "trading disabled (account mode)"; return false; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))       { reason = "terminal AutoTrading off";        return false; }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))                 { reason = "EA trading not allowed";          return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))         { reason = "account trading not allowed";     return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))          { reason = "expert trading disabled by broker"; return false; }
   if((ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL)
                                                          { reason = "symbol trade mode not FULL";      return false; }
   if(g_tick.bid <= 0.0 || g_tick.ask <= 0.0 || g_tick.ask < g_tick.bid)
                                                          { reason = "invalid bid/ask";                 return false; }
   if(!IsInTradeSession())                                { reason = "outside trade session";           return false; }
   reason = "";
   return true;
}

bool HasEnoughMargin(const ENUM_ORDER_TYPE type, const double volume, const double price)
{
   double margin = 0.0;
   if(!OrderCalcMargin(type, _Symbol, volume, price, margin))
   {
      LogAlways(StringFormat("OrderCalcMargin failed, error %d", GetLastError()));
      return false;
   }
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   // Conservative: reserve margin for the counter order as well.
   return (freeMargin >= margin * 2.0);
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

//+------------------------------------------------------------------+
//| Retcode handling                                                 |
//+------------------------------------------------------------------+
bool IsSuccessRetcode(const uint rc)
{
   return (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL || rc == TRADE_RETCODE_PLACED);
}

// Only errors where the request was definitely NOT executed and an immediate retry with fresh prices makes sense.
bool IsRetryableRetcode(const uint rc)
{
   return (rc == TRADE_RETCODE_REQUOTE || rc == TRADE_RETCODE_PRICE_CHANGED || rc == TRADE_RETCODE_PRICE_OFF);
}

void HandleFailure(const string action)
{
   uint rc = trade.ResultRetcode();
   LogAlways(StringFormat("Trade request rejected: %s | retcode=%u (%s) | lastError=%d",
                          action, rc, trade.ResultRetcodeDescription(), GetLastError()));

   if(rc == TRADE_RETCODE_INVALID_STOPS || rc == TRADE_RETCODE_INVALID_PRICE)
      Log("Invalid stops/price - will recompute against broker limits on next attempt");

   if(rc == TRADE_RETCODE_TOO_MANY_REQUESTS || rc == TRADE_RETCODE_MARKET_CLOSED ||
      rc == TRADE_RETCODE_TRADE_DISABLED   || rc == TRADE_RETCODE_NO_MONEY      ||
      rc == TRADE_RETCODE_REJECT           || rc == TRADE_RETCODE_TIMEOUT       ||
      rc == TRADE_RETCODE_CONNECTION       || rc == TRADE_RETCODE_INVALID_VOLUME ||
      rc == TRADE_RETCODE_FROZEN)
      g_requestCooldownUntilMs = g_nowMs + REQUEST_COOLDOWN_MS;
}

//+------------------------------------------------------------------+
//| Basket position-id set                                           |
//+------------------------------------------------------------------+
bool InBasket(const ulong posId)
{
   int n = ArraySize(g_basketPosIds);
   for(int i = 0; i < n; i++)
      if(g_basketPosIds[i] == posId)
         return true;
   return false;
}

void AddBasketPosId(const ulong posId)
{
   if(posId == 0 || InBasket(posId))
      return;
   int n = ArraySize(g_basketPosIds);
   ArrayResize(g_basketPosIds, n + 1);
   g_basketPosIds[n] = posId;
   g_basketRealizedDirty = true;
}

void ResetBasketVars()
{
   ArrayResize(g_basketPosIds, 0);
   g_counterTriggered    = false;
   g_hedgeLockMs         = 0;
   g_basketRealized      = 0.0;
   g_basketRealizedDirty = false;
   g_basketWasHedged     = false;
   g_basketStartMsc      = 0;
   g_basketStartTime     = 0;
   g_basketSpreadAtStart = 0.0;
   g_closeReason         = "";
}

//+------------------------------------------------------------------+
//| Account scan: one pass over positions and orders                 |
//+------------------------------------------------------------------+
void ScanAccount()
{
   ZeroMemory(g_snap);
   bool trackIds = (g_state == STATE_SETUP || g_state == STATE_ACTIVE || g_state == STATE_CLOSING);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      g_snap.positions++;
      ENUM_POSITION_TYPE ptype = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(ptype == POSITION_TYPE_BUY)
         g_snap.buyPositions++;
      else
         g_snap.sellPositions++;

      g_snap.openVolume     += PositionGetDouble(POSITION_VOLUME);
      g_snap.floatingProfit += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);

      long openMsc = PositionGetInteger(POSITION_TIME_MSC);
      if(g_snap.earliestOpenMsc == 0 || openMsc < g_snap.earliestOpenMsc)
      {
         g_snap.earliestOpenMsc = openMsc;
         g_snap.earliestType    = (int)ptype;
      }
      if(openMsc > g_snap.latestOpenMsc)
         g_snap.latestOpenMsc = openMsc;

      if(g_snap.missingSlTicket == 0 && PositionGetDouble(POSITION_SL) == 0.0)
         g_snap.missingSlTicket = ticket;

      if(trackIds)
         AddBasketPosId((ulong)PositionGetInteger(POSITION_IDENTIFIER));
   }

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol)
         continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != MagicNumber)
         continue;

      ENUM_ORDER_TYPE otype = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(otype == ORDER_TYPE_BUY || otype == ORDER_TYPE_SELL)
      {
         g_snap.marketInFlight++;
         continue;
      }

      g_snap.pendings++;
      if(otype == ORDER_TYPE_BUY_STOP)
      {
         g_snap.buyStops++;
         g_snap.buyStopTicket = ticket;
         g_snap.buyStopPrice  = OrderGetDouble(ORDER_PRICE_OPEN);
      }
      else if(otype == ORDER_TYPE_SELL_STOP)
      {
         g_snap.sellStops++;
         g_snap.sellStopTicket = ticket;
         g_snap.sellStopPrice  = OrderGetDouble(ORDER_PRICE_OPEN);
      }
      else
         g_snap.otherPendings++;
   }
}

int CountEAPositions()     { return g_snap.positions; }
int CountEAPendingOrders() { return g_snap.pendings; }

//+------------------------------------------------------------------+
//| PnL                                                              |
//| Commission: entry commission is charged on the IN deal and is    |
//| therefore already contained in the realized basket/day sums.     |
//| Exit commission of OPEN positions is unknown to MT5 -> estimated |
//| via CommissionPerLotPerSideUSD * open volume.                    |
//+------------------------------------------------------------------+
double FloatingNet()
{
   return g_snap.floatingProfit - CommissionPerLotPerSideUSD * g_snap.openVolume;
}

double GetBasketProfit()
{
   return g_basketRealized + FloatingNet();
}

double GetDailyPnL()
{
   return g_dailyRealized;
}

double DealNet(const ulong deal)
{
   return HistoryDealGetDouble(deal, DEAL_PROFIT)
        + HistoryDealGetDouble(deal, DEAL_COMMISSION)
        + HistoryDealGetDouble(deal, DEAL_SWAP)
        + HistoryDealGetDouble(deal, DEAL_FEE);
}

// Sum of all own deals in [from, to]. Deals without our magic (e.g. broker SL fills)
// are included when they belong to a position that was opened with our magic.
double SumOwnDeals(const datetime from, const datetime to, const double fallback)
{
   if(!HistorySelect(from, to))
      return fallback;

   int total = HistoryDealsTotal();
   ulong ids[];
   int idCount = 0;

   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber)
         continue;
      ulong posId = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      bool known = false;
      for(int k = 0; k < idCount; k++)
         if(ids[k] == posId) { known = true; break; }
      if(!known)
      {
         ArrayResize(ids, idCount + 1);
         ids[idCount++] = posId;
      }
   }

   double sum = 0.0;
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      bool ours = ((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) == MagicNumber);
      if(!ours)
      {
         ulong posId = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
         for(int k = 0; k < idCount; k++)
            if(ids[k] == posId) { ours = true; break; }
      }
      if(ours)
         sum += DealNet(deal);
   }
   return sum;
}

void RecalcDailyPnL()
{
   g_dailyRealized = SumOwnDeals(g_dayStart, TimeCurrent() + 86400, g_dailyRealized);
   g_dailyDirty = false;
}

void RecalcBasketRealized()
{
   g_basketRealizedDirty = false;
   int n = ArraySize(g_basketPosIds);
   if(n == 0)
   {
      g_basketRealized = 0.0;
      return;
   }
   datetime from = (g_basketStartTime > 0) ? g_basketStartTime - 60 : TimeCurrent() - 86400;
   if(!HistorySelect(from, TimeCurrent() + 86400))
      return;

   double sum = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      if(InBasket((ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID)))
         sum += DealNet(deal);
   }
   g_basketRealized = sum;
}

//+------------------------------------------------------------------+
//| Risk conditions                                                  |
//+------------------------------------------------------------------+
bool BasketTargetReached(const double basketProfit)
{
   return (basketProfit >= TargetProfitUSD);
}

bool EmergencyLossReached(const double basketProfit)
{
   return (EmergencyBasketLossUSD > 0.0 && basketProfit <= -EmergencyBasketLossUSD);
}

// Conservative: realized day PnL plus floating PnL of the open basket.
bool DailyLossLimitReached()
{
   if(MaxDailyLossUSD <= 0.0)
      return false;
   return (g_dailyRealized + FloatingNet() <= -MaxDailyLossUSD);
}

bool HedgeLockTimedOut()
{
   if(HedgeLockTimeoutSec <= 0 || g_hedgeLockMs == 0)
      return false;
   return (g_nowMs - g_hedgeLockMs >= (long)HedgeLockTimeoutSec * 1000);
}

//+------------------------------------------------------------------+
//| Day change (broker server time, 00:00)                           |
//+------------------------------------------------------------------+
void CheckNewDay()
{
   datetime now = TimeCurrent();
   datetime dayStart = (datetime)(((long)now / 86400) * 86400);
   if(dayStart == g_dayStart)
      return;

   bool first = (g_dayStart == 0);
   g_dayStart   = dayStart;
   g_dailyDirty = true;
   if(first)
      return;

   Log("New trading day (server time " + TimeToString(dayStart, TIME_DATE) + ")");
   if(g_state == STATE_DAILY_STOP)
   {
      RecalcDailyPnL();
      LogAlways("Daily stop lifted - trading re-enabled");
      g_state = STATE_IDLE;
      RecoverState();
   }
}

//+------------------------------------------------------------------+
//| Trade operations                                                 |
//+------------------------------------------------------------------+
bool OpenMarket(const bool isBuy)
{
   for(int attempt = 0; attempt <= MaxTradeRetries; attempt++)
   {
      if(attempt > 0 && !SymbolInfoTick(_Symbol, g_tick))   // fresh prices for the retry
         break;

      double sl = CalcMarketStopLoss(isBuy);
      ResetLastError();
      bool ok = isBuy ? trade.Buy(g_volume, _Symbol, 0.0, sl, 0.0, "DSEA entry")
                      : trade.Sell(g_volume, _Symbol, 0.0, sl, 0.0, "DSEA entry");
      uint rc = trade.ResultRetcode();
      if(ok && IsSuccessRetcode(rc))
      {
         LogAlways(StringFormat("%s opened: %.2f lots @ %s, SL %s, deal #%I64u",
                                isBuy ? "BUY" : "SELL", g_volume,
                                DoubleToString(trade.ResultPrice(), g_digits),
                                DoubleToString(sl, g_digits), trade.ResultDeal()));
         return true;
      }
      if(!IsRetryableRetcode(rc))
         break;
      Log(StringFormat("Entry retry %d after retcode %u", attempt + 1, rc));
   }
   HandleFailure(isBuy ? "BUY entry" : "SELL entry");
   return false;
}

bool OpenInitialBuy()  { return OpenMarket(true); }
bool OpenInitialSell() { return OpenMarket(false); }

bool PlacePending(const ENUM_ORDER_TYPE type)
{
   if(g_nowMs < g_requestCooldownUntilMs)
      return false;
   if(g_lastPendingAttemptMs != 0 && g_nowMs - g_lastPendingAttemptMs < PENDING_RETRY_INTERVAL_MS)
      return false;
   g_lastPendingAttemptMs = g_nowMs;

   bool isBuyStop = (type == ORDER_TYPE_BUY_STOP);
   double dist  = PipsToPrice(DistancePips);
   double raw   = isBuyStop ? g_tick.ask + dist : g_tick.bid - dist;
   double price = AdjustPriceToBrokerLimits(type, raw);
   double sl    = CalcStopLossFromEntry(isBuyStop, price);

   if(!IsPendingPriceValid(type, price))
   {
      Log("Invalid stops: pending price violates broker limits - skipped");
      return false;
   }

   ResetLastError();
   bool ok = isBuyStop ? trade.BuyStop(g_volume, price, _Symbol, sl, 0.0, g_orderTime, 0, "DSEA counter")
                       : trade.SellStop(g_volume, price, _Symbol, sl, 0.0, g_orderTime, 0, "DSEA counter");
   if(ok && IsSuccessRetcode(trade.ResultRetcode()))
   {
      g_lastModifyMs = g_nowMs;
      LogAlways(StringFormat("%s placed @ %s (SL %s), order #%I64u",
                             isBuyStop ? "BUY STOP" : "SELL STOP",
                             DoubleToString(price, g_digits), DoubleToString(sl, g_digits),
                             trade.ResultOrder()));
      return true;
   }
   HandleFailure(isBuyStop ? "BUY STOP place" : "SELL STOP place");
   return false;
}

bool PlaceBuyStop()  { return PlacePending(ORDER_TYPE_BUY_STOP); }
bool PlaceSellStop() { return PlacePending(ORDER_TYPE_SELL_STOP); }

// Moves the counter STOP only TOWARDS the market (BUY STOP down, SELL STOP up).
bool TrailPending(const ENUM_ORDER_TYPE type, const ulong ticket, const double currentPrice)
{
   if(ticket == 0)
      return false;
   if(g_nowMs < g_requestCooldownUntilMs)
      return false;
   if(g_nowMs - g_lastModifyMs < (long)MinModifyIntervalMs)
      return false;

   bool isBuyStop = (type == ORDER_TYPE_BUY_STOP);
   double dist   = PipsToPrice(DistancePips);
   double target = AdjustPriceToBrokerLimits(type, isBuyStop ? g_tick.ask + dist : g_tick.bid - dist);
   double step   = MathMax(PointsToPrice(MinModifyStepPoints), g_tickSize);

   bool improves = isBuyStop ? (target <= currentPrice - step) : (target >= currentPrice + step);
   if(!improves)
      return false;

   // freeze level: an order this close to the market must not be modified
   double freeze = FreezeLevelPrice();
   if(freeze > 0.0)
   {
      double gap = isBuyStop ? currentPrice - g_tick.ask : g_tick.bid - currentPrice;
      if(gap <= freeze)
         return false;
   }
   if(!IsPendingPriceValid(type, target))
      return false;

   double sl = CalcStopLossFromEntry(isBuyStop, target);
   g_lastModifyMs = g_nowMs;
   ResetLastError();
   if(trade.OrderModify(ticket, target, sl, 0.0, g_orderTime, 0, 0.0) && IsSuccessRetcode(trade.ResultRetcode()))
   {
      if(LogPendingModifies)
         Log(StringFormat("Pending order modified: %s #%I64u %s -> %s",
                          isBuyStop ? "BUY STOP" : "SELL STOP", ticket,
                          DoubleToString(currentPrice, g_digits), DoubleToString(target, g_digits)));
      return true;
   }

   uint rc = trade.ResultRetcode();
   // order was triggered/deleted between scan and request -> the next scan resolves it
   if(rc == TRADE_RETCODE_INVALID_ORDER || rc == TRADE_RETCODE_INVALID)
   {
      Log("Modify skipped: order no longer pending (probably triggered)");
      return false;
   }
   HandleFailure(isBuyStop ? "BUY STOP modify" : "SELL STOP modify");
   return false;
}

bool TrailBuyStop()  { return TrailPending(ORDER_TYPE_BUY_STOP,  g_snap.buyStopTicket,  g_snap.buyStopPrice); }
bool TrailSellStop() { return TrailPending(ORDER_TYPE_SELL_STOP, g_snap.sellStopTicket, g_snap.sellStopPrice); }

bool DeleteOrderSafe(const ulong ticket)
{
   ResetLastError();
   if(trade.OrderDelete(ticket) && IsSuccessRetcode(trade.ResultRetcode()))
   {
      Log(StringFormat("Pending order #%I64u deleted", ticket));
      return true;
   }
   uint rc = trade.ResultRetcode();
   if(rc == TRADE_RETCODE_INVALID_ORDER || rc == TRADE_RETCODE_INVALID)
      return true;   // already gone (triggered or deleted)
   HandleFailure(StringFormat("delete order #%I64u", ticket));
   return false;
}

// Deletes own pending orders. keepBuyStop/keepSellStop keep at most ONE order of that type.
bool CleanupPendings(const bool keepBuyStop, const bool keepSellStop)
{
   bool allOk = true;
   bool keptBuy = false, keptSell = false;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || (ulong)OrderGetInteger(ORDER_MAGIC) != MagicNumber)
         continue;
      ENUM_ORDER_TYPE otype = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(otype == ORDER_TYPE_BUY || otype == ORDER_TYPE_SELL)
         continue;   // market order in flight, cannot be deleted

      if(otype == ORDER_TYPE_BUY_STOP && keepBuyStop && !keptBuy)   { keptBuy = true;  continue; }
      if(otype == ORDER_TYPE_SELL_STOP && keepSellStop && !keptSell) { keptSell = true; continue; }

      if(!DeleteOrderSafe(ticket))
         allOk = false;
   }
   return allOk;
}

bool DeleteAllEAPendingOrders()
{
   return CleanupPendings(false, false);
}

bool ClosePositionSafe(const ulong ticket)
{
   for(int attempt = 0; attempt <= MaxTradeRetries; attempt++)
   {
      if(!PositionSelectByTicket(ticket))
         return true;   // already closed
      ResetLastError();
      if(trade.PositionClose(ticket) && IsSuccessRetcode(trade.ResultRetcode()))
      {
         Log(StringFormat("Position #%I64u closed @ %s", ticket, DoubleToString(trade.ResultPrice(), g_digits)));
         return true;
      }
      uint rc = trade.ResultRetcode();
      if(rc == TRADE_RETCODE_POSITION_CLOSED)
         return true;
      if(!IsRetryableRetcode(rc))
         break;
   }
   HandleFailure(StringFormat("close position #%I64u", ticket));
   return false;
}

bool CloseAllEAPositions()
{
   // 1) Hedged pair of equal volume: CloseBy avoids paying the spread a second time.
   if(g_closeByAllowed)
   {
      ulong buyT = 0, sellT = 0;
      double buyV = 0.0, sellV = 0.0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0)
            continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
            continue;
         if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
         {
            if(buyT == 0) { buyT = ticket; buyV = PositionGetDouble(POSITION_VOLUME); }
         }
         else if(sellT == 0) { sellT = ticket; sellV = PositionGetDouble(POSITION_VOLUME); }
      }
      if(buyT != 0 && sellT != 0 && MathAbs(buyV - sellV) < 1e-8)
      {
         ResetLastError();
         if(trade.PositionCloseBy(buyT, sellT) && IsSuccessRetcode(trade.ResultRetcode()))
            Log(StringFormat("Hedged pair closed by: #%I64u / #%I64u", buyT, sellT));
         else
            HandleFailure("CloseBy");   // falls through to individual closes
      }
   }

   // 2) Everything still open is closed individually.
   bool allOk = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(!ClosePositionSafe(ticket))
         allOk = false;
   }
   return allOk;
}

bool CloseEntireBasket()
{
   bool pendingsOk  = DeleteAllEAPendingOrders();   // first: nothing new can fill while closing
   bool positionsOk = CloseAllEAPositions();
   return (pendingsOk && positionsOk);
}

// Attaches a broker-side SL to any own position that has none (restart, broker dropped SL).
void EnsureStopLoss()
{
   ulong ticket = g_snap.missingSlTicket;
   if(ticket == 0)
      return;
   if(g_lastSlFixMs != 0 && g_nowMs - g_lastSlFixMs < SL_FIX_INTERVAL_MS)
      return;
   g_lastSlFixMs = g_nowMs;
   if(!PositionSelectByTicket(ticket))
      return;

   bool   isBuy = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
   double tp    = PositionGetDouble(POSITION_TP);
   double sl    = CalcStopLossFromEntry(isBuy, PositionGetDouble(POSITION_PRICE_OPEN));
   double minDist = BrokerMinDistance();
   if(isBuy)
      sl = MathMin(sl, g_tick.bid - minDist);
   else
      sl = MathMax(sl, g_tick.ask + minDist);
   sl = NormalizePriceToTickSize(sl);

   ResetLastError();
   if(trade.PositionModify(ticket, sl, tp) && IsSuccessRetcode(trade.ResultRetcode()))
      LogAlways(StringFormat("SL attached to position #%I64u @ %s", ticket, DoubleToString(sl, g_digits)));
   else
      HandleFailure(StringFormat("attach SL #%I64u", ticket));
}

//+------------------------------------------------------------------+
//| Basket lifecycle                                                 |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Entry filters (evaluated only while IDLE, not on every tick      |
//| of an active basket)                                             |
//+------------------------------------------------------------------+
// +1 = fast EMA above slow EMA by at least TrendMinGapPips, -1 = below, 0 = flat or no data.
// Uses the last CLOSED bar (shift 1) so the signal does not repaint inside the bar.
int GetTrendDirection()
{
   double fast[1], slow[1];
   if(CopyBuffer(g_maFastHandle, 0, 1, 1, fast) != 1 || CopyBuffer(g_maSlowHandle, 0, 1, 1, slow) != 1)
      return 0;
   double gap = fast[0] - slow[0];
   double minGap = PipsToPrice(TrendMinGapPips);
   if(gap >= minGap && gap > 0.0)
      return 1;
   if(gap <= -minGap && gap < 0.0)
      return -1;
   return 0;
}

bool IsInSessionWindow()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(SessionStartHour < SessionEndHour)
      return (dt.hour >= SessionStartHour && dt.hour < SessionEndHour);
   return (dt.hour >= SessionStartHour || dt.hour < SessionEndHour);   // window wraps over midnight
}

bool CanStartNewBasket()
{
   if(g_state != STATE_IDLE)
      return false;
   if(g_snap.positions > 0 || g_snap.pendings > 0 || g_snap.marketInFlight > 0)
      return false;
   if(g_nowMs < g_requestCooldownUntilMs)
      return false;
   if(DailyLossLimitReached())
      return false;

   string reason = "";
   if(!IsTradingEnvironmentValid(reason))
   {
      if(!g_envBlocked || reason != g_lastEnvReason)
         Log("New basket blocked: " + reason);
      g_envBlocked    = true;
      g_lastEnvReason = reason;
      return false;
   }
   if(g_envBlocked)
   {
      Log("Trading environment OK again");
      g_envBlocked    = false;
      g_lastEnvReason = "";
   }

   if(UseSessionFilter && !IsInSessionWindow())
   {
      if(!g_sessionBlocked)
         Log("Outside session window - no new baskets");
      g_sessionBlocked = true;
      return false;
   }
   if(g_sessionBlocked)
   {
      Log("Session window open");
      g_sessionBlocked = false;
   }

   if(InitialDirection == INITIAL_TREND)
   {
      g_trendDir = GetTrendDirection();
      if(g_trendDir == 0)
      {
         if(!g_trendBlocked)
            Log("Trend filter: market flat / no data - waiting");
         g_trendBlocked = true;
         return false;
      }
      if(g_trendBlocked)
      {
         Log(StringFormat("Trend filter: %s", g_trendDir > 0 ? "UP" : "DOWN"));
         g_trendBlocked = false;
      }
   }

   if(!IsSpreadAcceptable())
   {
      if(!g_spreadBlocked)
         Log(StringFormat("Spread too high: %.1f pips > %d - waiting", GetCurrentSpreadPips(), MaxSpreadPips));
      g_spreadBlocked = true;
      return false;
   }
   if(g_spreadBlocked)
   {
      Log(StringFormat("Spread OK again: %.1f pips", GetCurrentSpreadPips()));
      g_spreadBlocked = false;
   }
   return true;
}

bool StartNewBasket()
{
   bool isBuy = g_nextIsBuy;
   switch(InitialDirection)
   {
      case INITIAL_BUY:  isBuy = true;  break;
      case INITIAL_SELL: isBuy = false; break;
      case INITIAL_TREND: isBuy = (g_trendDir > 0); break;
      default:           isBuy = g_nextIsBuy; break;
   }

   ENUM_ORDER_TYPE otype = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = isBuy ? g_tick.ask : g_tick.bid;
   if(!HasEnoughMargin(otype, g_volume, price))
   {
      LogAlways("Not enough free margin for a new basket - waiting");
      g_requestCooldownUntilMs = g_nowMs + 5000;
      return false;
   }

   ResetBasketVars();
   g_basketIsBuy         = isBuy;
   g_basketStartMsc      = g_nowMs;
   g_basketStartTime     = TimeCurrent();
   g_basketSpreadAtStart = GetCurrentSpreadPips();

   // State switches BEFORE the request: no second entry can be sent until this one is resolved.
   g_state       = STATE_SETUP;
   g_entrySentMs = g_nowMs;

   bool ok = isBuy ? OpenInitialBuy() : OpenInitialSell();
   if(!ok)
   {
      uint rc = trade.ResultRetcode();
      if(rc == TRADE_RETCODE_TIMEOUT || rc == 0)
      {
         // Outcome unknown: stay in SETUP; the scan will find the position or the setup times out.
         LogAlways("Entry outcome unknown - waiting for account state");
         return false;
      }
      g_state = STATE_IDLE;
      ResetBasketVars();
      return false;
   }

   LogAlways(StringFormat("New basket started: %s %.2f lots, spread %.1f pips",
                          isBuy ? "BUY" : "SELL", g_volume, g_basketSpreadAtStart));

   // The position is normally visible immediately -> arm the counter order on the same tick.
   ScanAccount();
   if(g_snap.positions > 0)
   {
      g_state = STATE_ACTIVE;
      ManagePendingOrders();
   }
   return true;
}

void FinishBasket(const string reason)
{
   RecalcBasketRealized();
   double pnl  = g_basketRealized;
   double hold = (g_basketStartMsc > 0) ? (double)(g_nowMs - g_basketStartMsc) / 1000.0 : 0.0;

   g_statBaskets++;
   if(pnl >= 0.0) { g_statWins++;   g_statGrossWin  += pnl; }
   else           { g_statLosses++; g_statGrossLoss += pnl; }
   if(g_basketWasHedged)
      g_statHedged++;
   g_statSpreadSum += g_basketSpreadAtStart;
   g_statHoldSum   += hold;

   LogAlways(StringFormat("Basket #%d finished (%s): net %+.2f | hold %.1fs | hedged=%s | session net %+.2f",
                          g_statBaskets, reason, pnl, hold, g_basketWasHedged ? "yes" : "no",
                          g_statGrossWin + g_statGrossLoss));

   if(InitialDirection == INITIAL_ALTERNATING)
      g_nextIsBuy = !g_basketIsBuy;

   ResetBasketVars();
   g_dailyDirty = true;
}

void BeginClosing(const string reason)
{
   if(g_state == STATE_CLOSING)
      return;
   g_closeReason        = reason;
   g_state                = STATE_CLOSING;
   g_lastCloseAttemptMs   = 0;
   g_closeRetryIntervalMs = CLOSE_RETRY_INTERVAL_MS;
   LogAlways(StringFormat("Basket closing: %s (basket PnL %+.2f)", reason, GetBasketProfit()));
}

void ProcessClosing()
{
   if(g_lastCloseAttemptMs == 0 || g_nowMs - g_lastCloseAttemptMs >= g_closeRetryIntervalMs)
   {
      g_lastCloseAttemptMs = g_nowMs;
      bool ok = CloseEntireBasket();
      g_closeRetryIntervalMs = ok ? CLOSE_RETRY_INTERVAL_MS : REQUEST_COOLDOWN_MS;
   }
   ScanAccount();
   if(g_snap.positions == 0 && g_snap.pendings == 0 && g_snap.marketInFlight == 0)
   {
      FinishBasket(g_closeReason);
      g_state = STATE_IDLE;
   }
}

void EnterDailyStop()
{
   LogAlways(StringFormat("Daily loss reached: day realized %+.2f, floating %+.2f, limit -%.2f -> DAILY_STOP",
                          g_dailyRealized, FloatingNet(), MaxDailyLossUSD));
   g_state                = STATE_DAILY_STOP;
   g_lastCloseAttemptMs   = 0;
   g_closeRetryIntervalMs = CLOSE_RETRY_INTERVAL_MS;
}

void HandleDailyStop()
{
   if(g_snap.positions == 0 && g_snap.pendings == 0)
      return;
   if(g_lastCloseAttemptMs != 0 && g_nowMs - g_lastCloseAttemptMs < g_closeRetryIntervalMs)
      return;
   g_lastCloseAttemptMs = g_nowMs;
   bool ok = CloseEntireBasket();
   g_closeRetryIntervalMs = ok ? CLOSE_RETRY_INTERVAL_MS : REQUEST_COOLDOWN_MS;
   ScanAccount();
   if(g_snap.positions == 0 && g_snap.pendings == 0 && g_basketStartMsc != 0)
      FinishBasket("daily loss limit");
}

//+------------------------------------------------------------------+
//| State reconstruction (OnInit, recovery, new day)                 |
//+------------------------------------------------------------------+
void RecoverState()
{
   ScanAccount();
   if(g_snap.positions > 0)
   {
      ResetBasketVars();
      g_state = STATE_ACTIVE;
      ScanAccount();   // now collects position ids into the basket set

      bool hedged        = (g_snap.buyPositions > 0 && g_snap.sellPositions > 0);
      g_basketStartMsc   = g_snap.earliestOpenMsc;
      g_basketStartTime  = (datetime)(g_snap.earliestOpenMsc / 1000);
      g_basketIsBuy      = (g_snap.earliestType == (int)POSITION_TYPE_BUY);
      g_counterTriggered = hedged || g_snap.positions >= MaxPositionsPerBasket;
      g_basketWasHedged  = hedged;
      g_hedgeLockMs      = hedged ? g_snap.latestOpenMsc : 0;
      RecalcBasketRealized();

      LogAlways(StringFormat("State recovered: ACTIVE | positions=%d (B%d/S%d) pendings=%d counterTriggered=%s",
                             g_snap.positions, g_snap.buyPositions, g_snap.sellPositions,
                             g_snap.pendings, g_counterTriggered ? "yes" : "no"));
      return;
   }

   if(g_snap.pendings > 0)
   {
      g_state = STATE_ERROR_RECOVERY;
      LogAlways(StringFormat("Inconsistent state: %d pending order(s) without position -> removing", g_snap.pendings));
      return;
   }

   if(g_state != STATE_IDLE)
      Log("State recovered: IDLE");
   g_state = STATE_IDLE;
   ResetBasketVars();
}

void HandleErrorRecovery()
{
   if(g_lastRecoveryMs != 0 && g_nowMs - g_lastRecoveryMs < RECOVERY_INTERVAL_MS)
      return;
   g_lastRecoveryMs = g_nowMs;
   if(g_snap.positions == 0 && g_snap.pendings > 0)
      DeleteAllEAPendingOrders();
   RecoverState();
}

// Synchronizes the internal state with the scanned account state.
void UpdateEAState()
{
   switch(g_state)
   {
      case STATE_IDLE:
         if(g_snap.positions > 0 || g_snap.pendings > 0)
         {
            LogAlways("Own orders found while IDLE -> re-reading account state");
            RecoverState();
         }
         break;

      case STATE_SETUP:
         if(g_snap.positions > 0)
         {
            g_state = STATE_ACTIVE;
            Log("Entry confirmed - basket ACTIVE");
         }
         else if(g_snap.pendings > 0)
            g_state = STATE_ERROR_RECOVERY;
         else if(g_snap.marketInFlight == 0 && g_nowMs - g_entrySentMs > SETUP_TIMEOUT_MS)
         {
            LogAlways("Setup timeout: no position appeared -> re-reading account state");
            RecoverState();
         }
         break;

      case STATE_ACTIVE:
         if(g_snap.positions == 0)
         {
            if(g_snap.pendings > 0)
            {
               // position closed by SL or externally -> orphan counter order must go
               if(g_lastOrphanDeleteMs == 0 || g_nowMs - g_lastOrphanDeleteMs >= PENDING_RETRY_INTERVAL_MS)
               {
                  g_lastOrphanDeleteMs = g_nowMs;
                  Log("Basket positions gone (SL/external) - deleting orphan pending order");
                  DeleteAllEAPendingOrders();
               }
               break;
            }
            if(g_snap.marketInFlight > 0)
               break;
            FinishBasket("positions closed by SL / externally");
            g_state = STATE_IDLE;
            break;
         }

         if(g_snap.buyPositions > 0 && g_snap.sellPositions > 0)
         {
            if(!g_counterTriggered)
               LogAlways(StringFormat("Pending order triggered - basket hedged (locked PnL %+.2f)", GetBasketProfit()));
            g_counterTriggered = true;
            g_basketWasHedged  = true;
            if(g_hedgeLockMs == 0)
               g_hedgeLockMs = g_nowMs;
         }
         else
            g_hedgeLockMs = 0;

         if(g_snap.positions >= MaxPositionsPerBasket)
            g_counterTriggered = true;
         break;

      default:
         break;
   }
}

void ManagePendingOrders()
{
   if(g_snap.positions == 0)
      return;

   bool hedged       = (g_snap.buyPositions > 0 && g_snap.sellPositions > 0);
   bool armAllowed   = !g_counterTriggered && g_snap.positions < MaxPositionsPerBasket;
   bool needBuyStop  = armAllowed && !hedged && g_snap.sellPositions > 0;
   bool needSellStop = armAllowed && !hedged && g_snap.buyPositions > 0;

   // Anything that should not exist: wrong side, duplicates, foreign types, no longer allowed.
   bool dirty = (g_snap.otherPendings > 0) ||
                (g_snap.buyStops  > (needBuyStop  ? 1 : 0)) ||
                (g_snap.sellStops > (needSellStop ? 1 : 0));
   if(dirty)
   {
      if(g_lastPendingAttemptMs == 0 || g_nowMs - g_lastPendingAttemptMs >= PENDING_RETRY_INTERVAL_MS)
      {
         g_lastPendingAttemptMs = g_nowMs;
         Log("Removing surplus/invalid pending orders");
         CleanupPendings(needBuyStop, needSellStop);
      }
      return;
   }

   if(needBuyStop)
   {
      if(g_snap.buyStops == 0)
         PlaceBuyStop();
      else
         TrailBuyStop();
   }
   if(needSellStop)
   {
      if(g_snap.sellStops == 0)
         PlaceSellStop();
      else
         TrailSellStop();
   }
}

//+------------------------------------------------------------------+
//| Status panel                                                     |
//+------------------------------------------------------------------+
void UpdateStatusPanel()
{
   if(!ShowStatusPanel)
      return;
   if(g_lastPanelMs != 0 && g_nowMs - g_lastPanelMs < PANEL_INTERVAL_MS)
      return;
   g_lastPanelMs = g_nowMs;

   string dir;
   if(g_snap.positions > 0)
      dir = g_basketIsBuy ? "BUY (current)" : "SELL (current)";
   else if(InitialDirection == INITIAL_BUY)
      dir = "BUY";
   else if(InitialDirection == INITIAL_SELL)
      dir = "SELL";
   else if(InitialDirection == INITIAL_TREND)
      dir = (g_trendDir > 0) ? "TREND: UP" : (g_trendDir < 0) ? "TREND: DOWN" : "TREND: FLAT (waiting)";
   else
      dir = g_nextIsBuy ? "BUY (next, alternating)" : "SELL (next, alternating)";

   string tradingTxt = (g_tradingBlocked || g_state == STATE_DAILY_STOP) ? "DISABLED" : "ENABLED";

   Comment(StringFormat(
      "Dynamic Straddle EA\n"
      "State: %s\n"
      "Symbol: %s\n"
      "Spread: %.1f pips (%d pts)\n"
      "Positions: %d (B%d / S%d)\n"
      "Pending Orders: %d\n"
      "Basket PnL: %+.2f USD\n"
      "Target: %.2f USD | Emergency: -%.2f USD\n"
      "Daily PnL: %+.2f USD\n"
      "Daily Limit: -%.2f USD\n"
      "Initial Direction: %s\n"
      "Baskets: %d (W %d / L %d) | Net %+.2f\n"
      "Trading: %s",
      StateToString(g_state), _Symbol,
      GetCurrentSpreadPips(), (int)MathRound((g_tick.ask - g_tick.bid) / g_point),
      g_snap.positions, g_snap.buyPositions, g_snap.sellPositions,
      g_snap.pendings,
      GetBasketProfit(),
      TargetProfitUSD, EmergencyBasketLossUSD,
      GetDailyPnL(),
      MaxDailyLossUSD,
      dir,
      g_statBaskets, g_statWins, g_statLosses, g_statGrossWin + g_statGrossLoss,
      tradingTxt));
}

void PrintStats()
{
   if(g_statBaskets == 0)
   {
      LogAlways("STATS: no completed baskets");
      return;
   }
   double net    = g_statGrossWin + g_statGrossLoss;
   double pf     = (g_statGrossLoss < 0.0) ? g_statGrossWin / -g_statGrossLoss : 0.0;
   double avgWin = (g_statWins > 0)   ? g_statGrossWin / g_statWins : 0.0;
   double avgLos = (g_statLosses > 0) ? g_statGrossLoss / g_statLosses : 0.0;
   LogAlways(StringFormat(
      "STATS baskets=%d wins=%d losses=%d winrate=%.1f%% net=%+.2f avgWin=%+.2f avgLoss=%+.2f PF=%.2f "
      "expectancy=%+.3f avgHold=%.1fs avgSpread=%.2fpips hedgedBaskets=%d",
      g_statBaskets, g_statWins, g_statLosses, 100.0 * g_statWins / g_statBaskets,
      net, avgWin, avgLos, pf, net / g_statBaskets,
      g_statHoldSum / g_statBaskets, g_statSpreadSum / g_statBaskets, g_statHedged));
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

   if(LotSize <= 0.0 || DistancePips <= 0 || StopLossPips <= 0 || TargetProfitUSD <= 0.0 ||
      MaxPositionsPerBasket < 1 || MaxTradeRetries < 0 || MinModifyIntervalMs < 0 || MinModifyStepPoints < 0)
   {
      LogAlways("Invalid input parameters");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InitialDirection == INITIAL_TREND)
   {
      if(TrendFastEMA <= 0 || TrendSlowEMA <= TrendFastEMA || TrendMinGapPips < 0.0)
      {
         LogAlways("Invalid trend filter inputs (need 0 < TrendFastEMA < TrendSlowEMA)");
         return INIT_PARAMETERS_INCORRECT;
      }
      g_maFastHandle = iMA(_Symbol, TrendTimeframe, TrendFastEMA, 0, MODE_EMA, PRICE_CLOSE);
      g_maSlowHandle = iMA(_Symbol, TrendTimeframe, TrendSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
      if(g_maFastHandle == INVALID_HANDLE || g_maSlowHandle == INVALID_HANDLE)
      {
         LogAlways(StringFormat("Cannot create EMA handles, error %d", GetLastError()));
         return INIT_FAILED;
      }
   }
   if(UseSessionFilter && (SessionStartHour < 0 || SessionStartHour > 23 || SessionEndHour < 0 ||
                           SessionEndHour > 24 || SessionStartHour == SessionEndHour))
   {
      LogAlways("Invalid session hours");
      return INIT_PARAMETERS_INCORRECT;
   }

   g_volume = NormalizeVolume(LotSize);
   if(MathAbs(g_volume - LotSize) > 1e-8)
      LogAlways(StringFormat("LotSize %.4f adjusted to broker volume rules: %.4f", LotSize, g_volume));

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints((ulong)MaxDeviationPoints);
   trade.SetTypeFilling(DetermineFilling());
   trade.SetMarginMode();
   trade.SetAsyncMode(false);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   int expModes = (int)SymbolInfoInteger(_Symbol, SYMBOL_EXPIRATION_MODE);
   g_orderTime = ((expModes & SYMBOL_EXPIRATION_GTC) == SYMBOL_EXPIRATION_GTC) ? ORDER_TIME_GTC : ORDER_TIME_DAY;

   int orderModes = (int)SymbolInfoInteger(_Symbol, SYMBOL_ORDER_MODE);
   if((orderModes & SYMBOL_ORDER_STOP) != SYMBOL_ORDER_STOP)
      LogAlways("WARNING: broker does not allow STOP orders on this symbol - strategy cannot work");

   g_tradingBlocked = false;
   if(!IsHedgingAccount())
   {
      if(AllowNettingMode)
         LogAlways("WARNING: netting account - counter order will NET the position instead of hedging it");
      else
      {
         LogAlways("Netting account detected: this strategy needs HEDGING. New trades disabled (AllowNettingMode=false)");
         g_tradingBlocked = true;
      }
   }
   g_closeByAllowed = IsHedgingAccount() && ((orderModes & SYMBOL_ORDER_CLOSEBY) == SYMBOL_ORDER_CLOSEBY);

   string sym = _Symbol;
   StringToUpper(sym);
   if(StringFind(sym, "XAU") < 0 && StringFind(sym, "GOLD") < 0)
      LogAlways("WARNING: EA is designed for XAUUSD - check pip settings for " + _Symbol);

   // Translate the pip inputs into money so the configuration can be sanity-checked in the log.
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double moneyPerPriceUnit = (g_tickSize > 0.0) ? tickValue / g_tickSize * g_volume : 0.0;
   LogAlways(StringFormat("Symbol %s digits=%d point=%s tickSize=%s pip=%s | lots=%.2f -> 1.00 price = %.2f %s",
                          _Symbol, g_digits, DoubleToString(g_point, g_digits), DoubleToString(g_tickSize, g_digits),
                          DoubleToString(g_pipSize, g_digits), g_volume, moneyPerPriceUnit,
                          AccountInfoString(ACCOUNT_CURRENCY)));
   LogAlways(StringFormat("Distance %d pips = %.2f price (~%.2f), SL %d pips = %.2f price (~%.2f), MaxSpread %d pips = %.2f price",
                          DistancePips, PipsToPrice(DistancePips), PipsToPrice(DistancePips) * moneyPerPriceUnit,
                          StopLossPips, PipsToPrice(StopLossPips), PipsToPrice(StopLossPips) * moneyPerPriceUnit,
                          MaxSpreadPips, PipsToPrice(MaxSpreadPips)));
   LogAlways(StringFormat("Broker: stopsLevel=%d pts freezeLevel=%d pts | filling=%s | closeBy=%s | hedging=%s",
                          (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL),
                          (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL),
                          EnumToString(DetermineFilling()), g_closeByAllowed ? "yes" : "no",
                          IsHedgingAccount() ? "yes" : "no"));

   g_nextIsBuy = (InitialDirection != INITIAL_SELL);
   if(SymbolInfoTick(_Symbol, g_tick))
      g_nowMs = (long)g_tick.time_msc;

   CheckNewDay();
   RecalcDailyPnL();
   RecoverState();

   LogAlways(StringFormat("EA initialized | state=%s | magic=%I64u", StateToString(g_state), MagicNumber));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(DeletePendingOrdersOnDeinit)
      DeleteAllEAPendingOrders();
   if(ClosePositionsOnDeinit)
      CloseAllEAPositions();
   PrintStats();
   if(g_maFastHandle != INVALID_HANDLE) { IndicatorRelease(g_maFastHandle); g_maFastHandle = INVALID_HANDLE; }
   if(g_maSlowHandle != INVALID_HANDLE) { IndicatorRelease(g_maSlowHandle); g_maSlowHandle = INVALID_HANDLE; }
   Comment("");
   LogAlways(StringFormat("EA deinitialized (reason %d)", reason));
}

// Optimizer criterion ("Custom max"): expectancy per basket scaled by sqrt(n) (t-stat-like),
// so a few lucky baskets cannot win the optimization.
double OnTester()
{
   if(g_statBaskets < MinTestBaskets)
      return 0.0;
   double net = g_statGrossWin + g_statGrossLoss;
   return (net / g_statBaskets) * MathSqrt((double)g_statBaskets);
}

void OnTick()
{
   if(!SymbolInfoTick(_Symbol, g_tick))
      return;
   if(g_tick.bid <= 0.0 || g_tick.ask <= 0.0)
      return;
   g_nowMs = (long)g_tick.time_msc;

   CheckNewDay();
   if(g_dailyDirty)
      RecalcDailyPnL();
   if(g_basketRealizedDirty)
      RecalcBasketRealized();
   ScanAccount();

   // 1) Emergency / daily risk
   if(g_state != STATE_DAILY_STOP && DailyLossLimitReached())
      EnterDailyStop();
   if(g_state == STATE_DAILY_STOP)
   {
      HandleDailyStop();
      UpdateStatusPanel();
      return;
   }

   if(g_state != STATE_CLOSING && g_snap.positions > 0)
   {
      EnsureStopLoss();
      double basket = GetBasketProfit();
      if(EmergencyLossReached(basket))
      {
         LogAlways(StringFormat("Emergency loss reached: %+.2f <= -%.2f", basket, EmergencyBasketLossUSD));
         BeginClosing("emergency basket loss");
      }
      // 2) Basket profit target
      else if(BasketTargetReached(basket))
      {
         Log(StringFormat("Basket target reached: %+.2f >= %.2f", basket, TargetProfitUSD));
         BeginClosing("target reached");
      }
      else if(HedgeLockTimedOut())
         BeginClosing("hedge lock timeout");
   }

   // 3) Closing process
   if(g_state == STATE_CLOSING)
   {
      ProcessClosing();
      UpdateStatusPanel();
      return;
   }

   if(g_state == STATE_ERROR_RECOVERY)
   {
      HandleErrorRecovery();
      UpdateStatusPanel();
      return;
   }

   // 4) State synchronisation
   UpdateEAState();

   // 5) Pending order management
   if(g_state == STATE_ACTIVE)
      ManagePendingOrders();

   // 6) New setup
   if(g_state == STATE_IDLE && CanStartNewBasket())
      StartNewBasket();

   // 7) UI
   UpdateStatusPanel();
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   // Order add/update/delete are picked up by the per-tick account scan; deals drive accounting.
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;
   if(trans.symbol != _Symbol)
      return;

   ulong deal = trans.deal;
   if(deal == 0 || !HistoryDealSelect(deal))
   {
      g_dailyDirty          = true;
      g_basketRealizedDirty = true;
      return;
   }

   ulong posId = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   bool  ours  = ((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) == MagicNumber) || InBasket(posId);
   if(!ours)
      return;

   g_dailyDirty          = true;
   g_basketRealizedDirty = true;

   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
   if(entry == DEAL_ENTRY_IN)
   {
      if(g_state == STATE_SETUP || g_state == STATE_ACTIVE || g_state == STATE_CLOSING)
         AddBasketPosId(posId);
      ulong order = (ulong)HistoryDealGetInteger(deal, DEAL_ORDER);
      if(order != 0 && (order == g_snap.buyStopTicket || order == g_snap.sellStopTicket))
         LogAlways(StringFormat("Pending order triggered: order #%I64u filled @ %s",
                                order, DoubleToString(HistoryDealGetDouble(deal, DEAL_PRICE), g_digits)));
   }
   else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
   {
      ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(deal, DEAL_REASON);
      if(reason == DEAL_REASON_SL)
         LogAlways(StringFormat("Hard stop loss hit: position %I64u, net %+.2f", posId, DealNet(deal)));
      else
         Log(StringFormat("Exit deal #%I64u: position %I64u, net %+.2f", deal, posId, DealNet(deal)));
   }
}
//+------------------------------------------------------------------+
