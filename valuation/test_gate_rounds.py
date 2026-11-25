"""Checks for gate_rounds.py (RULES.md 24): run `python3 test_gate_rounds.py` (copies one round folder to a temp dir)."""
import json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
import run_digest as RD, gate_rounds as GR
SRC = sys.argv[1] if len(sys.argv) > 1 else 'runs/2026-11-16/META'
tmp = tempfile.mkdtemp(); D, S_ = SRC.split('/')[-2:]; rd = f'{tmp}/{D}/{S_}'; gd = rd + '/gate'
shutil.copytree(SRC, rd); RD.RUNS = GR.RUNS = tmp; up = rd + '/update.json'; n = 0
def ok(c, m):
    global n; n += 1
    if not c: sys.exit(f'FAIL {n}: {m}')
def set_rounds(k):
    u = json.load(open(up)); u['gate_rounds'] = [dict(utc=str(i), why='t') for i in range(k)]; json.dump(u, open(up, 'w'))
def agents(C, text, fail_first=False):
    for i in range(1, C['shards'] + 1):
        us = list(C['groups'])[(i - 1) * 10:i * 10]
        rows = [dict(it, source_url=u, result='ok') for u in us for it in C['groups'][u]]
        if fail_first and i == 1 and rows: rows[0]['result'] = 'not_found'
        json.dump(dict(results=rows), open(f'{gd}/shard_{i}.json', 'w'))
    if not os.path.exists(f'{gd}/shard_text.json') or text is not None: json.dump(dict(results=text or []), open(f'{gd}/shard_text.json', 'w'))
def merge():
    try: RD.gate_merge(D, S_)
    except SystemExit: pass
set_rounds(1); RD.gate_prep(D, S_, update=True); C1 = json.load(open(gd + '/checklist.json'))
drv = json.load(open(rd + '/supervisor.json'))['model']['reconciled_inputs']['drivers'][0]['id']
F1 = next((u, it) for u in C1['groups'] for it in C1['groups'][u]); agents(C1, [dict(origin=f'supervisor driver {drv}', item='a', value='1', result='unsupported'),
            dict(origin='supervisor fair_value', item='reason', value='x', result='ok'),
            dict(origin='supervisor uncited claim', item='zzz', value='qqq', result='not_found')], fail_first=True)
merge(); ok(os.path.exists(gd + '/merged_r1.json') and os.path.exists(gd + '/rows_r1.json'), 'round 1 saves rows_r1 + merged_r1')
ok(not os.path.exists(gd + '/must_card.json'), 'no must-card list in round 1')
# round 2, nothing changed: everything carries, no text agent
set_rounds(2); RD.gate_prep(D, S_, update=True); C2 = json.load(open(gd + '/checklist.json'))
# Kilian 2026-11-23 (CRDO round 3 re-merged round-2 fixes): only passed results carry; earlier failures go back to their checker
op = [(u, it) for u in C2['groups'] for it in C2['groups'][u]]
ok(list(C2['groups']) == [F1[0]] and len(op) == 1 and op[0][1]['item'] == F1[1]['item'] and not C2['arith'],
   'unchanged set: only the URL with an earlier failure is re-opened, and only its failed figure')
car = json.load(open(gd + '/carried.json'))['results']
ok(not any(x.get('result') not in ('ok',) for x in car), 'no earlier failure carries by script')
ok(f'[supervisor driver {drv}] a = 1: was unsupported' in open(gd + '/brief_text.md').read(), 'earlier text failure goes back to the text agent')
ok(not os.path.exists(gd + '/shard_text.json') or 'zzz' in open(gd + '/brief_text.md').read(), 'unplaced earlier failure goes back to the text agent')
# change one driver: only that part is in the text scope, its earlier result is not carried
S = json.load(open(rd + '/supervisor.json')); S['model']['reconciled_inputs']['drivers'][0]['label'] = 'changed'; json.dump(S, open(rd + '/supervisor.json', 'w'))
RD.gate_prep(D, S_, update=True); bt = open(gd + '/brief_text.md').read(); car = json.load(open(gd + '/carried.json'))['results']
ok('RE-GATE SCOPE' in bt and f'driver {drv}' in bt, 'changed driver listed in the text scope')
ok(not any(x.get('origin') == f'supervisor driver {drv}' for x in car), 'changed driver result not carried')
ok(any(x.get('origin') == 'supervisor fair_value' for x in car), 'unchanged part carries')
agents(json.load(open(gd + '/checklist.json')), [dict(origin=f'supervisor driver {drv}', item='a', value='1', result='trend_only')])
merge(); mc = json.load(open(gd + '/must_card.json'))
ok(mc['round'] == 2 and any(m['result'] == 'trend_only' for m in mc['items']), 'round 2 lists trend_only as must-card')
ok('MUST-CARD ROWS' in open(gd + '/brief_fix.md').read(), 'fix brief carries the must-card list')
# round cap
set_rounds(3)
try: GR.check_round(D, S_, 'x'); ok(False, 'round 4 refused')
except SystemExit as e: ok('no override' in str(e) and 'Round history' in str(e), 'round 4 refused with no override, history printed')
set_rounds(1)
try: GR.check_round(D, S_, 'fixed five rows'); ok(False, 'list-of-changes why refused')
except SystemExit: ok(True, '')
REASON = 'reason: one independent re-check of the fix agent own fixes; carding now would put unchecked numbers on cards'
try: GR.check_round(D, S_, REASON); ok(False, 'reason without a trigger refused')
except SystemExit as e: ok('no "trigger:"' in str(e) and 'Round history' in str(e), 'no trigger: refused with history')
try: GR.check_round(D, S_, 'trigger: fixed some rows; ' + REASON); ok(False, 'trigger naming no changed part refused')
except SystemExit as e: ok('names none of' in str(e) and drv in str(e), 'trigger must name a changed part (refusal lists them)')
try: GR.check_round(D, S_, f"trigger: applied Kilian's card picks on driver {drv}; " + REASON); ok(False, "Kilian's picks as trigger refused")
except SystemExit as e: ok("Kilian's picks" in str(e), 'picks refusal says why')
ok(GR.check_round(D, S_, f'trigger: gate round 1 fix rebuilt driver {drv}; ' + REASON) == 2, 'trigger + reason allowed')
e = GR.entry(D, S_, 2, f'trigger: gate round 1 fix rebuilt driver {drv}; ' + REASON)
ok(e['trigger'].startswith('gate round 1') and e['reason'].startswith('one independent') and any(drv in c for c in e['changed']), 'entry logs trigger, reason, changed parts')
u = json.load(open(up)); u['gate_rounds'] = [dict(utc='t1', why='first round'), dict(utc='t2', why='w', **e)]; json.dump(u, open(up, 'w'))
ok('trigger: gate round 1' in GR.history(rd) and 'changed since round 1' in GR.history(rd), 'history shows trigger and changed parts')
# nothing changed since the round being re-gated: refused
S = json.load(open(rd + '/supervisor.json')); GR.save_rows(gd, dict(json.load(open(gd + '/rows_r1.json')), round=2,
    sections={k: v['fp'] for k, v in GR.sections(S).items()}))
set_rounds(2)
try: GR.check_round(D, S_, f'trigger: driver {drv}; ' + REASON); ok(False, 'unchanged set refused')
except SystemExit as e: ok('nothing in supervisor.json changed' in str(e), 'nothing changed since the last round: refused')
GR.snapshot(D, S_, 2, 't'); ok(os.path.exists(gd + '/values_r2.json'), 'snapshot saves gate/values_r2.json')
shutil.rmtree(tmp); print(f'test_gate_rounds: {n} checks ok')
