"""Synthetic checks for solve_check.py and the 2026-11-23 back-solve wording in forecast_lint.py (no network, nothing written).
Run: python3 test_solve_check.py"""
import copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forecast_lint as fl
import solve_check as sc

ok = 0
def check(cond, msg):
    global ok
    if not cond: sys.exit(f'FAIL: {msg}')
    ok += 1

L = [f'Q{q} FY{y}' for y in (2026, 2027, 2028, 2029) for q in (1, 2, 3, 4)]
ENDS = [f'{y - 1 if q < 4 else y}-{["04", "07", "10", "01"][q - 1]}-30' for y in (2026, 2027, 2028, 2029) for q in (1, 2, 3, 4)]
GU = dict(kind='guide', why_nothing_better='x' * 160, tried=[dict(build='end-market build from outside forecasts',
          why_not='the company discloses no revenue weights for its end markets')], source='src 14')


def ri(q, derivation='', source='', guide_use=None, gc=None):
    d = dict(id='comms_rev', label='Communications revenue', unit='$M per quarter', agg='sum', basis='estimate', quarters=q,
             derivation=derivation, source=source)
    if guide_use: d['guide_use'] = guide_use
    return dict(unit='$M', quarters=dict(labels=L, ends=ENDS, actual=6), drivers=[d], guidance_check=gc or [])


def rules(R): return [(f['rule'], f.get('flag_only', False)) for f in fl.solved(R)]


FY26 = [454.7, 515.6, 556.6, 567.4]  # 2,094.3
# MRVL 2026-11-20 before the gate fix: Q4 FY27 = FY26 x 1.08 less Q1-Q3, FY28-29 at +2.5% to ~$2.5B in FY31
MRVL = FY26 + [585.1, 567.8, 488.0, 620.9, 551.5, 576.4, 558.2, 632.2, 565.3, 590.8, 572.1, 648.1]
TXT = ("Q4 FY27 621: Murphy, same call: 'we currently expect fiscal 2027 growth to approach our 10% target'; 'approach' taken as +8% "
       "on FY26 2,094.3 (moderately conservative) = FY27 2,261.8, so Q4 = FY27 less Q1-Q3. FY28 2,318.3 and FY29 2,376.3 (+2.5% a year): "
       "Murphy at Investor Day (src 63): 'we just maintain our position with very modest growth over the period' to ~$2.5B in FY31, "
       "which FY27 2,262 reaches at ~2.5% a year.")
R = ri(MRVL, TXT, guide_use=GU)
h = fl.solved(R)
check(('year-residual', False) in rules(R), f'MRVL Q4 FY27 = FY26 x 1.08 less Q1-Q3 refused: {h}')
check(('compound-target', False) in rules(R), f'MRVL FY28-29 at +2.5% compounding to ~$2.5B FY31 refused: {h}')
check(any(f['rule'] == 'year-residual' for f in fl.lint(R)), 'refused through forecast_lint.lint even with a complete guide_use (a haircut is not the guide)')
W = [(f['rule'], f['phrase']) for f in fl.wording(R)]
check(('back-solve', 'FY27 less Q1-Q3') in W, f'"FY27 less Q1-Q3" wording refused: {W}')
check(any(r == 'back-solve' and 'reaches at ~2.5%' in p for r, p in W), f'"reaches at ~2.5%" wording refused: {W}')

# the rebuilt MRVL row (end-market growth, descriptive "+6.8%" in a cross-check) passes
NEW = FY26 + [585.1, 567.8, 488.0, 595.0, 561.3, 586.3, 567.4, 629.6, 588.6, 614.8, 595.0, 660.1]
R = ri(NEW, "Q4 FY27 595.0 = Q4 FY26 567.4 grown at the end-market rate. Cross-checks only, not inputs: Murphy's FY27 growth "
            "'approach our 10% target' (our FY27 2,235.9 = +6.8%, more cautious) and the Investor Day comms ~$2.5B in FY31 (src 63).")
check(rules(R) == [], f'rebuilt MRVL row passes: {fl.solved(R)}')

# a year at the company guide midpoint with Q4 the residual: a flag with a complete guide_use, refused without one
q = FY26 + [585.1, 567.8, 488.0, 2300.0 - 585.1 - 567.8 - 488.0] + NEW[8:]
gc = [dict(metric='communications revenue', fy='FY2027', guide_low=2250.0, guide_high=2350.0)]
check(rules(ri(q, gc=gc)) == [('year-residual', False)], 'guide midpoint less reported quarters refused without guide_use')
check(rules(ri(q, gc=gc, guide_use=GU)) == [('guide-use', True)], 'guide midpoint less reported quarters with a complete guide_use is a flag')
bad = dict(GU, why_nothing_better='too short')
check(rules(ri(q, gc=gc, guide_use=bad)) == [('year-residual', False)], 'an incomplete guide_use does not clear it')
# consensus is never a guide use
gc2 = [dict(metric='communications revenue', fy='FY2027', guide_low=2000.0, guide_high=2100.0, consensus=2300.0)]
check(rules(ri(q, gc=gc2, guide_use=GU)) == [('year-residual', False)], 'consensus less reported quarters refused even with guide_use')
# a guidance row for another metric (a tax rate) does not apply
gc3 = [dict(metric='non-GAAP tax rate', fy='FY2027', guide_low=2300.0, guide_high=2300.0)]
check(rules(ri(q, gc=gc3)) == [], 'a guidance row naming another metric does not apply')
# a guide figure quoted in the driver's text, declared: flag; the same figure only as our own printed total: no stated figure
qt = "Murphy (CEO, Q2 call): 'we expect communications revenue of $2.3 billion in fiscal 2027'."
check(rules(ri(q, qt, guide_use=GU)) == [('guide-use', True)], 'a quoted management year figure with guide_use is a flag')
check(rules(ri(q, qt)) == [('year-residual', False)], 'a quoted management year figure without guide_use is refused')
check(rules(ri(q, 'Q4 FY27 built from shipments; FY2027 $2,300.0M (+9.8%).')) == [], 'our own printed year total is not a stated figure')

# growth compounding to a long-term target: refused; uneven bottom-up growth passes
def path(y27, rates):
    ys = [y27]
    for r in rates: ys.append(round(ys[-1] * (1 + r), 1))
    out = []
    for y in ys[1:]: out += [round(y * s, 1) for s in (0.24, 0.25, 0.24)] + [None]
    return ys, out
q27 = FY26 + [585.1, 567.8, 488.0, 620.9]
ys, out = path(2261.8, [0.03, 0.03])
for i in range(3, 8, 4): out[i] = round(ys[(i // 4) + 1] - sum(out[i - 3:i]), 1)
LT = 'Investor Day (src 63): communications ~$2.55B by FY31, the company target.'
check(rules(ri(q27 + out, LT)) == [('compound-target', False)], f'+3%/yr landing on $2.55B FY31 refused: {fl.solved(ri(q27 + out, LT))}')
ys, out = path(2261.8, [0.034, 0.021])
for i in range(3, 8, 4): out[i] = round(ys[(i // 4) + 1] - sum(out[i - 3:i]), 1)
check(rules(ri(q27 + out, LT)) == [], 'uneven bottom-up growth passes')
ys, out = path(2261.8, [0.05, 0.05])
for i in range(3, 8, 4): out[i] = round(ys[(i // 4) + 1] - sum(out[i - 3:i]), 1)
check(rules(ri(q27 + out, LT)) == [], 'a constant rate that does not compound to the target passes')

# wording: phrases barred, negated / quoted / history / filed-year arithmetic not
def wd(t): return [f['phrase'] for f in fl.wording(ri(MRVL, t)) if f['rule'] == 'back-solve']
for t, want in [('FY28-29 grow at 2.5%, the rate that reaches the FY31 figure.', 'rate that reaches'),
                ('Growth consistent with reaching the $2.5B goal.', 'consistent with reaching'),
                ('FY2028 grows 9%, enough to land on the long-term target.', 'land on the long-term target'),
                ('Q4 = FY2027 view minus Q1-Q3.', 'FY2027 view minus Q1-Q3'),
                ('Q4 set as the rest of the year after the plan.', 'as the rest of the year'),
                ('Growth needed to reach the framework is 6%.', 'needed to reach'),
                ('Q4 closes the gap to the plan.', 'closes the gap')]:
    check(bool(wd(t)), f'barred ({want}): {t} -> {wd(t)}')
for t in ['The path does not reach the target until FY2031; built from orders.',
          "The CEO said they are 'on track to deliver the target' this year.",
          'The record landed at or above the first guide in FY2024 and FY2025.',
          'Q4 FY2026 actual = 10-K FY2026 less 9M (filed).',
          'TTM capex = FY26 $6,042M + 9M FY27 $8,856M - 9M FY26 $4,758M.']:
    check(wd(t) == [], f'not barred: {t} -> {wd(t)}')

# gate rows map the new rules to back_solved
check(all(r in str(fl.gate_rows.__code__.co_consts) for r in ('year-residual', 'compound-target')), 'gate_rows maps the new rules')
print(f'ok {ok}')
