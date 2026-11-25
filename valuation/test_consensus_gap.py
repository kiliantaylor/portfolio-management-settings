"""Tests for consensus_gap.py (RULES.md 42, Kilian 2026-11-24). Run: python3 test_consensus_gap.py"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import consensus_gap as C

N = [0]
def ok(c, m):
    N[0] += 1
    if not c: print('FAIL', m); sys.exit(1)


# metric parsing: revenue / EPS rows in, growth / margins / whispers out
for m, want in [('revenue', 'revenue'), ('revenue (Zacks consensus sales)', 'revenue'), ('Revenue ($M)', 'revenue'), ('net sales', 'revenue'),
                ('EPS (Zacks)', 'eps'), ('EPS diluted', 'eps'), ('EPS non-GAAP', 'eps'), ('revenue growth', None), ('EBIT margin', None),
                ('whisper EPS (Street + usual beat)', None), ('free cash flow', None), ('EPS growth', None)]:
    ok(C.metric(m) == want, f'metric {m!r} -> {C.metric(m)!r}, want {want!r}')

# source classes: the company's own words, consensus feeds and outside sources
cik = 1751008
S = {'own 8-K': dict(title='AppLovin 8-K Ex.99.1 Q3', url='https://www.sec.gov/Archives/edgar/data/1751008/0001/x.htm'),
     'peer 10-Q': dict(title='Unity 10-Q Q3', url='https://www.sec.gov/Archives/edgar/data/1810806/0001/x.htm'),
     'call': dict(title='AppLovin Q3 2026 earnings call transcript (Investing.com)', url='https://www.investing.com/news/transcripts/x'),
     'ir': dict(title='Investor letter', url='https://investors.applovin.com/x'),
     'zacks': dict(title='Zacks estimates', url='https://www.zacks.com/stock/quote/APP/detailed-earnings-estimates'),
     'nasdaq': dict(title='Nasdaq.com analyst earnings forecast', url='https://api.nasdaq.com/api/analyst/APP/earnings-forecast'),
     'kind consensus': dict(title='S&P estimates', url='https://example.com/x', kind='consensus'),
     'sell side': dict(title='Morgan Stanley APP note: revenue 2027E $10.36B, above consensus on gaming', url='https://example.com/ms', kind='sell_side'),
     'industry': dict(title='Sensor Tower state of mobile gaming 2026', url='https://sensortower.com/x', kind='industry_forecast'),
     'plan': dict(title='Long-term model', url='https://example.com/plan', kind='company_plan')}
want = {'own 8-K': 'company', 'peer 10-Q': 'outside', 'call': 'company', 'ir': 'company', 'zacks': 'consensus', 'nasdaq': 'consensus',
        'kind consensus': 'consensus', 'sell side': 'outside', 'industry': 'outside', 'plan': 'company'}
for k, x in S.items(): ok(C.source_class(x, 'APP', cik) == want[k], f'source_class {k}: {C.source_class(x, "APP", cik)}, want {want[k]}')

# consensus selection: published beats none_found; Zacks EPS row first; then latest as_of; guidance_check level for revenue
RI = dict(reference=dict(consensus=[
    dict(metric='revenue', fy='FY2027', value=10000.0, as_of='2026-11-01'), dict(metric='revenue', fy='FY2027', value=10470.0, as_of='2026-11-24'),
    dict(metric='EPS non-GAAP', fy='FY2028', value=9.7, as_of='2026-11-20'), dict(metric='EPS (Zacks)', fy='FY2028', value=8.3, as_of='2026-10-26'),
    dict(metric='revenue', fy='FY2029', value=None, none_found=dict(date='2026-11-24', publishers=['Zacks', 'Nasdaq'])),
    dict(metric='revenue', fy='FY2029', value=None, none_found=dict(date='2026-11-25', publishers=['Zacks'])),
    dict(metric='EPS diluted', fy='Q4 FY2026', value=4.66, as_of='2026-11-24')]),
    guidance_check=[dict(metric='revenue', fy='FY2026', consensus=8290.0, consensus_as_of='2026-11-24')])
c = C.consensus(RI)
ok(c[('revenue', 2027)]['value'] == 10470.0, 'latest revenue row wins')
ok(c[('eps', 2028)]['value'] == 8.3, 'Zacks EPS row beats a newer non-Zacks row')
ok(c[('revenue', 2029)]['value'] is None, 'none_found row kept when nothing is published')
ok(c[('revenue', 2026)]['value'] == 8290.0, 'guidance_check consensus counts for revenue')
ok(('eps', 2026) not in c, 'a quarter row is not a year row')
ok(C.none_found_ok(dict(none_found=dict(date='2026-11-24', publishers=['Zacks'])))[0], 'none_found with date + publishers ok')
ok(not C.none_found_ok(dict(none_found=dict(date='2026-11-24', publishers=[])))[0], 'none_found without publishers refused')
ok(not C.none_found_ok(dict(none_found='searched'))[0], 'none_found as bare text refused')

# table / check on a set with a stubbed engine: missing year, none-published year, explained and unexplained gaps
def run(RI, sources, B):
    orig = C.booked; C.booked = lambda *a, **k: B
    try:
        d = dict(as_of='2026-11-25', sources=sources, model=dict(reconciled_inputs=dict(RI, drivers=[dict(id='x')])))
        return C.table('2026-11-25', 'APP', d), C.check('2026-11-25', 'APP', d)
    finally: C.booked = orig

B = dict(revenue={2026: 8377.5, 2027: 11494.0, 2028: 13500.0, 2029: 15800.0}, eps={2026: 16.4, 2027: 21.0}, eps_basis='Zacks basis', years=[2026, 2027, 2028, 2029])
src = [S['call'], S['sell side'], S['zacks']]
RI2 = dict(reference=dict(consensus=[dict(metric='revenue', fy='FY2026', value=8290.0), dict(metric='revenue', fy='FY2027', value=10470.0),
                                     dict(metric='EPS (Zacks)', fy='FY2026', value=16.25), dict(metric='EPS (Zacks)', fy='FY2027', value=20.86),
                                     dict(metric='revenue', fy='FY2028', value=12000.0),
                                     dict(metric='revenue', fy='FY2029', value=None, none_found=dict(date='2026-11-24', publishers=['Zacks', 'Nasdaq']))],
                          consensus_gaps=[dict(metric='revenue', fy='FY2027', why='management says gaming runs way over its range', source_ids=[1, 3])]))
T, F = run(RI2, src, B)
st = {(r['metric'], r['fy']): r['status'] for r in T['rows']}
ok(st[('revenue', 'FY2026')] == 'ok', 'FY2026 inside 5%')
ok(st[('revenue', 'FY2027')] == 'gap_unsupported', 'FY2027 +9.8% explained only by the company call and a consensus feed: unsupported (the APP case)')
ok(st[('revenue', 'FY2028')] == 'gap_unsupported', 'FY2028 +12.5% with no entry: unsupported')
ok(st[('revenue', 'FY2029')] == 'none_published', 'FY2029 none published')
ok(st[('eps', 'FY2027')] == 'ok', 'EPS FY2027 inside 5%')
rules = sorted((f['rule'], f['where']) for f in F)
ok(rules == [('consensus-gap', 'consensus gap revenue FY2027'), ('consensus-gap', 'consensus gap revenue FY2028')], f'findings {rules}')
RI2['reference']['consensus_gaps'][0]['source_ids'] = [1, 2]
T, F = run(RI2, src, B)
ok({(r['metric'], r['fy']): r['status'] for r in T['rows']}[('revenue', 'FY2027')] == 'gap_explained', 'a sell-side source explains the gap')
del RI2['reference']['consensus'][3]
T, F = run(RI2, src, B)
ok(any(f['rule'] == 'consensus-missing' and f['where'] == 'consensus eps FY2027' for f in F), 'missing EPS FY2027 row is a consensus-missing finding')
ok(C.street_line(T, {('revenue', 'FY2029')})[0].startswith('Street FY2029 revenue ($M): no consensus published'), 'street line for a none-published year')
ok('+9.8% vs Street' in C.street_line(T, {('revenue', 'FY2027')})[0], 'street line shows the gap')

# round_fail: gate start sees only missing rows; gaps Kilian accepted drop out at finish; buys grandfathered before BUY_BLOCK_FROM
with tempfile.TemporaryDirectory() as td:
    os.makedirs(f'{td}/2026-11-25/APP'); os.makedirs(f'{td}/2026-11-24/APP')
    d = dict(as_of='2026-11-25', sources=src, model=dict(reconciled_inputs=dict(RI2, drivers=[dict(id='x')])))
    for D in ('2026-11-25', '2026-11-24'): json.dump(d, open(f'{td}/{D}/APP/supervisor.json', 'w'))
    json.dump(dict(accepted_carried=[dict(item='consensus gap revenue FY2028', status='accepted_by_kilian')]), open(f'{td}/2026-11-25/APP/citation_audit.json', 'w'))
    C.RUNS = td; orig = C.booked; C.booked = lambda *a, **k: B
    try:
        g = C.round_fail('2026-11-25', 'APP', step='gate'); ok({f['rule'] for f in g} == {'consensus-missing'}, f'gate step: missing only {g}')
        f = C.round_fail('2026-11-25', 'APP', step='finish'); ok(sorted(x['where'] for x in f) == ['consensus eps FY2027'], f'finish: accepted gap dropped {[x["where"] for x in f]}')
        ok(C.round_fail('2026-11-24', 'APP', for_buys=True) == [], 'buys: rounds before BUY_BLOCK_FROM grandfathered')
        ok(len(C.round_fail('2026-11-25', 'APP', for_buys=True)) == 1, 'buys: rounds from BUY_BLOCK_FROM blocked')
        R = C.gate_rows('2026-11-25', 'APP'); ok(R == [], f'gate rows: the gap Kilian accepted does not re-open {R}')
        json.dump({}, open(f'{td}/2026-11-25/APP/citation_audit.json', 'w'))
        R = C.gate_rows('2026-11-25', 'APP'); ok(len(R) == 1 and R[0]['item'] == 'consensus gap revenue FY2028' and R[0]['result'] == 'unsupported', 'gate rows: an unexplained gap is unsupported')
        try: C.refuse('2026-11-25', 'APP', 'gate'); ok(False, 'gate refuse should exit')
        except SystemExit as e: ok('consensus-missing' in str(e), 'gate refuse names the missing row')
    finally: C.booked = orig

# gate items: consensus rows and gap explanations are cited figures
I = C.gate_items(RI2, lambda ids: [f'https://x/{i}' for i in ids])
ok(any(b == 'claim' and 'differs from consensus' in it for it, v, b, u in I), 'gap explanation is a claim item')
ok(sum(1 for it, v, b, u in I if b == 'consensus') == 4, 'every published consensus row is an item')
# basis traps (audit/street_check_1124): Zacks page rows on company non-GAAP; quarter-built Street; provider basis bridge
Sz = [dict(title='Zacks detailed estimates', url='https://www.zacks.com/stock/quote/SNOW/detailed-earnings-estimates'),
      dict(title='Nasdaq', url='https://api.nasdaq.com/api/analyst/SNOW/earnings-forecast')]
ok(C.page_basis(dict(metric='EPS (Zacks)', source_ids=[1]), Sz), 'a Zacks-page EPS row is company non-GAAP')
ok(not C.page_basis(dict(metric='EPS (Zacks)', source_ids=[2]), Sz), "Nasdaq's Zacks feed is after stock comp")
ok(not C.page_basis(dict(metric='EPS', basis='Zacks (SBC expensed)', source_ids=[1]), Sz), 'a row saying SBC expensed stays on the bridged basis')
ok(C.page_basis(dict(metric='EPS non-GAAP'), Sz), 'a row saying non-GAAP is company basis')
RIq = dict(reference=dict(consensus=[dict(metric='EPS (Zacks)', fy='Q4 FY2026', value=0.9, as_of='2026-11-20')]))
ok(C.quarter_street(RIq, 2026, {2026: (2.1, ['Q4 FY2026'])}) == (3.0, 'Q4 FY2026 0.9'), 'reported quarters + quarter consensus')
ok(C.quarter_street(RIq, 2026, {2026: (2.1, ['Q3 FY2026', 'Q4 FY2026'])}) is None, 'a missing quarter row: no quarter build')
c2 = C.consensus(dict(reference=dict(consensus=[dict(metric='EPS (Zacks)', fy='FY2027', value=15.89, as_of='2026-11-24', source_ids=[2]),
                                                dict(metric='EPS (Zacks)', fy='FY2027', value=17.02, as_of='2026-11-24', source_ids=[1])])), Sz)
ok(c2[('eps', 2027)]['value'] == 17.02, 'same date: the Zacks page beats the lagging Nasdaq feed')
RIb = dict(reference=dict(consensus=[dict(metric='revenue', fy='FY2027', value=10000.0, basis_bridge=dict(booked_adjust=-1000.0, why='Zacks sales net of TAC', source_ids=[2]))]))
Bb = dict(revenue={2027: 11000.0}, eps={}, eps_basis='Zacks basis', years=[2027])
T, F = run(RIb, src, Bb)
ok(T['rows'][0]['status'] == 'ok' and T['rows'][0]['booked'] == 10000.0, f'basis bridge applied before comparing {T["rows"]}')
ok(any('basis bridge' in it for it, v, b, u in C.gate_items(RIb, lambda ids: ids)), 'basis bridge is a gated claim')
print(f'OK {N[0]} checks')
