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
# --- 2026-11-17 carry-forward (Kilian: "project economic reality, not just blindly project forward past data") ---
def drv(der, q=None, A=None):
    R = dict(drivers=[dict(id='capex', basis='estimate', derivation=der, **({'quarters': q} if q else {}))])
    if q: R['quarters'] = dict(labels=[f'Q{i % 4 + 1} FY{2025 + i // 4}' for i in range(len(q))], actual=A)
    return R
rules = lambda R: [f['rule'] for f in fl.lint(R)]
check('carry-forward' in rules(drv('Q4 FY2026 = Q3 $77.3B x 1.013, the lowest Q4/Q3 ratio of 2022-2025 (XBRL) = $78.3B.')), 'AMZN Q4/Q3 ratio fires')
check('carry-forward' in rules(drv('Q4 AWS EBITDA margin held at Q3\'s 58.72%.')), 'held at Q3 fires')
check('carry-forward' in rules(drv('FY2029: FY2028 quarters x 1.035: the FY2028 rate held.')), 'the FY2028 rate held fires')
check('carry-forward' in rules(drv('Q4 FY26 5,216.8 = Q4 FY25 4,764.2 x 1.095 (9M pace +9.5% y/y).')), 'past quarter x the 9M pace fires')
check(rules(drv('Q4 FY26 = Q4 FY25 4,764 x 1.06: 18 new chiller lines at 40 units a quarter (slide 7, src 76).')) == [], 'past quarter x a driver-built rate passes')
check(rules(drv('Q4 capex was $78.3B on a Q4/Q3 ratio; now built from the commitments table (src 4).')) == [], 'superseded wording skipped')
check(rules(drv("CFO: 'a higher than average step up in our Q1 operating expenses' (src 110).")) == [], 'a quoted step-up is evidence, not our method')
check(rules(drv('Q1-Q4 split by the FY2023-FY2025 mean quarter shares so the year\'s total is the driver build.')) == [], 'multi-year quarter split passes (rule 9)')
check(rules(drv('Built from backlog conversion (src 3). | BEFORE: Q4 = Q3 x 1.05, the average Q4/Q3 ratio.')) == [], 'history after BEFORE: skipped')
R = dict(drivers=[], driver_build=dict(quarters=dict(constants_note=dict(capex_q=dict(derivation='Q4 = Q3 x the mean Q4/Q3 ratio 1.02 of FY23-25.')))))
check('carry-forward' in rules(R), 'driver_build constants_note is checked')
# numbers: flat at last actual, next quarter = last actual x a past same-quarter q/q
check('carry-num' in rules(drv('Built from contracts (src 1).', [1, 2, 3, 4, 5, 6, 6, 6, 6, 6], 6)), 'last actual held flat fires')
q = [100, 110, 120, 150, 130, 140, 150]; q += [q[-1] * 150 / 120, 200, 210, 220]
check('carry-num' in rules(drv('Built from contracts (src 1).', q, 7)), 'next quarter at the past Q4/Q3 ratio fires')
check('carry-num' not in rules(drv('Built from contracts (src 1).', [100, 110, 120, 150, 130, 140, 150, 171, 160, 170, 180, 190], 7)), 'a driver-built next quarter passes')

# --- guide use as a last resort, flagged (Kilian 2026-11-17) ---
GU = dict(kind='guide', why_nothing_better='The company gives no segment or customer split of this line and no outside forecast covers it: '
          'the only forward information for the quarter is the company guide itself, so a driver build would be invented.',
          tried=[dict(build='customer capex budgets for the quarter', why_not='no customer discloses spend on this product line by quarter')],
          source='src 4')
g = ri(MID, pct); g['guidance_check'][0]['guide_use'] = GU
check(fl.lint(g) == [] and [x['rule'] for x in fl.guide_uses(g)] == ['guide-use'], 'declared guide use is a flag, not a failure')
g['guidance_check'][0]['guide_use'] = dict(GU, kind='guide_x_beat')
check([x['rule'] for x in fl.lint(g)] == ['guide-use-unexplained'], 'kind must match what the value sits on')
g['guidance_check'][0]['guide_use'] = dict(GU, why_nothing_better='no better data')
check([x['rule'] for x in fl.lint(g)] == ['guide-use-unexplained'], 'short explanation fails')
g['guidance_check'][0]['guide_use'] = dict(GU, tried=[])
check([x['rule'] for x in fl.lint(g)] == ['guide-use-unexplained'], 'no tried alternative fails')
check(rules(drv('Q4 capex = $220B guide - 9M $175.67B = $44.33B.')) == ['guide-residual'], 'guide less 9M is a guide use')
R = drv('Q4 capex = $220B guide - 9M $175.67B = $44.33B.'); R['drivers'][0]['guide_use'] = GU
check(fl.lint(R) == [] and len(fl.guide_uses(R)) == 1, 'declared guide residual is a flag')
check(rules(drv('Building Systems H2 855 sits at the top of its 1.58-1.65B guide less H1 792.9.')) == [], 'a cross-check against the guide passes')
print(f'ok {ok} checks')
