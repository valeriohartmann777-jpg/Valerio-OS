# FUTURES_ROLL_METHOD — ES and NQ from Databento trades

Written 2026-10-09, before any Databento data was requested. The roll rule itself was
frozen on 2026-10-07 in `configs/instruments.yaml`, before any data existed; this
document describes how it is applied to Databento's parent-symbol data. Nothing here
re-chooses it. Code: `edgelab/data/contracts.py`, `edgelab/data/trades.py`.

## The frozen rule

`configs/instruments.yaml` (ES and NQ):

```yaml
roll:
  months: [3, 6, 9, 12]
  expiry: third_friday
  roll_offset_days: 8
```

Quarterly contracts (H, M, U, Z) expire on the third Friday of the expiry month. The
roll date is **8 calendar days before expiry** (the Thursday of the week before).

## Which instruments count

A parent request (`ES.FUT`, `NQ.FUT`) returns every instrument of the product. An
instrument is used only if its definition (schema `definition`, same period) shows:

* `instrument_class = F` (outright future), `security_type = FUT`, `asset` = the root;
* not user-defined;
* raw symbol `<root><month code><year digit(s)>`, month code in H/M/U/Z;
* an expiration, and a tick size equal to `instruments.yaml` (0.25).

Calendar spreads (`S`), options, user-defined and other instruments are counted per
reason (`contracts.csv`, `validation.json`) and never enter bars or profiles. Trades of an
instrument id without a definition are counted and not used.

## The active contract of a trading date

A CME trading date `d` starts at 18:00 New York time on the previous evening. On `d`,
the eligible outrights are those **listed before the session of `d` starts** (definition
`activation`) whose **roll date lies after `d`**. The eligible contract with the nearest
expiry is active. From the first trading date on or after the roll date, the next
contract is active.

Example (2024): ESH4 expires Friday 2024-03-15, roll date Thursday 2024-03-07. Trading
date 2024-03-06 is the last ESH4 session; trading date 2024-03-07 (from Wednesday
2024-03-06 18:00 ET) is the first ESM4 session.

**Causality.** The choice uses only the calendar and contract attributes known from the
listing (expiry, activation); no price or volume of any date. A later trade can therefore
never change the contract chosen for an earlier date (tested by perturbing the future).
Volume is used only for diagnostics: the active contract's share of outright volume, whether
it was the previous session's volume leader, and the first date the new contract out-traded
the old one (`liquidity_daily.csv`, `liquidity_rolls.csv`, summarised in
`reports/TRADE_DATA_QUALITY_REPORT.md`). These use the whole sample and are reported,
never used to choose a contract.

Databento's continuous symbols are not used: `.c.` rolls by calendar at expiry and `.v.`
/ `.n.` by the previous day's volume or open interest; none is the frozen rule.

## Prices: unadjusted, one contract at a time

Bars are the traded prices of the active contract; nothing is back-adjusted. Each
session's volume profile, POC/VAH/VAL, developing values and VWAP come from the trades of
one contract. A composite of several sessions only covers consecutive completed sessions
of one contract; a window that would cross a roll stays empty.

## How the studies treat a roll (unchanged)

The bar file has a `contract` column. The runner (`edgelab/studies/runner.py:
roll_exclusions`, protocol 13.3) excludes the trading date of each contract change, and an
event day also needs a non-excluded previous day, so neither the first session on the new
contract nor the one after it is an event day.

Multi-session features of the frozen studies (ATR_d, prior-week levels, 20-session volume
baselines, pivot zones) can still straddle a roll in an unadjusted series. The gap between
the two contracts at every roll is measured in ATR_d(14) units and reported; changing a
feature definition because of it would change a pre-registered study and needs a separate,
dated decision.

## Time

Bars and profiles use `ts_event`, the CME matching-engine time, in UTC; each bar is labelled
by the UTC minute it opens. Conversion to New York time goes through the tz database only
(no fixed offsets); the build checks that sessions open at 18:00 New York time in both EST
and EDT. Holiday sessions keep the wall-clock trade date of `configs/sessions.yaml`; dates
without a full RTH session are flagged and are not event days.
