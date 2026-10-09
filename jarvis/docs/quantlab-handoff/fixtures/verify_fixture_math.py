"""Independent arithmetic checks for synthetic fixture reference numbers; no trading inference."""
import json
from pathlib import Path

obj = json.loads((Path(__file__).parent / 'golden_expected.json').read_text(encoding='utf8'))
fees = obj['input']['fee_usd_per_order']
entry, exit_ = obj['execution']
initial_cash = obj['input']['initial_cash_usd']
assert entry['fill_bar_start_utc'] > entry['signal_bar_start_utc']
assert exit_['fill_bar_start_utc'] > exit_['signal_bar_start_utc']
assert entry['fill_bar_start_utc'] >= entry['signal_available_at_utc']
assert exit_['fill_bar_start_utc'] >= exit_['signal_available_at_utc']
assert entry['cash_after_usd'] == initial_cash - entry['fill_price_usd'] - fees
assert exit_['cash_after_usd'] == entry['cash_after_usd'] + exit_['fill_price_usd'] - fees
assert obj['round_trip']['gross_pnl_usd'] == exit_['fill_price_usd'] - entry['fill_price_usd']
assert obj['round_trip']['net_pnl_usd'] == obj['round_trip']['gross_pnl_usd'] - 2*fees
assert obj['end']['equity_usd'] == obj['end']['cash_usd'] == initial_cash

# Independent SMA(2)/SMA(3) arithmetic, not calling a QuantLab engine.
closes = [10,10,10,12,12,12,10,9,9,12,12,9]
fast = [None if i<1 else sum(closes[i-1:i+1])/2 for i in range(len(closes))]
slow = [None if i<2 else sum(closes[i-2:i+1])/3 for i in range(len(closes))]
buys=[]; sells=[]
for i in range(3,len(closes)):
    if fast[i-1] <= slow[i-1] and fast[i] > slow[i]: buys.append(i)
    if fast[i-1] >= slow[i-1] and fast[i] < slow[i]: sells.append(i)
assert buys == obj['ma_fixture']['buy_signal_bar_indices'], (buys, obj['ma_fixture']['buy_signal_bar_indices'])
assert sells == obj['ma_fixture']['sell_signal_bar_indices'], (sells, obj['ma_fixture']['sell_signal_bar_indices'])
# one round-trip 12 -> 9, then new BUY 12, mark at 9; 3 filled orders each fee 0.5
cash = 100 - (12+0.5) + (9-0.5) - (12+0.5)
final_equity = cash + 9
assert final_equity == obj['ma_fixture']['final_equity_usd'], final_equity
print('SYNTHETIC golden fixture arithmetic: PASS')
