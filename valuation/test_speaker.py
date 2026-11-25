"""Tests for speaker.py (RULES.md 25, Kilian 2026-11-18). python3 test_speaker.py"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import speaker as S

TR = 'https://s205.q4cdn.com/x/CRWV-US-CORRECTED-TRANSCRIPT-CoreWeave-Q2-2026-Earnings-Call.pdf'
CFO = {'says': 'Q2 capex $9.4B', 'speaker': 'Nitin Agrawal', 'role': 'CFO'}
LIU = {'says': '>3 GW active by end of next year', 'speaker': 'Irvin Liu', 'role': 'analyst', 'firm': 'Evercore ISI'}
n = 0


def src(title, speakers=None, url=TR):
    d = {'title': title, 'url': url}
    if speakers is not None: d['speakers'] = speakers
    return d


def run(sources, text):
    return S.check_set({'drivers': [{'id': 'acie', 'derivation': text}]}, sources)


def ok(cond, what):
    global n
    if not cond: sys.exit(f'FAIL: {what}')
    n += 1


T_BAD = 'CoreWeave Q2 2026 corrected transcript (2026-08-11): Q2 capex $9.4B; >3 GW active targeted by end-2027'
T_OK = "CoreWeave Q2 2026 corrected transcript (2026-08-11): Q2 capex $9.4B (CFO); Evercore analyst Irvin Liu asked about >3 GW active by end-2027"

# 1. no speakers at all
f = run([src(T_BAD)], 'capex rises [src 1].')
ok([x['result'] for x in f] == ['speaker_missing'], 'transcript with no speakers is speaker_missing')
# 2. the CoreWeave case: analyst figure in the title as a company target, and in a derivation
f = run([src(T_BAD, [CFO, LIU])], "CoreWeave's remaining 1.6 GW = 4.6 GW contracted less '>3 GW active by end of next year' [src 1].")
ok({x.get('where') for x in f if x['result'] == 'analyst_as_company'} == {'source title', 'drivers[0].derivation'}, f'CoreWeave replay flags title + derivation: {f}')
# 3. labelled as the analyst's: passes
f = run([src(T_OK, [CFO, LIU])], "Evercore analyst Irvin Liu's '>3 GW by end-2027' is not a company target [src 1]; capex $9.4B [src 1].")
ok(f == [], f'labelled analyst figure passes: {f}')
ok(run([src(T_OK, [CFO, LIU])], "Liu asked about 3 GW in the question [src 1].") == [], 'speaker name + asked labels it')
# 4. valid restated: company evidence
R = dict(LIU, restated={'speaker': 'Michael Intrator', 'role': 'CEO', 'quote': 'yes, we expect more than 3 GW active by the end of 2027'})
ok(run([src(T_BAD, [CFO, R])], 'CoreWeave targets >3 GW by end-2027 [src 1].') == [], 'executive restatement with the figure clears it')
# 5. restated by an analyst / without the figure: does not count
for bad in [{'speaker': 'X', 'role': 'analyst', 'quote': 'more than 3 GW active by end of 2027'},
            {'speaker': 'Michael Intrator', 'role': 'CEO', 'quote': 'we feel good about the trajectory of the build'}]:
    f = run([src(T_BAD, [CFO, dict(LIU, restated=bad)])], 'x [src 1].')
    ok(any(x['result'] == 'analyst_as_company' for x in f), f'bad restated refused: {bad}')
# 6. a title figure nobody is recorded for
f = run([src(T_OK + '; backlog ~$133B', [CFO, LIU])], 'x [src 1].')
ok([x.get('figure') for x in f] == ['133b'], f'uncovered title figure is speaker_missing: {f}')
# 7. an analyst entry needs a firm; an unknown role is refused
ok(run([src(T_OK, [CFO, dict(LIU, firm='')])], 'x [src 1].')[0]['result'] == 'speaker_missing', 'analyst with no firm')
ok(run([src(T_OK, [CFO, dict(LIU, role='guy on the call')])], 'x [src 1].')[0]['result'] == 'speaker_missing', 'unknown role')
# 8. not a transcript / not cited: ignored
ok(run([src('NVIDIA 10-K FY2026: revenue $130.5B', url='https://www.sec.gov/x.htm')], 'rev [src 1].') == [], '10-K ignored')
ok(run([src(T_BAD)], 'nothing cited here.') == [], 'uncited transcript ignored')
ok(not S.is_transcript(src('WYSU: Acton calls for a conditional moratorium on new data centers', url='https://wysu.org/x')), "'calls for' is not a call")
ok(S.is_transcript(src('Microsoft Q1 FY2027 earnings call (2026-10-28): capex', url='https://x.com/a')), 'earnings call title')
# 9. history paths are not uses
ok(run([src(T_OK, [CFO, LIU])], 'x [src 1].') == [] and
   S.check_set({'citation_audit': {'corrections': [{'from': "4.6 GW less '>3 GW active targeted' [src 1]"}]}, 'd': '[src 1]'}, [src(T_OK, [CFO, LIU])]) == [],
   'a correction\'s from text is history')
# 10. roles
for r, c in [('CFO', 'company'), ('Director of Investor Relations', 'company'), ('IR', 'company'), ('Evercore ISI analyst', 'analyst'),
             ('operator', 'other'), ('', 'missing'), ('Chief Strategy Officer', 'company')]:
    ok(S.role_class(r) == c, f'role {r!r} -> {c}')
# 11. gate-merge rows from the checkers' records
with tempfile.TemporaryDirectory() as rd:
    os.makedirs(f'{rd}/gate')
    json.dump({'transcript_urls': {TR: {}}}, open(f'{rd}/gate/speaker.json', 'w'))
    json.dump({'sources': [src(T_OK, [CFO, LIU])], 'model': {}}, open(f'{rd}/supervisor.json', 'w'))
    rows = [dict(item='CoreWeave active GW end-2027', value='3 GW', source_url=TR, result='ok', _src='shard', speaker='Irvin Liu', speaker_role='analyst'),
            dict(item='Q2 capex', value='$9.4B', source_url=TR, result='ok', _src='shard', speaker='', speaker_role=''),
            dict(item='Q2 capex', value='$9.4B', source_url=TR, result='ok', _src='shard', speaker='Some analyst', speaker_role='analyst', speaker_firm='X'),
            dict(item='analyst Liu: 3 GW', value='3 GW', source_url=TR, result='ok', _src='shard', speaker='Irvin Liu', speaker_role='analyst'),
            dict(item='old row', value='$9.4B', source_url=TR, result='ok', _src='shard', carried_from_round=1),
            dict(item='other URL', value='3 GW', source_url='https://x', result='ok', _src='shard', speaker_role='analyst')]
    G = S.gate_check(rd, rows)
    ok([g['result'] for g in G] == ['analyst_as_company', 'speaker_missing', 'analyst_as_company'], f'checker rows: {[g["result"] for g in G]}')
    # mismatch: the set says CFO, the checker says CEO is fine; the set says CFO and the checker an analyst on a labelled row
    G = S.agent_rows_check(rd, [dict(item='analyst estimate of capex', value='$9.4B', source_url=TR, _src='shard', speaker='Y', speaker_role='analyst', speaker_firm='Z'),
                                dict(item='capex', value='$9.4B', source_url=TR, _src='shard', speaker='Michael Intrator', speaker_role='CEO')])
    ok([g['result'] for g in G] == ['speaker_mismatch'], f'mismatch: {[g["result"] for g in G]}')
# 12. finish refuses with no override at all (Kilian 2026-11-25: "there is absolutely no bypass permitted")
os.environ['VAL_RUN_ROOT'] = tempfile.mkdtemp()
import importlib; importlib.reload(S)
rd = f"{os.environ['VAL_RUN_ROOT']}/2026-11-18/ZZZ"; os.makedirs(f'{rd}/gate')
json.dump({'transcript_urls': {TR: {}}}, open(f'{rd}/gate/speaker.json', 'w'))
json.dump({'sources': [src(T_BAD, [CFO, LIU])], 'model': {}, 'd': '[src 1]'}, open(f'{rd}/supervisor.json', 'w'))
try: S.refuse('2026-11-18', 'ZZZ', 'finish'); ok(False, 'refused')
except SystemExit as e: ok('REFUSED (transcript-speaker)' in str(e) and '--why' not in str(e), 'refusal text, no override offered')
import inspect; ok(list(inspect.signature(S.refuse).parameters) == ['U', 's', 'step'], 'refuse takes no reason')
ok(list(inspect.signature(S.refuse_set).parameters) == ['U', 's', 'step'], 'refuse_set takes no reason')
# 13. replay on the NVDA set from before gate round 2 (if present)
p = f'{HERE}/runs/2026-11-17/NVDA/supervisor.json.pre_gatefix_r2'
if os.path.exists(p):
    d = json.load(open(p)); sr = d['sources']; sr[172]['speakers'] = [CFO, LIU]
    f = [x for x in S.check_set(d, sr) if x['src'] == 173 and x['result'] == 'analyst_as_company']
    ok(len(f) >= 2, f'NVDA pre-fix replay catches the >3 GW: {len(f)}')
# 14. pre-gate / finish set check (Kilian 2026-11-23, MRVL): no gate/speaker.json needed; a transcript with no speakers is refused
rd2 = f"{os.environ['VAL_RUN_ROOT']}/2026-11-23/YYY"; os.makedirs(rd2)
T_PLAIN = 'CoreWeave Q2 2026 corrected transcript (2026-08-11): Q2 capex $9.4B'
json.dump({'sources': [src(T_PLAIN)], 'd': 'capex [src 1]'}, open(f'{rd2}/supervisor.json', 'w'))
for step in ('gate', 'finish'):
    try: S.refuse_set('2026-11-23', 'YYY', step); ok(False, f'{step}: transcript with no speakers passed')
    except SystemExit as e: ok(f'REFUSED (transcript-speaker, {step})' in str(e) and 'speaker_missing' in str(e), f'{step} refusal text')
json.dump({'sources': [src(T_PLAIN, [{'says': 'Q2 capex $9.4B', 'speaker': 'Nitin Agrawal', 'role': 'CFO'}])], 'd': 'capex [src 1]'}, open(f'{rd2}/supervisor.json', 'w'))
ok(S.refuse_set('2026-11-23', 'YYY', 'gate') is None, 'speakers recorded: gate passes')
json.dump({'sources': [src(T_PLAIN)], 'd': 'no citation here'}, open(f'{rd2}/supervisor.json', 'w'))
ok(S.refuse_set('2026-11-23', 'YYY', 'gate') is None, 'an uncited transcript source is not the set\'s')
json.dump({'sources': [src(T_PLAIN)], 'd': 'capex [src 1]'}, open(f'{rd2}/supervisor.json', 'w'))
try: S.refuse_set('2026-11-23', 'YYY', 'gate'); ok(False, 'set refused with no override')
except SystemExit: ok(True, 'set refused with no override')
# 15. replay: MRVL 2026-11-20 set (src 14 from 11-02, src 63 added 11-22) is refused before the gate (read only)
p = f'{HERE}/runs/2026-11-20/MRVL/supervisor.json'
if os.path.exists(p):
    d = json.load(open(p)); f = S.check_set(d, d.get('sources') or [])
    bare = {i for i in (14, 63) if len(d.get('sources') or []) >= i and not d['sources'][i - 1].get('speakers')}  # until the MRVL thread records them
    ok(bare <= {x['src'] for x in f if x['result'] == 'speaker_missing'}, f'MRVL replay: {sorted({x["src"] for x in f})}')
print(f'OK {n} checks')
