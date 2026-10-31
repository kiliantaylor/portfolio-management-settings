"""Build the "Held-Name News" page from the daily news checks (agents/news.md, REFRESH.md step 2c).

usage: python3 news_page.py [RUN_DATE]   -> news.html (publish to the News artifact, see REFRESH.md)
Reads runs/RUN_DATE/SYM/news.json for every name held in data/trades.json; RUN_DATE defaults to the newest run
folder that has one. Keeps a dated history in data/news_history.json so earlier days stay on the page."""
import glob, html, json, os, sys
import fscache; fscache.install()  # 2026-10-21: read-once cache for the shared network folder (fscache.py; same output)
sys.path.insert(0, '/mnt/project-files/adhoc'); from page_search import inject as add_search  # ticker/company search box (Kilian 2026-10-19)
HERE = os.path.dirname(os.path.abspath(__file__))
esc = lambda x: html.escape(str(x if x is not None else ''))

trades = json.load(open(f'{HERE}/data/trades.json'))
trades = trades.get('trades', trades) if isinstance(trades, dict) else trades
pos = {}
for t in trades:
    pos[t['symbol']] = pos.get(t['symbol'], 0) + (t['shares'] if t.get('side', 'buy') == 'buy' else -t['shares'])
held = sorted(s for s, n in pos.items() if n > 0)

dates = sorted({p.split('/')[-3] for p in glob.glob(f'{HERE}/runs/*/*/news.json')})  # every check so far, keyed by its run date
hist_p = f'{HERE}/data/news_history.json'
hist = {}  # rebuilt from the run folders each time
for d in dates:
    for s in held:
        p = f'{HERE}/runs/{d}/{s}/news.json'
        if os.path.exists(p):
            n = json.load(open(p))
            hist.setdefault(d, {})[s] = n
json.dump(hist, open(hist_p, 'w'), indent=1)

RANK = {'material': 0, 'minor': 1, 'none': 2}
def card(n):
    v = n.get('verdict', 'none')
    items = ''.join(
        f'<li><span class="d">{esc(i.get("date"))}</span> <a href="{esc(i.get("url"))}" target="_blank" rel="noopener">{esc(i.get("headline"))}</a>'
        f' <span class="tag {esc(i.get("materiality"))}">{esc(i.get("materiality"))}</span>'
        f'{"" if i.get("verified") else " <span class=tag>unverified</span>"}<p>{esc(i.get("why"))}</p></li>'
        for i in sorted(n.get('items') or [], key=lambda i: (RANK.get(i.get('materiality'), 3), i.get('date') or '')))
    fv = ''
    if v == 'material':
        fv = (f'<p class="fv"><b>Effect on research target (12m):</b> {n.get("fair_value_change_pct") if n.get("fair_value_change_pct") is not None else "–"}%'
              f'{" · " + esc(n.get("fair_value_reasoning")) if n.get("fair_value_reasoning") else ""}</p>'
              f'{"<p class=fv><b>What it hits:</b> " + esc(n.get("breaks")) + "</p>" if n.get("breaks") else ""}')
    return (f'<details class="card {esc(v)}"{" open" if v == "material" else ""}><summary><span class="sym">{esc(n.get("symbol"))}</span>'
            f'<span class="pill {esc(v)}">{esc(v)}</span><span class="win">{esc(n.get("window_from"))} to {esc(n.get("as_of"))}</span>'
            f'<span class="sum">{esc(n.get("summary"))}</span></summary>{fv}<ul>{items}</ul></details>')

# Insider-sale warning (Kilian 2026-10-19 card "Show warning"; display only): data/insider_flags.json from insider_flags.py
def insider_block():
    p = f'{HERE}/data/insider_flags.json'
    if not os.path.exists(p): return ''
    try:
        F = json.load(open(p)); from insider_flags import line
    except Exception as e:
        return f'<section class="ins"><h2>Insider sales</h2><p class="lede">insider_flags.json unreadable: {esc(e)}</p></section>'
    rows, quiet, failed = '', [], []
    for s in held:
        if s in (F.get('errors') or {}): failed.append(s); continue
        es = F.get(s)
        if es is None: failed.append(s); continue
        if not es: quiet.append(s); continue
        rows += ''.join(f'<li><span class="sym">{esc(s)}</span> {esc(line(s, e))} <span class="d">(filed {esc(e["filed"])})</span> '
                        f'<a href="{esc(e["accession_url"])}" target="_blank" rel="noopener">Form 4</a></li>' for e in es)
    tail = (f'No such sales for {esc(", ".join(quiet))}. ' if quiet else '') + (f'Not checked (EDGAR error or new name): {esc(", ".join(failed))}. ' if failed else '')
    return (f'<section class="ins"><h2>Insider sales <small>Form 4s filed {esc(F.get("window_days", 90))} days to {esc(F.get("as_of"))}</small></h2>'
            f'<p class="lede">Open-market sales by a CEO, CFO or director with the Form 4 10b5-1 box unchecked (not under a pre-set trading plan).</p>'
            f'{"<ul>" + rows + "</ul>" if rows else ""}<p class="lede">{tail}'
            'In the backtest (SEC insider data, liquid names, 2023 to Mar 2026) such sales were followed by about &minus;1.4% vs SPY over 3 months '
            'and &minus;4.3% over 12 months per trade; planned sales showed nothing. A warning only: sizing is not changed.</p></section>')
ins = insider_block()

days = ''
for d in sorted(hist, reverse=True):
    ns = sorted(hist[d].values(), key=lambda n: (RANK.get(n.get('verdict'), 3), n.get('symbol')))
    mat = [n['symbol'] for n in ns if n.get('verdict') == 'material']
    head = f'{len(mat)} material: {", ".join(mat)}' if mat else 'nothing material'
    checked = max((n.get('as_of') or d) for n in ns)
    days += f'<section><h2>Close of {esc(d)} <small>checked {esc(checked)} · {esc(head)}</small></h2>{"".join(card(n) for n in ns)}</section>'

page = f'''<title>Held-Name News</title>
<style>
/* single column of dated sections; one expandable card per held name, material first */
:root{{--bg:#f3f5f4;--panel:#ffffff;--fg:#17201c;--muted:#5d6a64;--line:#d9e0dc;--accent:#1f6f5c;
--mat:#b3401f;--mat-bg:#fbe9e3;--min:#7a5a00;--min-bg:#f7efd6;--none-bg:#e6ece9;
--f-body:"IBM Plex Sans",system-ui,sans-serif;--f-mono:"IBM Plex Mono",ui-monospace,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#111614;--panel:#18201d;--fg:#dde5e1;--muted:#93a29b;--line:#2a3531;--accent:#5fbfa3;--mat:#f08a6a;--mat-bg:#3a1f17;--min:#e3c15c;--min-bg:#352d14;--none-bg:#24302b;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#111614;--panel:#18201d;--fg:#dde5e1;--muted:#93a29b;--line:#2a3531;--accent:#5fbfa3;--mat:#f08a6a;--mat-bg:#3a1f17;--min:#e3c15c;--min-bg:#352d14;--none-bg:#24302b;color-scheme:dark}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 var(--f-body);padding:28px 16px 48px}}
main{{max-width:860px;margin:0 auto;display:grid;gap:28px}}
h1{{font-size:24px;margin:0;text-wrap:balance}} .lede{{color:var(--muted);margin:6px 0 0;max-width:65ch}}
h2{{font:600 15px var(--f-mono);margin:0 0 10px}} h2 small{{color:var(--muted);font-weight:400;margin-left:8px}}
section{{display:grid;gap:8px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:6px}} .card.material{{border-color:var(--mat)}}
summary{{cursor:pointer;padding:12px 14px;display:grid;grid-template-columns:auto auto 1fr;gap:4px 10px;align-items:center}}
summary:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.sym{{font:600 15px var(--f-mono)}} .win{{color:var(--muted);font:12px var(--f-mono);justify-self:end}}
.sum{{grid-column:1/-1;min-width:0}}
.pill,.tag{{font:600 11px var(--f-mono);text-transform:uppercase;letter-spacing:.05em;padding:2px 7px;border-radius:999px;background:var(--none-bg);color:var(--muted)}}
.pill.material,.tag.material{{background:var(--mat-bg);color:var(--mat)}} .pill.minor,.tag.minor{{background:var(--min-bg);color:var(--min)}}
.fv{{margin:0 14px 8px}} ul{{margin:0;padding:0 14px 12px 32px;display:grid;gap:10px}} li p{{margin:4px 0 0;color:var(--muted)}}
.d{{font:12px var(--f-mono);color:var(--muted)}} a{{color:var(--accent)}}
.ins{{background:var(--panel);border:1px solid var(--min);border-radius:6px;padding:12px 14px}} .ins ul{{padding:0 0 8px 18px;gap:6px}} .ins .sym{{font-size:13px;margin-right:4px}}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap">
<main><header><h1>Held-Name News</h1><p class="lede">Weekday news check on each held name, newest day first. Held: {esc(", ".join(held))}. <a href="https://claude.ai/artifact/8kFbwWAfTBNuBqtrT8ZUCi" target="_blank" rel="noopener">How it works</a></p></header>{ins}{days}</main>'''
open(f'{HERE}/news.html', 'w').write(add_search(page))
print('news.html', len(page), 'days', len(hist), 'held', held)
