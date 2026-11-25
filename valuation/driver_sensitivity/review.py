"""Write the two driver-range review files from data/driver_sensitivity.json + driver_ranges.json + driver_outside.json.
Basis column = where the range comes from: filings / outside forecast / our scenario case / judgment."""
import json, re
V = '/mnt/project-files/valuation'
N = json.load(open(f'{V}/data/driver_sensitivity.json'))['names']; R = json.load(open(f'{V}/data/driver_ranges.json')); O = json.load(open(f'{V}/data/driver_outside.json'))
import os
ST = json.load(open(f'{V}/data/street_revenue.json')) if os.path.exists(f'{V}/data/street_revenue.json') else {}
NICK = {('AMAT', 'shr'): 'shr (AMAT share of WFE)'}
FIL = re.compile(r"sec\.gov|\b10-K\b|\b10-Q\b|\b8-K\b|\bfiled\b|shareholder letter|earnings release|earnings call|\bQ[1-4] (FY)?20\d\d call", re.I)

def basis(s, r, ov):
    why = ov.get('reason', '')
    out = bool(O.get(s, {}).get(r['driver'])) or 'Outside' in why
    if r['driver'] == 'dc_mor': b = 'shared pause build (your card)'
    elif 'card' in why.lower() and 'option' in why.lower(): b = 'your card'
    elif 'no source found' in why.lower(): b = 'judgment (no source found)'
    elif "model's own cited" in why: b = 'our scenario case'
    elif FIL.search(why) or (r.get('recent') and not ov): b = 'filings'
    else: b = 'judgment'
    if out: b += ' + outside forecast' if b != 'judgment' else ''
    if out and b == 'judgment': b = 'outside forecast'
    if b.startswith('filings') and 'http' not in why and not r.get('recent'): b = b.replace('filings', 'filings (no link yet)')
    return b + ('' if not ov or ov.get('status') != 'proposed' else ' (awaiting OK)')

def write(path, title, names, intro):
    L = [f'# {title}', '', intro, '']
    for s in names:
        n = N[s]; L += [f'## {s} (research target ${n["research_target"]}, round {n["round"]})', '', '| Driver | Target low - high | Swing | Basis | Why |', '|---|---|---|---|---|']
        st = ST.get(s)
        if st:
            rng = f" (range ${st['low']}-{st['high']}B)" if st.get('low') else ''
            flag = ' **Outside the Street range.**' if st.get('low') and not (st['low'] <= st['model'] <= st['high']) else ''
            L[-2:-2] = [f"Street revenue cross-check, {st['year']}: model ${st['model']}B vs Street mean ${st['mean']}B{rng}, {st['n']} analysts ({st['gap_pct']:+}%).{flag} Source: {st['url']}", '']
        for r in n['rows']:
            if 'rt_low' not in r or r['move_pct'] < 0.5: continue
            ov = R.get(s, {}).get(r['driver'], {})
            why = (ov.get('reason') or (f"filed changes {r['recent']['changes']} ({r['recent']['years']}), {'2 sd' if r['recent']['n'] >= 5 else 'largest move around the recent mean'}" if r.get('recent') else '')).replace('|', '/')
            out = O.get(s, {}).get(r['driver'])
            if out: why += ' Outside: ' + '; '.join(f"{y}: {e['value']} ({e['who']}, {e['url']})" for y, es in out.items() for e in es)
            L.append(f"| {NICK.get((s, r['driver']), r['driver'])} | ${r['rt_low']} - ${r['rt_high']} | +-{r['move_pct']}% | {basis(s, r, ov)} | {why} |")
        L.append('')
    open(path, 'w').write('\n'.join(L))

if __name__ == '__main__':
    intro = open('/mnt/project-files/notes/driver_ranges_held_1109.md').read().split('\n')[2]
    write('/mnt/project-files/notes/driver_ranges_held_1109.md', 'Driver ranges for held names (proposed, 2026-11-09)', ['NVDA', 'AVGO', 'VST', 'MOD', 'AMAT', 'GOOGL', 'NFLX', 'T', 'ABT'], intro)
    write('/mnt/project-files/notes/driver_ranges_researched_1109.md', 'Driver ranges for researched names (proposed, 2026-11-09)', 'APP MRVL XEL BKNG CRDO AMBQ AMZN DY META SNOW BE INTC COHR KEYS'.split(), intro)
