"""Tests for RULES.md 28 (Kilian 2026-11-20): pregate_diff.py, gsheet_delta.py, guards.bypasses. Run: python3 test_speed_rules.py"""
import json, os, sys, tempfile
T = tempfile.mkdtemp(); os.environ['VAL_RUN_ROOT'] = f'{T}/runs'; os.environ['VAL_GSHEET_LINKS'] = f'{T}/links.json'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pregate_diff as P, gsheet_delta as G, guards
fails = 0
def check(name, got, want):
    global fails
    if got != want: fails += 1; print('FAIL', name, repr(got), repr(want))

# ---- pregate_diff
os.makedirs(f'{T}/runs/B/XYZ'); os.makedirs(f'{T}/runs/U/XYZ')
base = dict(fair_value=dict(reason='Net debt 20,478.5 and minorities 1,488 give $136.54; tax at 20% on the excess.'),
            model=dict(reconciled_inputs=dict(balance=dict(net_debt=20478.5, minorities=1488), years=[dict(tax_rate=0.2)],
                                              inputs_detail=[dict(input='Net debt', value='20,478.5', derivation='stated 20,478.5')])),
            summary='DCF within 20% of the legs.', sources=[dict(id=55)])
cur = json.loads(json.dumps(base))
cur['model']['reconciled_inputs']['balance'].update(net_debt=21507.0, minorities=488)
cur['model']['reconciled_inputs']['years'][0]['tax_rate'] = 0.241
cur['model']['reconciled_inputs']['inputs_detail'][0].update(value='21,507', derivation='20,478.5 + 1,000 + 28.5 = 21,507')
json.dump(base, open(f'{T}/runs/B/XYZ/supervisor.json', 'w')); json.dump(cur, open(f'{T}/runs/U/XYZ/supervisor.json', 'w'))
json.dump(dict(base='B'), open(f'{T}/runs/U/XYZ/update.json', 'w'))
d, op = P.open_hits('U', 'XYZ')
figs = sorted(h['figure'] for h in op)
check('moved count', len(d['moved']), 4)
check('stale figures', figs, ['1,488', '20%', '20,478.5'])
check('before->after sentence not stale', any(h['path'].endswith('derivation') for h in op), False)
check('unrelated 20% (no tax nearby) not stale', any(h['path'] == 'summary' for h in op), False)
P.explain('U', 'XYZ', [h['id'] for h in op if h['figure'] == '20%'], 'the old rate is quoted on purpose')
d, op = P.open_hits('U', 'XYZ'); check('explained drops out', sorted(h['figure'] for h in op), ['1,488', '20,478.5'])
cur['fair_value']['reason'] = 'Net debt 21,507 and minorities 488 give $136.47; tax at 20% on the excess.'
json.dump(cur, open(f'{T}/runs/U/XYZ/supervisor.json', 'w'))
d, op = P.open_hits('U', 'XYZ'); check('fixed prose clean', op, [])

# ---- gsheet_delta
def payload(dirn, cells, fmts, rows=100):
    os.makedirs(dirn, exist_ok=True)
    json.dump([{'sheetId': 0, 'title': 'Summary'}, {'sheetId': 1, 'title': 'Model'}], open(f'{dirn}/tabs.json', 'w'))
    json.dump([{'addSheet': {'properties': {'sheetId': 1, 'title': 'Model', 'gridProperties': {'rowCount': rows, 'columnCount': 26}}}}], open(f'{dirn}/setup.json', 'w'))
    for i, (rng, vals) in enumerate(cells): json.dump(dict(range=rng, values=vals), open(f'{dirn}/values_{i}.json', 'w'))
    json.dump(fmts, open(f'{dirn}/format_0.json', 'w'))
bold = lambda r, c: {'repeatCell': {'range': {'sheetId': 1, 'startRowIndex': r, 'endRowIndex': r + 1, 'startColumnIndex': c, 'endColumnIndex': c + 1},
                                    'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}}, 'fields': 'userEnteredFormat.textFormat.bold'}}
payload(f'{T}/runs/R0/XYZ/gsheet_linked', [("'Summary'!A1", [['Model target', 136.54], ['x', '=B1*2']]), ("'Model'!A1", [['a', 1, 2], ['b', 3, 4]])], [bold(0, 0)])
json.dump(dict(url='https://docs.google.com/spreadsheets/d/OLDID/edit'), open(f'{T}/runs/R0/XYZ/gsheet_linked/linked.json', 'w'))
json.dump({'R0': {'XYZ': 'https://docs.google.com/spreadsheets/d/OLDID/edit'}}, open(f'{T}/links.json', 'w'))
payload(f'{T}/runs/U/XYZ/gsheet', [("'Summary'!A1", [['Model target', 136.47], ['x', '=B1*2']]), ("'Model'!A1", [['a', 1, ''], ['b', 3, 4], ['c', "'12%", '1/2']])], [bold(1, 0)], rows=140)
rt = G.plan('U', 'XYZ')
check('delta route', rt['route'], 'delta'); check('base id', rt.get('base_id'), 'OLDID')
check('changed cells', rt.get('changed_cells'), 5)
b = json.load(open(f'{T}/runs/U/XYZ/gsheet_delta/batch_0.json'))
kinds = [list(q)[0] for q in b]
check('grid grows first', kinds[0], 'updateSheetProperties')
uc = [q['updateCells'] for q in b if 'updateCells' in q]
flat = [c for q in uc for r in q['rows'] for c in r['values']]
check('number typed', {'userEnteredValue': {'numberValue': 136.47}} in flat, True)
check('quoted text kept as text', {'userEnteredValue': {'stringValue': '12%'}} in flat, True)
check('cleared cell', {} in flat, True)
check('parseable string left to update_values', json.load(open(f'{T}/runs/U/XYZ/gsheet_delta/values_0.json'))['values'], [['1/2']])
check('format clear + set', sorted(json.dumps(q['repeatCell']['cell']) for q in b if 'repeatCell' in q),
      sorted([json.dumps({'userEnteredFormat': {}}), json.dumps({'userEnteredFormat': {'textFormat': {'bold': True}}})]))
rt = G.plan('U', 'XYZ', 'testing the bypass'); check('forced full is a bypass', (rt['route'], rt.get('bypass')), ('full', True))
check('delta folder removed on full', os.path.isdir(f'{T}/runs/U/XYZ/gsheet_delta'), False)
json.dump([{'sheetId': 0, 'title': 'Summary'}], open(f'{T}/runs/U/XYZ/gsheet/tabs.json', 'w'))
check('tabs changed -> full', G.plan('U', 'XYZ')['route'], 'full')
os.remove(f'{T}/runs/R0/XYZ/gsheet_linked/linked.json')
check('no snapshot -> full', 'snapshot' in G.plan('U', 'XYZ')['reason'], True)

# ---- bypass list
guards.LOG = f'{T}/rule_overrides.jsonl'
guards.log('pregate-diff', 'r1', symbol='XYZ', update='U'); guards.log('sec-tagged-fetch', 'r2', symbol='XYZ', update=None, item='pref')
guards.log('pregate-diff', 'other', symbol='ABC', update='U'); guards.log('one-gate-round', 'n/a', symbol='XYZ', update='U')
check('bypass lines', [l.split(': ', 1)[1].split(' [')[0] for l in guards.bypass_lines('XYZ', 'U', '2000-01-01')], ['r1', 'r2'])
print('ok' if not fails else f'{fails} failed'); sys.exit(1 if fails else 0)
