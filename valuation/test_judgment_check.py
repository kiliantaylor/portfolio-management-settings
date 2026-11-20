"""Synthetic checks for judgment_check.py source resolution (no network, nothing written). Run: python3 test_judgment_check.py
2026-11-16 bug: `src N` whose sources entry had no http URL (an uploaded broker digest) fell through to sources[N] and returned the
NEXT source's link; SPCX 11-15 card cited src 13 (Morgan Stanley digest) and showed the Hund-Monk-Tice paper, and the check passed."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judgment_check as jc

ok = 0
def check(cond, msg):
    global ok
    if not cond: sys.exit(f'FAIL: {msg}')
    ok += 1

D = dict(sources=[
    dict(title='Q3 8-K', url='https://sec.gov/q3'),                                   # src 1
    dict(title='Morgan Stanley digest', url='valuation/broker/digests/upload_x.json'),  # src 2: upload, no public link
    dict(title='Hund-Monk-Tice', url='https://cfr.example/hmt.pdf'),                  # src 3
    dict(title='No link at all', url=''),                                             # src 4
    dict(title='Abs path', url='/mnt/project-files/valuation/runs/x/peers.md'),       # src 5
    dict(title='Path key', path='valuation/notes/y.md'),                              # src 6
    dict(title='file URL', url='file:///mnt/project-files/valuation/notes/t.pdf'),    # src 7 (AMAT src 125 shape)
    dict(title='Next public', url='https://fool.example/old'),                        # src 8
])

u = jc._urls(['src 2'], D)
check(not any('hmt' in x for x in u), f'src 2 must not borrow src 3 link: {u}')
check(len(u) == 1 and u[0].startswith('/mnt/project-files/valuation/broker/digests/upload_x.json') and 'no public link' in u[0], f'upload shown as project file: {u}')
check(jc._urls(['src 1'], D) == ['https://sec.gov/q3'], 'src 1 = sources[0]')
check(jc._urls(['src 3'], D) == ['https://cfr.example/hmt.pdf'], 'src 3 = sources[2]')
check(jc._urls(['src 4'], D) == [], f"src 4 has nothing, must not borrow src 5: {jc._urls(['src 4'], D)}")
check(jc._urls(['src 19'], D) == [] and jc._urls(['src 0'], D) == [], 'out of range resolves to nothing')
check(jc._urls(['src 5'], D)[0].startswith('/mnt/project-files/valuation/runs/x/peers.md'), 'absolute project path kept')
check(jc._urls(['src 6'], D)[0].startswith('/mnt/project-files/valuation/notes/y.md'), 'path key used')
u7 = jc._urls(['src 7'], D)
check(len(u7) == 1 and u7[0].startswith('/mnt/project-files/valuation/notes/t.pdf') and 'fool' not in u7[0], f'file:// URL is a project file, not src 8: {u7}')
check(jc._urls([3], D) == ['https://cfr.example/hmt.pdf'], 'bare int = src id')
check(jc._urls(['src 1, 3'], D) == ['https://sec.gov/q3', 'https://cfr.example/hmt.pdf'], 'list of ids')
check(len(jc._urls(['src 1-3'], D)) == 3, 'range of ids')
check(jc._urls(['https://x.example/a src 3 slide 9'], D) == ['https://x.example/a', 'https://cfr.example/hmt.pdf'], 'url + src id')

def card(srcs):
    return dict(item='x', question='q', context='c' * 130, options=[
        dict(label='A', reasoning='r' * 50, sources=srcs), dict(label='B', reasoning='r' * 50, sources=['src 1'])])
rules = lambda srcs: [v['rule'] for v in jc.card_problems(0, card(srcs), D)]
check(rules(['src 2']) == [], f'upload-only option passes as a real source: {rules(["src 2"])}')
check(rules(['src 4']) == ['card-option-bad-src', 'card-option-no-link'], f'src with no link flagged: {rules(["src 4"])}')
check(rules(['src 1', 'src 19']) == ['card-option-bad-src'], f'bad id flagged even beside a good one: {rules(["src 1", "src 19"])}')

# Plausibility screen (Kilian 2026-11-18, NVDA supply floor at the history ratio: FY28 EPS $39.0 vs consensus $15.63; line 2.0x)
SD = {'model': {'reconciled_inputs': {
    'reference': {'consensus': [dict(metric='revenue', fy='FY2028', value=680.0, as_of='2026-11-05'),
                                dict(metric='revenue', fy='FY2028', value=678.6, as_of='2026-11-17'),
                                dict(metric='EPS', fy='FY2028', value=15.63, as_of='2026-11-17'),
                                dict(metric='whisper EPS (Street + usual beat)', fy='FY2028', value=99.0, as_of='2026-11-17')]},
    'guidance_check': [dict(metric='revenue growth', fy='FY2028', guide_low=0.75, guide_high=0.75)]}}}
base = dict(revenue={2027: 423.0, 2028: 725.0}, eps={2028: 16.62})
A = jc.anchors(SD, base)
check(('consensus', 678.6) in A['revenue'][2028], f'latest consensus used: {A["revenue"]}')
check(any(n == 'guide' and abs(v - 740.25) < 0.01 for n, v in A['revenue'][2028]), f'growth guide on booked prior year: {A["revenue"]}')
check(A['eps'][2028] == [('consensus', 15.63)], f'whisper EPS is not an anchor: {A["eps"]}')
pl = jc.plausibility(dict(revenue={2028: 1620.8}, eps={2028: 39.0}), base, A)
check(pl['over'] and pl['metric'] == 'eps' and abs(pl['distance'] - 39.0 / 16.62) < 0.01, f'NVDA ratio option screened: {pl}')
pl = jc.plausibility(dict(revenue={2028: 1418.2}, eps={2028: 33.0}), base, A)
check(not pl['over'] and pl['distance'] < 2.0, f'1.99x stays an alternative: {pl}')
pl = jc.plausibility(dict(revenue={2028: 330.0}, eps={2028: 10.0}), base, A)
check(pl['over'] and pl['distance'] > 2.0, f'below half the lowest anchor screened: {pl}')
pl = jc.plausibility(dict(revenue={2028: 700.0}), base, A)
check(pl['distance'] == 1.0 and not pl['over'], f'inside band = 1.0: {pl}')
A2 = jc.anchors(SD, base, dict(anchors=[dict(metric='revenue', fy='FY2028', value=1700.0, source='src 9', what='buyer build')]))
pl = jc.plausibility(dict(revenue={2028: 1620.8}), base, A2)
check(pl['distance'] == 1.0, f'a named anchor widens the band: {pl}')
A3 = jc.anchors(SD, base, dict(anchors=[dict(metric='revenue', fy='FY2028', value=1700.0, source='', what='unsourced')]))
check(A3 == A, 'an unsourced named anchor is ignored')
pl = jc.plausibility(dict(revenue={2030: 9999.0}), base, A)
check(pl['distance'] is None and not pl['over'], f'no outside anchor = screen does not apply: {pl}')
check(jc._metric('Net sales') == 'revenue' and jc._metric('EPS diluted') == 'eps' and jc._metric('AI semiconductor revenue') is None, 'metric names')

print(f'OK {ok} checks')
