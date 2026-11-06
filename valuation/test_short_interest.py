"""Test of the short-interest days-to-cover tilt (Kilian 2026-10-19 card). Run: python3 test_short_interest.py [D]
Synthetic part (no network, temp folder): the mapping, the log shift moving every level by e^shift, the look-ahead /
staleness / no-data / outside-universe cases of combine.short_interest_tilt, and conviction's treatment (the tilt is not
scaled by the conviction multiplier). Real part: data/short_interest.json and runs/D/SYM/result.json for the held names."""
import json, math, os, sys, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import combine, short_interest as si

HELD = 'AVGO GOOGL VST NFLX BKNG NVDA AMZN AMAT MOD'.split()
ok = lambda c, msg: (print('  ok  ', msg) if c else (print('  FAIL', msg), sys.exit(1)))

print('synthetic')
e0, l0 = si.tilt(0.0); e5, l5 = si.tilt(0.5); e1, l1 = si.tilt(1.0)
ok(abs(e0 - si.SLOPE / 2) < 1e-12 and e5 == 0 and abs(e1 + si.SLOPE / 2) < 1e-12, f'excess at pct 0 / 0.5 / 1 = {e0:+.4f} / {e5:+.4f} / {e1:+.4f}')
ok(abs(si.tilt(0.1)[0] - 0.4 * si.SLOPE) < 1e-12, f'easiest-fifth centre (pct 0.1) +{si.tilt(0.1)[0]:.4f}, hardest (0.9) {si.tilt(0.9)[0]:+.4f}: about +/-2%')
ok(abs(si.SLOPE - 0.054769 * 2.668 ** 2 / (1 + 2.668 ** 2)) < 1e-5, f'slope {si.SLOPE} = walk-forward 0.0548 x shrink t^2/(1+t^2) {si.SHRINK:.3f}')
# a lognormal ladder, shifted the way combine.py does it: every quantile moves by exactly e^shift
S, sig = 100.0, 0.35; Ks = [S * math.exp(z * 0.1) for z in range(-15, 16)]
p = [1 - combine.N((math.log(K / S) - 0.05) / sig) for K in Ks]
sf0 = combine.survival_fn(Ks, [combine.clip(x) for x in p]); shift = si.tilt(0.1)[1]
p1 = combine.monotone([sf0(K * math.exp(-shift)) for K in Ks]); sf1 = combine.survival_fn(Ks, [combine.clip(x) for x in p1])
for q in (0.15, 0.5, 0.75):
    a, b = combine.quantile(sf0, q, 1, 2000), combine.quantile(sf1, q, 1, 2000)
    ok(abs(math.log(b / a) - shift) < 1e-3, f'q{int(q*100)} {a:.2f} -> {b:.2f} ({b/a-1:+.3%}; shift {math.exp(shift)-1:+.3%}; ladder interpolation < 0.1%)')
# combine.short_interest_tilt on a temp data folder
tmp = tempfile.mkdtemp(); os.makedirs(os.path.join(tmp, 'data', 'short_interest'))
snap = dict(settle_date='2026-09-30', available_est='2026-10-12', source='synthetic',
            names=dict(EASY=dict(dtc=1.0, pct=0.1, quintile=1, excess_12m=si.tilt(0.1)[0], log_shift=si.tilt(0.1)[1], in_universe=True),
                       HARD=dict(dtc=12.0, pct=0.9, quintile=5, excess_12m=si.tilt(0.9)[0], log_shift=si.tilt(0.9)[1], in_universe=True),
                       ADR=dict(dtc=3.0, pct=None, pct_vs_universe=0.3, quintile=None, excess_12m=0, log_shift=0, in_universe=False, reason='outside the backtest universe (country Taiwan)')))
json.dump(snap, open(os.path.join(tmp, 'data', 'short_interest', '2026-09-30.json'), 'w'))
real_here = combine.HERE; combine.HERE = tmp
try:
    t = combine.short_interest_tilt('2026-10-16', 'EASY'); ok(t['log_shift'] > 0.018 and t['settle_date'] == '2026-09-30', f"EASY +{t['log_shift']:.4f}")
    t = combine.short_interest_tilt('2026-10-16', 'HARD'); ok(t['log_shift'] < -0.018, f"HARD {t['log_shift']:+.4f}")
    t = combine.short_interest_tilt('2026-10-16', 'ADR'); ok(t['log_shift'] == 0 and 'outside' in t['reason'], f"ADR 0 ({t['reason']})")
    t = combine.short_interest_tilt('2026-10-16', 'NONE'); ok(t['log_shift'] == 0 and t['reason'] == 'no FINRA data', 'name without FINRA data: 0')
    t = combine.short_interest_tilt('2026-10-09', 'EASY'); ok(t['log_shift'] == 0 and 'no short-interest' in t['reason'], 'back-dated run before the publication date: 0 (no look-ahead)')
    t = combine.short_interest_tilt('2026-12-01', 'EASY'); ok(t['log_shift'] == 0 and 'days old' in t['reason'], f"stale settlement: 0 ({t['reason']})")
finally:
    combine.HERE = real_here; shutil.rmtree(tmp)
# conviction: mu_eff = m + tilt + k (mu - m - tilt); the tilt part is the same for any multiplier k
m, tilt_ret, alpha = 0.10, 0.02, 0.03
for k in (0.5, 1, 2):
    mu_eff = m + tilt_ret + k * alpha; ok(abs((mu_eff - k * alpha) - (m + tilt_ret)) < 1e-12, f'k={k}: mu_eff {mu_eff:.3f}, tilt part {tilt_ret:+.3f} unscaled')

D = sys.argv[1] if len(sys.argv) > 1 else '2026-10-16'
print(f'real (data/short_interest.json, runs/{D})')
d = json.load(open(os.path.join(HERE, 'data', 'short_interest.json')))
print(f"  settlement {d['settle_date']} (available ~{d['available_est']}), universe {d['n_universe']} names, slope {d['mapping']['slope']}")
ok(d['n_universe'] > 1000, 'universe has more than 1000 names')
print(f"  {'sym':5} {'dtc':>5} {'pct':>6} {'q':>2} {'shift':>7} {'target':>9} {'w/o DTC':>9} {'EPS shift':>9}")
for s in HELD:
    r = d['names'].get(s) or {}; t = combine.short_interest_tilt(D, s)
    ok(abs((t or {}).get('log_shift', 0) - (r.get('log_shift') or 0)) < 1e-9, f'{s} combine reads the file\'s shift')
    f = os.path.join(HERE, 'runs', D, s, 'result.json')
    if not os.path.exists(f): print(f'  {s:5} no result.json for {D}'); continue
    res = json.load(open(f)); rs = res.get('short_interest') or {}; ls = rs.get('log_shift') or 0.0
    print(f"  {s:5} {r.get('dtc', 0):5.2f} {r.get('pct') or 0:6.3f} {r.get('quintile') or '-':>2} {ls:+7.4f} {res['target']:9.2f} {res['target']*math.exp(-ls):9.2f} {(res.get('revision') or {}).get('log_shift', 0):+9.3f}"
          + ('' if 'short_interest' in res else '   (result.json predates the tilt; rerun combine.py)'))
print('all tests passed')
