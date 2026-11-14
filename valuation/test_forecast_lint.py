"""Synthetic checks for forecast_lint.py guide_anchor past-beat anchors (no network, nothing written). Run: python3 test_forecast_lint.py
Track records store actual_vs_mid_pct / median_vs_mid_pct in percent in most sets (AMAT 1.49 = +1.49%) but as fractions in some
(MOD, SPCX, VST 0.019); before 2026-11-09 the lint read percent as a fraction, so 'midpoint x (1 + past beat)' never fired."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forecast_lint as fl

ok = 0
def check(cond, msg):
    global ok
    if not cond: sys.exit(f'FAIL: {msg}')
    ok += 1


def ri(our, per, med=None, lo=9750.0, hi=10750.0, fy='Q4 FY2026', rag=None):
    tr = dict(periods=per)
    if med is not None: tr['median_vs_mid_pct'] = med
    return dict(guidance_check=[dict(metric='revenue', fy=fy, guide_low=lo, guide_high=hi, our_value=our, record_adjusted_guide=rag, track_record=tr)])


def P(period, lo, hi, actual, b): return dict(period=period, first_guide_low=lo, first_guide_high=hi, actual=actual, actual_vs_mid_pct=b)

MID = 10250.0
# percent units, read off the periods (6,800 vs 6,700 mid = +1.49%)
pct = [P('Q4 FY2025', 6200, 7200, 6800, 1.49), P('Q1 FY2026', 6350, 7350, 7012, 2.36), P('Q2 FY2026', 6600, 7600, 7300, 2.82)]
check(fl._pct_unit(dict(periods=pct)) == 'pct', 'percent units read from periods')
h = fl.guide_anchor(ri(round(MID * 1.0236, 1), pct))
check(len(h) == 1 and 'Q1 FY2026 beat +2.36%' in h[0]['why'], f'mid x (1 + 2.36%) fires: {h}')
spread = [P('Q1 FY2026', 6350, 7350, 6917, 1.0), P('Q2 FY2026', 6600, 7600, 7455, 5.0)]
h = fl.guide_anchor(ri(round(MID * 1.03, 1), spread))
check(len(h) == 1 and 'mean beat' in h[0]['why'], f'mid x (1 + mean beat) fires: {h}')
h = fl.guide_anchor(ri(round(MID * 1.034, 1), pct, med=3.4))
check(len(h) == 1 and 'median beat +3.40%' in h[0]['why'], f'mid x (1 + median 3.4%) fires: {h}')
check(fl.guide_anchor(ri(MID * 1.05, pct, med=3.4)) == [], 'a value away from every anchor passes')
check(fl.guide_anchor(ri(MID * 3.36, pct)) == [], 'percent is not read as a fraction (mid x 3.36 no longer an anchor)')

# fraction units, read off the periods (MOD style)
frac = [P('FY2025', 370, 380, 392.1, round(392.1 / 375 - 1, 4)), P('FY2026', 430, 440, 471.0, round(471 / 435 - 1, 4))]
check(fl._pct_unit(dict(periods=frac)) == 'frac', 'fraction units read from periods')
h = fl.guide_anchor(ri(MID * (1 + frac[1]['actual_vs_mid_pct']), frac))
check(len(h) == 1 and 'FY2026 beat +8.28%' in h[0]['why'], f'fraction beat fires: {h}')
check(fl.guide_anchor(ri(MID * (1 + frac[1]['actual_vs_mid_pct'] / 100) * 1.004, frac)) == [], 'fraction not read as percent')

# no derivable period: >1 in size is percent; small values are unclear, so both readings are checked (VST 8,100 x 1.019 case)
bare = [dict(period='FY2025', actual_vs_mid_pct=4.2)]
check(fl._pct_unit(dict(periods=bare)) == 'pct', 'size > 1 with no derivable period = percent')
check(len(fl.guide_anchor(ri(MID * 1.042, bare))) == 1, 'bare percent fires')
vst = [dict(period='FY2025', actual_vs_mid_pct=0.019)]
check(fl._pct_unit(dict(periods=vst)) is None, 'small bare value is unclear')
h = fl.guide_anchor(ri(8100 * 1.019, vst, lo=7600, hi=8600, fy='FY2027'))
check(len(h) == 1 and 'if fraction' in h[0]['why'], f'VST guide x 1.019 fires: {h}')
check(len(fl.guide_anchor(ri(8100 * 1.00019, vst, lo=7600, hi=8600, fy='FY2027'))) == 1, 'percent reading also checked (near mid anyway)')

# closed period skipped; midpoint and record-adjusted guide still fire
check(fl.guide_anchor(ri(MID * 1.0236, pct + [P('Q4 FY2026', 9750, 10750, 10500, 2.44)])) == [], 'closed period skipped')
check(len(fl.guide_anchor(ri(MID, pct))) == 1, 'midpoint fires')
check(len(fl.guide_anchor(ri(10400.0, pct, rag=10401.0))) == 1, 'record-adjusted guide fires')
print(f'ok {ok} checks')
