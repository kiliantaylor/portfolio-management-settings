"""EV/gross profit leg (Kilian card 2026-11-16 'B'): python3 test_ev_gross_profit.py. Synthetic gross margins on copies of
two filed sets (BE 2026-10-23 annual, AMBQ 2026-11-10 quarterly); nothing is written. The Sheet tie (Model, Calc, Quarters,
downside / upside) is checked with model_gsheet.py verify on such sets (thread 'Add EV/gross profit to model engine')."""
import copy, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import model_engine as ME
def load(d, s):
    sup = ME.load(f'{HERE}/runs/{d}/{s}/supervisor.json'); return ME.extract(sup), sup.get('as_of', d)
def leg(r, k='EV/gross profit'): return next(v for v in r['legs'].values() if v.get('metric') == k)
# 1. annual: the leg = per-share bridge of multiple x NTM gross profit; same bridge as EV/sales on the same EV
mi, a = load('2026-10-23', 'BE'); mi = copy.deepcopy(mi)
hm = next(h for h in mi['horizon_multiples'] if h['metric'] == 'EV/sales')
try: x = copy.deepcopy(mi); next(h for h in x['horizon_multiples'] if h['metric'] == 'EV/sales')['metric'] = 'EV/gross profit'; ME.value(x, a); raise SystemExit('FAIL: no refusal without a gross margin')
except ME.ModelError as e: assert 'gross_margin' in str(e)
mi['years'][0]['gross_margin'] = 0.5  # inherited by later years: GP = revenue / 2
sales = ME.value(mi, a); hm['metric'] = 'EV/gross profit'; hm['value'] *= 2  # 2x on half the metric = the same EV
gp = ME.value(mi, a)
assert abs(leg(gp)['value'] - leg(sales, 'EV/sales')['value']) < 1e-9, (leg(gp), leg(sales, 'EV/sales'))
assert all(abs(r['gross_profit'] - r['revenue'] / 2) < 0.01 for r in gp['rows'])  # output rows are rounded to 0.01
# 2. deltas: gross_margin moves only the EV/GP leg; ebit_margin leaves it; multiple takes full turns
g1 = ME.value(mi, a, deltas={'gross_margin': -0.05}); assert leg(g1)['value'] < leg(gp)['value']
assert all(abs(v['value'] - gp['legs'][k]['value']) < 1e-9 for k, v in g1['legs'].items() if v.get('metric') != 'EV/gross profit')
g2 = ME.value(mi, a, deltas={'ebit_margin': -0.05}); assert [r['gross_profit'] for r in g2['rows']] == [r['gross_profit'] for r in gp['rows']]  # (the leg still moves via net debt)
g3 = ME.value(mi, a, deltas={'multiple': -1.0}); assert abs(leg(g3)['multiple'] - (hm['value'] - 1.0)) < 1e-12
# 3. quarterly: driver_build.quarters.gross_profit sets each year's gross margin from the quarter sums; quarter rows carry it
mq, aq = load('2026-11-10', 'AMBQ'); mq = copy.deepcopy(mq); qb = mq['driver_build']['quarters']
qb['gross_profit'] = 'rev * (0.55 + 0.01 * fy)'
for r_ in qb['reported'].values(): r_['gross_profit'] = r_['revenue'] * 0.52
mq['horizon_multiples'][0].update(metric='EV/gross profit', value=9.0)
q = ME.value(mq, aq); Q = q['quarters']
for r_ in q['rows']:
    s_ = sum(x['gross_profit'] for x in Q if x['fy'] == r_['label']); assert abs(s_ - r_['gross_profit']) < 0.05, (r_['label'], s_, r_['gross_profit'])
assert all(abs(x['gross_profit'] - x['revenue'] * (0.52 if x['actual'] else 0.55 + 0.01 * (int(x['label'][-4:]) - 2025))) < 0.01 for x in Q), 'quarter gross profit'
print('OK: EV/gross profit leg (annual bridge, refusal, deltas, quarterly build)')
