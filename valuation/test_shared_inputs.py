"""python3 test_shared_inputs.py: owner publish, consumer take (declared and by text), stale, brief, on a temp registry and runs."""
import json, os, tempfile
import shared_inputs as si

T = tempfile.mkdtemp(); si.RUNS = os.path.join(T, 'runs'); si.REG = os.path.join(T, 'reg.json')


def mk(U, s, drivers, extra=None):
    d = os.path.join(si.RUNS, U, s); os.makedirs(d, exist_ok=True)
    ri = dict(drivers=drivers, **(extra or {}))
    json.dump(dict(model=dict(reconciled_inputs=ri)), open(os.path.join(d, 'supervisor.json'), 'w'))


old = {'CY2026': 750.7, 'CY2027': 1013.88, 'CY2028': 1359.36}
json.dump(dict(entries=dict(hs_capex=dict(id='hs_capex', owner='NVDA', row='hs_capex', field='years', labels=list(old), values=old,
     round='2026-11-09', consumers=dict(CRDO=dict(rows=['copper_tam'], taken_values=old, taken_round='2026-11-09'),
                                        COHR=dict(rows=['mkt'], taken_values=old, taken_round='2026-11-09'))))), open(si.REG, 'w'))
mk('2026-11-17', 'NVDA', [dict(id='hs_capex', years=[750.6998, 1009.450128, 1305.264852])])
f = si.publish('2026-11-17', 'NVDA')
assert f and 'CRDO, COHR' in f[0], f
assert si.load()['entries']['hs_capex']['values']['CY2027'] == 1009.45
assert sorted(x['symbol'] for x in si.stale()) == ['COHR', 'CRDO']
assert 'OLDER' in si.brief('CRDO') and si.brief('AVGO') == ''
assert si.publish('2026-11-17', 'NVDA') == []  # same path: nothing new
mk('2026-11-23', 'CRDO', [dict(id='copper_tam', derivation='Big-4 capex 1,009.45 (CY2027) / 1,305.26 (CY2028), CY2026 750.70')])
assert si.take('2026-11-23', 'CRDO') == []
mk('2026-11-23', 'COHR', [dict(id='mkt', derivation='capex 1,080 -> 1,330')])
assert si.take('2026-11-23', 'COHR')  # older path: a flag line
mk('2026-11-24', 'COHR', [dict(id='mkt')], dict(shared_inputs=dict(hs_capex=dict(owner='NVDA', round='2026-11-17'))))
assert si.take('2026-11-24', 'COHR') == []  # declared
assert si.stale() == []
mk('2026-11-20', 'NVDA', [dict(id='gm', years=[0.7])])
assert 'no row' in si.publish('2026-11-20', 'NVDA')[0]
print('test_shared_inputs: ok')
