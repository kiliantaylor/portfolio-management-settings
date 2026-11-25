"""Tests for RULES.md 29 as a hard stop (Kilian 2026-11-25: "there is absolutely no bypass permitted"). Run: python3 test_eps_tieout.py"""
import inspect, os, sys, types
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eps_tieout as E
fails = 0
def check(name, got, want):
    global fails
    if got != want: fails += 1; print('FAIL', name, repr(got), repr(want))

# ---- no override anywhere
check('refuse takes no why', list(inspect.signature(E.refuse).parameters), ['U', 's', 'stage'])
check('eps_tieout never calls guards', 'guards' in open(os.path.join(HERE, 'eps_tieout.py')).read().split('def refuse', 1)[1].split('def _hist', 1)[0], False)
nu = open(os.path.join(HERE, 'name_update.py')).read()
check('finish calls refuse without a reason', 'eps_tieout.refuse(U, s)' in nu and 'eps_tieout.refuse(U, s, why' not in nu, True)
check('finish lists excluded quarters to Kilian', 'eps_tieout_excluded=eps_tieout.kilian_lines(tie)' in nu and 'esc += eps_tieout.kilian_lines(tie)' in nu, True)

# ---- an unreadable set stops (no silent "not checked")
try: E.refuse('__no_such_round__', 'XYZ'); check('unreadable set refused', 'returned', 'SystemExit')
except SystemExit as e:
    m = str(e)
    check('unreadable set refused', m.startswith('REFUSED (eps-tieout, finish)'), True)
    check('refusal offers no --why', '--why' in m, False)

# ---- forecast_check
def P(excl=None, carries=False):
    q = dict(actual=False, **(dict(other_income=1.0) if carries else {}))
    return types.SimpleNamespace(mi=dict(balance=dict(nonop_excluded=excl or {})), Q=[q])
def row(end, nonop, ties=True): return dict(end=end, status='ok', gaap_ties=ties, why=None if ties else 'rebuilt 1.000 vs filed 1.10: a filed line is missing',
                                             lines=dict(filed_nonop_ex_marks=nonop))
check('nonop not carried fails', E.forecast_check(P(), [row('2026-09-30', 12.0)])['ok'], False)
check('nonop carried passes', E.forecast_check(P(carries=True), [row('2026-09-30', 12.0)])['ok'], True)
ok = dict(reason='FX remeasurement, not recurring', source='10-Q Q3 2026 Note 9')
check('excluded with reason+source passes', E.forecast_check(P({'2026-09-30': ok}), [row('2026-09-30', 12.0)])['ok'], True)
check('excluded without source fails', E.forecast_check(P({'2026-09-30': dict(reason='FX')}), [row('2026-09-30', 12.0)])['ok'], False)
check('excluded as bare string fails', E.forecast_check(P({'2026-09-30': 'FX'}), [row('2026-09-30', 12.0)])['ok'], False)
check('untied quarter fails even when nonop carried', E.forecast_check(P(carries=True), [row('2026-09-30', 0.0, ties=False)])['ok'], False)
check('untied quarter fails even when excluded', E.forecast_check(P({'2026-09-30': ok}), [row('2026-09-30', 12.0, ties=False)])['ok'], False)
check('no_filing row not excluded fails', E.forecast_check(P(), [dict(end='2026-09-30', status='no_filing', why='filed tax not found')])['ok'], False)

# ---- what Kilian is shown
L = E.kilian_lines(dict(excluded={'2026-09-30': ok}, ytd_derived=['OVERRIDE (RULES.md 29, told to Kilian): x']))
check('excluded line quoted', L[0], 'EPS tie-out: 2026-09-30 non-operating income left out of the forecast (balance.nonop_excluded): '
                                    'FX remeasurement, not recurring [source: 10-Q Q3 2026 Note 9]')
check('ytd-derived quarter quoted', len(L), 2)

# ---- blanket overrides on the other checks are gone (Kilian card 2026-11-25 "Remove blanket overrides")
import timeliness, judgment_check, speaker
for name, f in (('timeliness.refuse', timeliness.refuse), ('judgment_check.refuse', judgment_check.refuse),
                ('speaker.refuse', speaker.refuse), ('speaker.refuse_set', speaker.refuse_set)):
    check(f'{name} takes no reason', 'why' in inspect.signature(f).parameters, False)
import name_update
check('finish takes no reason', list(inspect.signature(name_update.finish).parameters), ['U', 's'])
check('gate keeps only the round reason (RULES.md 24)', list(inspect.signature(name_update.gate).parameters), ['U', 's', 'why'])
fin = nu.split('def finish(U, s):', 1)[1].split('\ndef ', 1)[0]
check('no need_why left in finish', 'need_why' in fin, False)
check('no pregate bypass log in gate', "guards.log('pregate-diff'" in nu, False)
import subprocess
for args in (['finish', 'U', 'XYZ', '--why', 'x'], ['gate', 'U', 'XYZ', '--why-pregate', 'x'], ['finish', 'U', 'XYZ', '--why-judgment', 'x']):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'name_update.py'), *args], capture_output=True, text=True)
    check(f'CLI refuses {args[-2]}', r.returncode != 0 and 'REFUSED' in (r.stderr + r.stdout), True)

print('OK' if not fails else f'{fails} FAILED'); sys.exit(1 if fails else 0)
