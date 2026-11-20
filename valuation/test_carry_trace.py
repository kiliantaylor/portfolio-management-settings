"""Checks for carry_trace.py (Kilian 2026-11-18, AMZN Q4 FY2026 AWS $49,663M). Run: python3 test_carry_trace.py
Synthetic series first (no files); then the AMZN 2026-11-17 states when the run folders are still there (copied to a temp dir)."""
import json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carry_trace as ct

n = 0
def ok(c, m):
    global n; n += 1
    if not c: sys.exit(f'FAIL {n}: {m}')

L = ['Q1 FY2026', 'Q2 FY2026', 'Q3 FY2026', 'Q4 FY2026', 'Q1 FY2027']
def S_(**kw):  # name -> (values, kind, inputs)
    return {k: dict(labels=L, values=v, actual=3, kind=kind, inputs=ins, id=k) for k, (v, kind, ins) in kw.items()}

# carry-step: last actual x a round factor
s = S_(**{'line AWS revenue': ([37587.0, 42232.0, 45984.0, 45984.0 * 1.08, 54065.1], 'line', {'ppe', 'turn'})})
h = ct.steps(s); ok(len(h) == 1 and h[0]['rule'] == 'carry-step' and 'x 1.08' in h[0]['value'], f'x1.08 step found: {h}')
s = S_(**{'line AWS revenue': ([37587.0, 42232.0, 45984.0, 49099.0, 53232.9], 'line', {'ppe', 'turn'})})
ok(not ct.steps(s), 'a built quarter off its drivers is not a step')
s = S_(**{'driver margin': ([0.58, 0.59, 0.5872, 0.5872 * 1.02, 0.6], 'driver', None)})
ok(not ct.steps(s), 'ratios (margins) are not level series')

# carry-held: built line at its base value while the inputs moved
cur = S_(**{'line AWS revenue': ([1, 2, 3, 49663.0, 5], 'line', {'ppe', 'turn'}), 'driver ppe': ([1, 2, 3, 340.741, 5], 'driver', None),
            'driver turn': ([1, 2, 3, 0.583, 5], 'driver', None)})
base = S_(**{'line AWS revenue': ([1, 2, 3, 49663.0, 5], 'line', {'ppe', 'turn'}), 'driver ppe': ([1, 2, 3, 326.521366, 5], 'driver', None),
             'driver turn': ([1, 2, 3, 0.608389, 5], 'driver', None)})
h = ct.holds(cur, [], [('base round 2026-10-29', base, [])])
ok([x['rule'] for x in h] == ['carry-held'] and 'ppe 326.521 -> 340.741' in h[0]['why'], f'inputs moved, output held: {h}')
ok(not ct.holds(base, [], [('base', base, [])]), 'nothing moved: no hit')

# carry-base: same value, the reason in words changed (and the same reason kept = no hit)
v = {'driver ads': ([1, 2, 3, 24.941, 5], 'driver', None)}
old = [('guidance_check net sales Q4 FY2026 drivers_note', "AWS $49,663M (Q3 x 1.08, +39.6%) + advertising $24,941M (+17%, the release's 'high teens') = $245,178M.")]
same = [('guidance_check net sales Q4 FY2026 drivers_note', "GATE ROUND 3 FIX 2026-11-17: AWS $49,099M (+38% y/y) + advertising $24,941M (+17%, the release's 'high teens') = $244,614M.")]
ok(not ct.holds(S_(**v), same, [('base', S_(**v), old)]), 'unchanged clause beside a changed one: no hit')
new = [('driver ads derivation', 'Q4 FY2026 advertising $24,941M from the agency ad-spend survey and Prime Video ad load.')]
h = ct.holds(S_(**v), new, [('base', S_(**v), old)])
ok(len(h) == 1 and h[0]['rule'] == 'carry-base', f'same value, new reason: {h}')
ok(not ct.holds(S_(**{'driver ads': ([1, 2, 3, 25.5, 5], 'driver', None)}), new, [('base', S_(**v), old)]), 'value changed: no hit')
ok(ct.quotes('AWS $49.66B', 49663.0) and ct.quotes('49,663', 49663.0) and not ct.quotes('+38%', 0.38), 'figure matching by scale and printed precision')

# AMZN 2026-11-17 states (rounds 1-3 and the final set) against the 10-29 base
R = os.path.join(HERE, 'runs'); A = f'{R}/2026-11-17/AMZN'
if os.path.exists(f'{A}/supervisor.json.pre_gatefix') and os.path.exists(f'{R}/2026-10-29/AMZN/supervisor.json'):
    import forecast_lint as fl
    tmp = tempfile.mkdtemp(); os.makedirs(f'{tmp}/2026-10-29/AMZN'); os.makedirs(f'{tmp}/2026-11-17/AMZN')
    shutil.copy(f'{R}/2026-10-29/AMZN/supervisor.json', f'{tmp}/2026-10-29/AMZN/'); shutil.copy(f'{A}/update.json', f'{tmp}/2026-11-17/AMZN/')
    fl.RUNS = tmp
    def state(suf):
        shutil.copy(f'{A}/supervisor.json{suf}', f'{tmp}/2026-11-17/AMZN/supervisor.json'); return ct.trace('2026-11-17', 'AMZN')
    for i, suf in enumerate(('.pre_gatefix', '.pre_gatefix2', '.pre_gatefix3'), 1):
        h = state(suf)
        ok(any(x['where'] == 'line AWS revenue' and x['rule'] == 'carry-held' for x in h), f'AMZN gate round {i}: AWS $49,663M caught as held')
        ok(any(x['where'] == 'line AWS revenue' and x['rule'] == 'carry-step' for x in h), f'AMZN gate round {i}: Q3 x 1.08 caught')
    ok(not [x for x in state('') if 'AWS' in x['where']], 'AMZN final set (rebuilt AWS $49,099M): no AWS hit')
    # round 2 against a round 1 snapshot
    shutil.copy(f'{A}/supervisor.json.pre_gatefix', f'{tmp}/2026-11-17/AMZN/supervisor.json')
    ct.snapshot(f'{tmp}/2026-11-17/AMZN', 1, json.load(open(f'{A}/supervisor.json.pre_gatefix'))['model']['reconciled_inputs'], 't')
    ok(any(r[0].startswith('gate round 1') for r in ct.refs('2026-11-17', 'AMZN')), 'gate/values_r1.json read as a reference round')
    shutil.rmtree(tmp)
else: print('(AMZN 2026-11-17 run folders gone: real-case checks skipped)')
print(f'test_carry_trace: {n} checks ok')
