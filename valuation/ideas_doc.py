"""Weekly ideas doc (Kilian 2026-10-09 17:32Z: "make the ideas tab a doc in the drive folder so we can remove it from
price targets").

python3 ideas_doc.py D  ->  ideas/D/ideas_doc.html, uploaded as Google Doc "Weekly Ideas D" to Drive folder
'Portfolio Management' / Ideas. Content: the screen table (screens/D/table.json, when it exists), the ranker's ideas
with thesis / catalysts / risks / fact-check / sources, the scouts' themes, and the quant screen's leaders and pullbacks.
"""
import glob, html, json, os, sys

D = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
e = lambda x: html.escape('' if x is None else str(x))
pct = lambda x: '' if x is None else f'{100 * x:+.1f}%'
import re


def _src(u):
    # link only a real URL inside the source label; otherwise plain text (Drive turns a bad href into a broken docs.google.com link)
    m = re.search(r'https?://[^\s)\]>"]+', str(u))
    return f'<li><a href="{e(m.group(0))}">{e(u)}</a></li>' if m else f'<li>{e(u)}</li>'

idir = max((p for p in glob.glob(f'{HERE}/ideas/20*') if os.path.basename(p) <= D), default=None)
I = json.load(open(f'{idir}/ideas.json')) if idir and os.path.exists(f'{idir}/ideas.json') else {}
SC = json.load(open(f'{idir}/screen.json')) if idir and os.path.exists(f'{idir}/screen.json') else {}
scouts = {}
for f in sorted(glob.glob(f'{idir}/scout_*.json')) if idir else []:
    try: j = json.load(open(f)); scouts[j.get('sector') or os.path.basename(f)[6:-5]] = j.get('themes', [])
    except Exception: pass
ST = f'{HERE}/screens/{D}/table.json'
T = json.load(open(ST)) if os.path.exists(ST) else None
priced = {os.path.basename(os.path.dirname(p)) for p in glob.glob(f'{HERE}/runs/*/*/result.json')}

h = [f'<html><head><meta charset="utf-8"><title>Weekly Ideas {e(D)}</title></head><body>',
     f'<h1>Weekly Ideas {e(D)}</h1>',
     '<p>Moved here from the Price Targets page on 2026-10-09. Screens set no price target or fair value and feed no '
     'buy; a name marked "full run" gets the full research round only on request. Targets for fully researched names '
     'stay on the Price Targets page.</p>']

if T and T['rows']:
    h.append(f'<h2>Screened names</h2><p>Verdict counts only once the citation check is clean. Revision percentile from '
             f'{e(T.get("revisions_snapshot"))}; options range = 12-month 10th to 90th percentile vs spot (width, not a return call).</p>')
    h.append('<table border="1" cellpadding="4"><tr><th>Ticker</th><th>Verdict</th><th>Multiple vs peers</th>'
             '<th>Growth</th><th>Gap</th><th>Revisions %ile</th><th>Options range</th><th>Next catalyst</th><th>Biggest risk</th><th>Reason</th></tr>')
    for r in T['rows']:
        mult = f'{r["subject"]} vs {r["peer_median"]} ({e(r["metric"])}, {e(r["basis"])}, {r["n_peers"]} peers)' if r.get('subject') is not None else ''
        rng = f'{r["range_10_90"][0]:+.0f}% to {r["range_10_90"][1]:+.0f}%' if r.get('range_10_90') else ''
        cat = r.get('catalyst') or {}; risk = r.get('risk') or {}
        revp = '' if r.get('revision_pct') is None else '%.0f' % (100 * r['revision_pct'])
        h.append(f'<tr><td><b>{e(r["symbol"])}</b></td><td>{e((r["verdict"] or "").replace("_", " "))}</td><td>{e(mult)}</td>'
                 f'<td>{e(r.get("subject_growth"))}</td><td>{e(r.get("gap"))}</td>'
                 f'<td>{revp}</td><td>{rng}</td>'
                 f'<td>{e(cat.get("date"))} {e(cat.get("what"))}</td><td>{e(risk.get("what"))}</td><td>{e(r.get("reason"))}</td></tr>')
    h.append('</table>')

if I:
    h.append(f'<h2>This week\'s ideas ({e(I.get("as_of"))})</h2><p>{e(I.get("summary"))}</p>')
    ideas = [i for i in I.get('ideas', []) if i.get('status') != 'dropped']
    h.append('<table border="1" cellpadding="4"><tr><th>Ticker</th><th>Name</th><th>Sector</th><th>Score</th><th>Status</th><th>Why now</th></tr>')
    for i in ideas:
        h.append(f'<tr><td><b>{e(i["symbol"])}</b></td><td>{e(i.get("name"))}</td><td>{e(i.get("sector"))}</td>'
                 f'<td>{e(i.get("total"))}</td><td>{e(i.get("status"))}{" (priced)" if i["symbol"] in priced else ""}</td>'
                 f'<td>{e(i.get("why_now"))}</td></tr>')
    h.append('</table>')
    for i in ideas:
        sc = ', '.join(f'{k} {v}/5' for k, v in (i.get('scores') or {}).items())
        h.append(f'<h3>{e(i["symbol"])}: {e(i.get("name"))}</h3><p><i>{e(sc)}</i></p><p><b>Thesis.</b> {e(i.get("thesis"))}</p>')
        if i.get('catalysts'):
            h.append('<p><b>Catalysts</b></p><ul>' + ''.join(f'<li>{e(c.get("date"))}: {e(c.get("event"))}</li>' for c in i['catalysts']) + '</ul>')
        if i.get('key_risks'):
            h.append('<p><b>Key risks</b></p><ul>' + ''.join(f'<li>{e(r)}</li>' for r in i['key_risks']) + '</ul>')
        if i.get('fact_check'):
            h.append(f'<p><b>Ranker\'s fact-check.</b> {e(i["fact_check"])}</p>')
        if i.get('sources'):
            h.append('<p><b>Sources</b></p><ul>' + ''.join(_src(u) for u in i['sources'][:10]) + '</ul>')

if scouts:
    h.append('<h2>Scout themes</h2>')
    for sec, th in scouts.items():
        h.append(f'<h3>{e(sec.title())}</h3><ul>' + ''.join(
            f'<li><b>{e(t.get("theme"))}</b> ({e(t.get("stage"))}). {e(t.get("evidence"))}</li>' for t in th) + '</ul>')

if SC.get('sectors'):
    h.append(f'<h2>Quant screen ({e(SC.get("as_of"))})</h2><p>Momentum and Sharpe leaders, and pullbacks, per sector.</p>')
    for sec, v in SC['sectors'].items():
        for k in ('leaders', 'pullbacks'):
            rows = v.get(k) or []
            if not rows: continue
            h.append(f'<h3>{e(sec.title())}: {k}</h3><table border="1" cellpadding="3"><tr><th>Ticker</th><th>Name</th>'
                     '<th>Last</th><th>12-1M mom.</th><th>3M</th><th>Off high</th><th>Vol 1Y</th><th>Score</th></tr>')
            for n in rows:
                h.append(f'<tr><td>{e(n.get("symbol"))}</td><td>{e(n.get("name"))}</td><td>{e(n.get("last"))}</td>'
                         f'<td>{pct(n.get("mom_12_1"))}</td><td>{pct(n.get("mom_3m"))}</td><td>{pct(n.get("off_high"))}</td>'
                         f'<td>{pct(n.get("vol_1y"))}</td><td>{e(n.get("score"))}</td></tr>')
            h.append('</table>')
h.append('</body></html>')
out = f'{HERE}/ideas/{D}/ideas_doc.html'
os.makedirs(os.path.dirname(out), exist_ok=True)
import re
open(out, 'w').write(re.sub(r"\bKilian's\b", "the owner's", re.sub(r'\bKilian\b', 'the owner', '\n'.join(h))))
print(out, os.path.getsize(out))
