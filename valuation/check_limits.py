"""Open limit orders vs the day's prices (Kilian 2026-10-05: "you can check this yourself"). Weekday step 0b in REFRESH.md.

For each review_settings.json limit_orders entry (GTC buy limits), reads valuation/data/daily_<SYM>.csv rows after the
placed date. The first day whose low is at or below the limit counts as a fill at min(limit, that day's open): a buy limit
fills at the limit or better, and a gap below it fills near the open. The fill is appended to data/trades.json with
"assumed": true and the $6 fee, and the entry leaves limit_orders. Kilian confirms or corrects the price from his broker;
edit the trade then. Usage: python3 check_limits.py [--dry]
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
dry = '--dry' in sys.argv
cfg_p, tr_p = f'{HERE}/data/review_settings.json', f'{HERE}/data/trades.json'
cfg, tr = json.load(open(cfg_p)), json.load(open(tr_p))
fee = cfg.get('fee_per_trade', 6.0)
filled = []
for sym, lo in list((cfg.get('limit_orders') or {}).items()):
    f = f'{HERE}/data/daily_{sym}.csv'
    if not os.path.exists(f): print(f'{sym}: no daily_{sym}.csv, cannot check'); continue
    rows = [r for r in csv.DictReader(open(f)) if r['date'] > lo.get('placed', '')]
    hit = next((r for r in rows if float(r['low']) <= lo['limit']), None)
    last = rows[-1] if rows else None
    if not hit:
        print(f"{sym}: limit {lo['limit']} not touched" + (f" (low since placed {min(float(r['low']) for r in rows):.2f}, last {last['date']})" if rows else ' (no rows after placed date yet)'))
        continue
    px = round(min(lo['limit'], float(hit['open'])), 2)
    t = dict(date=hit['date'], symbol=sym, side='buy', shares=lo['shares'], price=px, limit=lo['limit'], fees=fee, assumed=True,
             note=f"Assumed fill: daily low {float(hit['low']):.2f} touched the {lo['limit']} GTC limit; confirm with the broker")
    filled.append(t); print(f"{sym}: FILLED (assumed) {lo['shares']} @ {px} on {hit['date']}")
    if not dry:
        tr['trades'].append(t); del cfg['limit_orders'][sym]
if filled and not dry:
    json.dump(tr, open(tr_p, 'w'), indent=1); json.dump(cfg, open(cfg_p, 'w'), indent=1)
