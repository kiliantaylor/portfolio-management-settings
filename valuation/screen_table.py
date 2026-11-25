"""Screen table (Kilian 2026-10-09 17:27Z: slim per-name work, still say which names to look into further).

python3 screen_table.py D  ->  screens/D/table.json

Reads screens/D/<SYM>.json (agents/screen.md, checked by agents/screen_gate.md) and adds two figures we already compute:
the EPS-revision percentile (newest revisions_sleeve snapshot on or before D) and the options-implied 12M range
(priors/D/<SYM>.json q10-q90 as % of spot; width only, not a return call). No targets, fair values or buys.
"""
import csv, glob, json, os, sys

D = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
SD = os.path.join(HERE, 'screens', D)

snaps = sorted(f for f in glob.glob(os.path.join(HERE, '..', 'revisions_sleeve', 'snapshots', '*.csv'))
               if os.path.basename(f)[:10] <= D)
rev = {}
if snaps:
    with open(snaps[-1]) as fh:
        rev = {r['symbol']: float(r['pct']) for r in csv.DictReader(fh) if r.get('pct')}

rows = []
for f in sorted(glob.glob(os.path.join(SD, '*.json'))):
    if os.path.basename(f) == 'table.json':
        continue
    s = json.load(open(f))
    sym = s['symbol']
    pr = os.path.join(HERE, 'priors', D, sym + '.json')
    rng = None
    if os.path.exists(pr):
        p = json.load(open(pr)); q = p['quantiles']; spot = p['spot']
        rng = [round(100 * (q['q10'] / spot - 1), 1), round(100 * (q['q90'] / spot - 1), 1)]
    c = s.get('comps', {})
    chk = s.get('citation_check', {})
    rows.append(dict(symbol=sym, verdict=s.get('verdict'), reason=s.get('reason'),
                     clean=bool(chk.get('clean')), metric=c.get('metric'), basis=c.get('basis'),
                     subject=c.get('subject'), peer_median=c.get('peer_median'),
                     subject_growth=c.get('subject_growth'), gap=c.get('growth_adjusted_gap'),
                     n_peers=len(c.get('peers', [])),
                     catalyst=s.get('catalyst', {}), risk=s.get('risk', {}),
                     revision_pct=round(rev[sym], 3) if sym in rev else None, range_10_90=rng,
                     exclusions=s.get('exclusions', {})))
# a verdict only counts once its citation check is clean
for r in rows:
    if not r['clean']:
        r['verdict'] = 'pending_check'
order = {'full_run': 0, 'pending_check': 1, 'pass': 2}
rows.sort(key=lambda r: (order.get(r['verdict'], 3), -(r['revision_pct'] or 0)))
json.dump(dict(date=D, revisions_snapshot=os.path.basename(snaps[-1]) if snaps else None, rows=rows),
          open(os.path.join(SD, 'table.json'), 'w'), indent=1)
print(len(rows), 'screens;', sum(r['verdict'] == 'full_run' for r in rows), 'full_run')
