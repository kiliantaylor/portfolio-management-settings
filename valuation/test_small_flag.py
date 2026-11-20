"""Tests for small_flag.py (RULES.md 26). Runs on a scratch copy of a real round; never touches runs/.

    python3 test_small_flag.py [ROUND SYM]     default 2026-11-17 NVDA
"""
import copy, json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
U, S = (sys.argv[1], sys.argv[2].upper()) if len(sys.argv) > 2 else ('2026-11-17', 'NVDA')
T = tempfile.mkdtemp(prefix='smallflag_'); os.makedirs(f'{T}/{U}'); shutil.copytree(f'{HERE}/runs/{U}/{S}', f'{T}/{U}/{S}')
os.environ['VAL_RUN_ROOT'] = T; os.environ['VAL_OVERRIDES_LOG'] = f'{T}/rule_overrides.jsonl'; sys.path.insert(0, HERE)
import small_flag as SF, judgment_check as JC
JC.RUNS = T; SF.RUNS = T
F = 0
def ok(c, m):
    global F; print(('ok   ' if c else 'FAIL ') + m); F += 0 if c else 1


# ---- unit: feeds_forward on a fake engine
RI0 = dict(quarters=dict(labels=['Q1 FY1', 'Q2 FY1', 'Q3 FY1', 'Q4 FY1', 'Q1 FY2', 'Q2 FY2'], actual=2),
           drivers=[dict(id='x', quarters=[1, 1, 1, 1, 1, 1])])
def fake(mi, *a):
    q = mi['drivers'][0]['quarters']; chain = mi.get('_chain', False)
    rev = [q[i] + (q[i - 1] if chain and i > 2 else 0) for i in range(6)]
    Q = [dict(label=l, fy=l.split()[1], revenue=rev[i], ebit=rev[i] / 2, eps=rev[i] / 10) for i, l in enumerate(mi['quarters']['labels'])][2:]
    rows = [dict(label='FY1', revenue=sum(rev[:4])), dict(label='FY2', revenue=sum(rev[4:]))]
    return dict(quarters=Q, rows=rows)
real = SF._engine; SF._engine = fake; C = ('2026-01-01', 'X', None, None)
ok(SF.feeds_forward(RI0, [dict(path='drivers[id=x].quarters[3:4]', value=[2])], C)['status'] == 'no_feed_forward', 'Q4 that does not chain = no_feed_forward')
ok(SF.feeds_forward(dict(RI0, _chain=True), [dict(path='drivers[id=x].quarters[3:4]', value=[2])], C)['status'] == 'ok', 'Q4 that chains into Q1 = ok')
ok(SF.feeds_forward(RI0, [dict(path='drivers[id=x].quarters[5:6]', value=[2])], C)['status'] == 'last_period', 'last forecast quarter = last_period')
ok(SF.feeds_forward(RI0, [dict(path='drivers[id=x].quarters[3:4]', value=[1])], C)['status'] == 'no_effect', 'input that moves nothing = no_effect')
ok(SF.feeds_forward(RI0, [dict(path='horizon_multiples[0].multiple', value=20)], C)['status'] == 'not_period', 'scalar input = not_period')
SF._engine = real
ok(SF.row_period(RI0, 'x Q4 FY1 = 8,850') is None, 'row_period needs a 4-digit FY label in the set')
ok(SF.row_period(dict(quarters=dict(labels=['Q4 FY2027'])), 'edge_rev Q4 FY2027 = 8,850') == ('q', 0), 'row_period reads "Q4 FY2027"')
# row_part (11-19): which alternatives belong to the flagged quarter
RI1 = dict(quarters=dict(labels=[f'Q{i}' for i in range(16)]))
ok(SF.row_part(RI1, [dict(path='drivers[id=x].quarters[7:8]', value=[1]), dict(path='drivers[id=x].quarters[8:16]', value=[1] * 8)], ('q', 7))[0] == 'own', 'row_part: exact Q4 entry (+ its chain) = own')
ok(SF.row_part(RI1, [dict(path='drivers[id=x].quarters[8:16]', value=[1] * 8)], ('q', 7))[0] == 'other', 'row_part: FY28-29 only = other period')
k_, pt_ = SF.row_part(RI1, [dict(path='drivers[id=x].quarters[7:16]', value=list(range(9))), dict(path='drivers[id=y].quarters[8:16]', scale=1.1)], ('q', 7))
ok(k_ == 'span' and pt_ == [dict(path='drivers[id=x].quarters[7:8]', value=[0])], f'row_part: span cut to the Q4 element only ({pt_})')

# row_periods (11-19 follow-on): year and year-range rows, constants paths
RI2 = dict(quarters=dict(labels=[f'Q{q} FY{y}' for y in range(2025, 2030) for q in range(1, 5)]))
ok(SF.row_periods(RI2, 'sw_rev FY29 at -5.4%') == ('q', frozenset(range(16, 20))), 'row_periods: "FY29" = its four quarters')
ok(SF.row_periods(RI2, 'non_ai_rev FY28-FY29 +3.2%') == ('q', frozenset(range(12, 20))), 'row_periods: "FY28-FY29" = eight quarters')
ok(SF.row_periods(RI2, 'sw_margin Q4 FY26-FY29: 94%') == ('q', frozenset(range(7, 20))), 'row_periods: "Q4 FY26-FY29" from Q4 FY26 on')
ok(SF.row_periods(RI2, 'x Q4 FY2027 = 1') == ('q', frozenset([11])), 'row_periods: single quarter')
ok(SF.row_periods(dict(years=[dict(label='FY2027'), dict(label='FY2028'), dict(label='FY2029')]), 'x FY28-FY29') == ('y', frozenset([1, 2])), 'row_periods: annual set uses years labels')
ok(SF.row_part(RI2, [dict(path='driver_build.quarters.constants.k[16:20]', value=[1] * 4)], SF.row_periods(RI2, 'k FY29'))[0] == 'own', 'row_part: constants path inside FY29 = own')
k_, pt_ = SF.row_part(RI2, [dict(path='driver_build.quarters.constants.k[8:20]', value=list(range(12)))], SF.row_periods(RI2, 'k FY29'))
ok(k_ == 'span' and pt_ == [dict(path='driver_build.quarters.constants.k[16:20]', value=[8, 9, 10, 11])], 'row_part: FY27-29 constants span cut to FY29')
ok(SF.row_part(RI2, [dict(path='driver_build.quarters.constants.k[7:8]', value=[1])], SF.row_periods(RI2, 'k FY29'))[0] == 'other', 'row_part: Q4 FY26 only = other period for an FY29 row')

# AVGO 2026-11-17 (follow-on): whole-year rows; on a scratch copy
if os.path.isdir(f'{HERE}/runs/2026-11-17/AVGO'):
    os.makedirs(f'{T}/2026-11-17', exist_ok=True); shutil.copytree(f'{HERE}/runs/2026-11-17/AVGO', f'{T}/2026-11-17/AVGO')
    pa = f'{T}/2026-11-17/AVGO/supervisor.json'; da = json.load(open(pa))
    da['small_flag_notes'] = [dict(item='sw_rev', row="sw_rev FY29 at -5.4% a year past Gartner's 'by 2028' window", alternatives=[5, 6], searched='test: Gartner VMware attrition window and Broadcom FY26 10-K, nothing past 2028'),
                              dict(item='constant: non_ai_rev', row='non_ai_rev FY28-FY29 +3.2% a year', alternatives=[6, 7], searched='test: WSTS autumn 2026 and Broadcom Q3 FY26 call for non-AI FY28-29, none')]
    json.dump(da, open(pa, 'w'), indent=1)
    V, _ = SF.validate('2026-11-17', 'AVGO'); big = [v['where'] for v in V if v['rule'] == 'bigger-alternative']
    ok(not any('sw_rev' in w for w in big), f'AVGO sw_rev FY29: FY27-29 span alternatives counted by their FY29 part only ({big})')
    ok([w for w in big if 'non_ai_rev' in w] == ["note 1: constant: non_ai_rev | non_ai_rev FY28-FY29 +3.2% a year alt 4"], f'AVGO non_ai_rev FY28-29: only the bookings-led path (+0.53% on FY28-29 alone) is bigger ({big})')
    VJ = JC.check('2026-11-17', 'AVGO'); r_ = {v['rule'] for v in VJ if 'Non-AI' in v['where']}
    ok('small-flag-card-missing-option' in r_ and 'small-flag-card' not in r_, f'judgment_check: non_ai_rev card not refused as small, asked to carry the bigger option ({r_})')
    ok(any(v['rule'] == 'small-flag-card' and 'Software revenue FY29' in v['where'] for v in VJ), 'judgment_check: sw_rev FY29 card still refused as small (note it)')

# ---- integration on the copy
p = f'{T}/{U}/{S}/supervisor.json'; d = json.load(open(p))
if S == 'NVDA':  # 11-19 regression: Q4 FY27 notes were refused on FY28-29 alternatives (SemiAnalysis share, Oracle/SpaceX capex, lead/lag, Edge x1.27)
    d0 = copy.deepcopy(d)
    d['small_flag_notes'] = [dict(item='hs_capture', row='hs_capture Q4 FY2027 = 0.3044', alternatives=[6, 7], searched='test: NVIDIA Q3 FY27 release, CFO commentary and call, no Q4 Hyperscale split'),
                             dict(item='edge_rev', row='edge_rev Q4 FY2027 = 8,850', alternatives=[5, 7], searched='test: NVIDIA Q3 FY27 release, CFO commentary and call, no Q4 Edge outlook')]
    json.dump(d, open(p, 'w'), indent=1)
    V, _ = SF.validate(U, S); big = [v for v in V if v['rule'] == 'bigger-alternative']
    ok(not big, f'NVDA Q4 FY27 notes: no bigger-alternative from other periods ({[v["where"] for v in big]})')
    # a span alternative whose Q4 FY27 part alone is big is still caught
    it = next(x for x in d['judgment_alternatives'] if x['item'] == 'edge_rev'); RIn = d['model']['reconciled_inputs']
    q = next(x for x in RIn['drivers'] if x['id'] == 'edge_rev')['quarters']
    it['alternatives'].append(dict(build='test: Q4 FY27 x60 inside a span', patch=[dict(path='drivers[id=edge_rev].quarters[7:16]', value=[q[7] * 60] + q[8:16])]))
    json.dump(d, open(p, 'w'), indent=1); JC.size(U, S)
    V, _ = SF.validate(U, S)
    ok(any(v['rule'] == 'bigger-alternative' and 'spans Q4 FY2027' in v['why'] for v in V), 'span with a big Q4 FY27 part alone is still caught')
    json.dump(d0, open(p, 'w'), indent=1); JC.size(U, S); d = json.load(open(p))
out = SF.suggest(U, S)
nv = {x['item']: x for x in out}
if S == 'NVDA':
    # (11-19: NVDA's thread now chains FY28-29 from Q4 inside each alternative, so the live cards no longer say FIX THE MODEL;
    # the feeds-forward refusal is tested below on a Q4-only alternative instead)
    ok(isinstance(out, list), 'suggest runs on NVDA')
    V = JC.check(U, S); ok(sum(v['rule'] == 'small-flag-card' for v in V) >= 1, 'judgment_check flags the tiny flagged NVDA cards (small-flag-card)')

# a note on a row whose alternatives fail feeds-forward is refused
it = next(x for x in d['judgment_alternatives'] if x['item'] == nv.get('edge_rev', {}).get('item', 'edge_rev'))
qq = next(x for x in d['model']['reconciled_inputs']['drivers'] if x['id'] == 'edge_rev')['quarters']
it['alternatives'].append(dict(build='test: Q4 FY27 only, no chain', patch=[dict(path='drivers[id=edge_rev].quarters[7:8]', value=[qq[7] * 1.01])]))
json.dump(d, open(p, 'w'), indent=1); JC.size(U, S); d = json.load(open(p)); it = next(x for x in d['judgment_alternatives'] if x['item'] == 'edge_rev')
bad = dict(item=it['item'], row='edge_rev Q4 FY2027 = 8,850', alternatives=[len(it['alternatives']) - 1],
           searched='searched NVIDIA Q4 outlook, IDC Q4 2026 PC, JPR add-in board for a Q4 Edge figure; none found')
d['small_flag_notes'] = [bad]; json.dump(d, open(p, 'w'), indent=1)
V, _ = SF.validate(U, S); ok(any(v['rule'].startswith('feeds-forward-') for v in V), 'note refused when the path does not feed forward')
r = subprocess.run([sys.executable, f'{HERE}/small_flag.py', 'accept', U, S], capture_output=True, text=True, env=os.environ)
ok(r.returncode != 0 and 'REFUSED' in (r.stderr + r.stdout), 'accept refuses while a note fails')

# a valid note: last-quarter alternative (no period after it), tiny, on a flagged row
d = json.load(open(p)); it = next(x for x in d['judgment_alternatives'] if x['item'] == 'edge_rev')
L = d['model']['reconciled_inputs']['quarters']['labels']; n = len(L)
it['alternatives'] = [a for a in it['alternatives'] if SF._periods(d['model']['reconciled_inputs'], a.get('patch')) != ('q', n - 1)]  # copy only: no bigger option on that quarter
it['alternatives'].append(dict(build='test: last forecast quarter x 0.999', source='src 84', patch=[dict(path=f'drivers[id=edge_rev].quarters[{n - 1}:{n}]', scale=0.999)]))
k = len(it['alternatives']) - 1; row = f'edge_rev {L[-1]} = test'
A = json.load(open(f'{T}/{U}/{S}/citation_audit.json'))
A['open_failures'] = [dict(origin='test', item=f'edge_rev {L[-1]}', value='test', result='unsupported'), dict(origin='test', item='opex other', value='1', result='unsupported')]
A['clean'] = False; json.dump(A, open(f'{T}/{U}/{S}/citation_audit.json', 'w'), indent=1)
d['small_flag_notes'] = [dict(item='edge_rev', row=row, alternatives=[k], searched='searched NVIDIA outlook and IDC for this quarter, none found (test row)')]
d['judgment_calls_for_kilian'].append(dict(item='edge_rev', gate_r2_row=row, question='test card', options=[dict(label='a', fv_effect_pct=0.0)]))
json.dump(d, open(p, 'w'), indent=1)
JC.size(U, S)
V, _ = SF.validate(U, S); ok(not V, f'valid last-period note passes ({V[:2]})')
# standing card through gate_accept: uncovered row refused, covered row accepted
r = subprocess.run([sys.executable, f'{HERE}/gate_accept.py', 'accept', U, S, '--msg', SF.STANDING_CARD, '--quote', SF.STANDING_QUOTE, '--items', '1'], capture_output=True, text=True, env=os.environ)
ok(r.returncode != 0 and 'not covered' in r.stderr, 'gate_accept refuses the standing card on a row no note covers')
r = subprocess.run([sys.executable, f'{HERE}/small_flag.py', 'accept', U, S], capture_output=True, text=True, env=os.environ)
ok(r.returncode == 0, 'small_flag accept runs: ' + (r.stdout + r.stderr)[-300:])
A = json.load(open(f'{T}/{U}/{S}/citation_audit.json')); d = json.load(open(p))
ok([f['item'] for f in A['open_failures']] == ['opex other'], 'covered row closed, the other stays open')
ok(A['accepted_carried'][-1]['owner_override']['msg'] == SF.STANDING_CARD, 'closed on the standing card')
ok(not any(c.get('question') == 'test card' for c in d['judgment_calls_for_kilian']) and d['small_flag_notes'][0].get('card_withdrawn'), 'draft card withdrawn into the note')
ok('booked without a card (rule 26)' in SF.note_line(d['small_flag_notes'][0]), 'one-line note: ' + SF.note_line(d['small_flag_notes'][0])[:160])
shutil.rmtree(T)
print(f'{F} failed'); sys.exit(1 if F else 0)
