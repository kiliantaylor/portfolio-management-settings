"""Ticker / company-name search box for the published pages (Kilian 2026-10-19: "add a search functionality to each
page? it should work for the ticker abbreviation or the full company name").

inject(html) puts a small search box in the top-right corner of a page, plus a ticker -> company-name map. Typing a
ticker or any part of a company name lists the matching names that appear on the page; picking one jumps to its first
mention (opening a closed tab or <details> on the way) and the arrows step through every mention. Names come from
valuation/data/cik_map.json (SEC company names), plus a few everyday aliases. Used by build_report.py (Price Targets),
news_page.py (Held-Name News), calendar/build_calendar.py (Catalyst Calendar) and portfolio/build_v6.py (dashboard).
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
CIK = os.path.join(HERE, '..', 'valuation', 'data', 'cik_map.json')
ALIASES = {'GOOGL': ['Google'], 'GOOG': ['Google'], 'META': ['Facebook'], 'BKNG': ['Priceline'], 'TSM': ['TSMC'],
           'ASML': ['ASML'], 'NVDA': ['Nvidia'], 'AVGO': ['Broadcom'], 'AMZN': ['Amazon', 'AWS'], 'MSFT': ['Microsoft'],
           'CRWV': ['CoreWeave'], 'NBIS': ['Nebius'], 'CBRS': ['Cerebras'], 'VST': ['Vistra'], 'APP': ['AppLovin'],
           'NFLX': ['Netflix'], 'AMAT': ['Applied Materials'], 'MOD': ['Modine'], 'TJX': ['TJ Maxx', 'Marshalls']}
SMALL = {'and', 'of', 'the', 'for', 'de', 'in', 'on'}


def tidy(name):
    """SEC names are often all caps ('APPLIED OPTOELECTRONICS, INC.'); title-case those, keep mixed case as filed."""
    name = re.sub(r'\s+', ' ', (name or '').strip()).rstrip('/').strip()
    name = re.sub(r'\s*/[A-Z]{2}/?$', '', name)  # 'APPLE INC /DE/' style state suffixes
    if name.upper() != name: return name
    words = []
    for i, w in enumerate(name.lower().split(' ')):
        words.append(w if (i and w in SMALL) else w[:1].upper() + w[1:])
    out = ' '.join(words)
    return re.sub(r'\b(Inc|Corp|Co|Ltd|Plc|Nv|Sa|Ag|Se|Llc|Lp)\b\.?', lambda m: {'Nv': 'N.V.', 'Sa': 'S.A.', 'Ag': 'AG', 'Se': 'SE', 'Plc': 'plc', 'Llc': 'LLC', 'Lp': 'LP'}.get(m.group(1), m.group(1) + '.'), out)


def names():
    try: raw = json.load(open(CIK))
    except (OSError, ValueError): raw = {}
    out = {s: [tidy(v.get('name') if isinstance(v, dict) else v)] for s, v in raw.items()}
    for s, a in ALIASES.items(): out.setdefault(s, [s]); out[s] += [x for x in a if x not in out[s]]
    return out


CSS = """
#psx{position:relative;z-index:2147483000;margin:8px 0;
  font:14px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--psx-bg:#fff;--psx-fg:#17202b;--psx-mute:#5d6875;
  --psx-line:#cfd6de;--psx-acc:#1d5ca6;--psx-acc-soft:#e3edf9;--psx-hit:#f2b705}
@media (prefers-color-scheme:dark){#psx{--psx-bg:#1a212b;--psx-fg:#e4e9ef;--psx-mute:#9aa6b4;--psx-line:#344050;--psx-acc:#82b4ef;--psx-acc-soft:#22324a}}
#psx [hidden]{display:none!important}
@media (min-width:900px){#psx{position:fixed;margin:0;top:max(8px,env(safe-area-inset-top,0px));right:max(8px,env(safe-area-inset-right,0px))}}
#psx ul{position:absolute;right:0;left:0}
@media (min-width:900px){#psx ul{left:auto;width:340px}}
#psx .psx-bar{display:flex;align-items:center;gap:4px;background:var(--psx-bg);color:var(--psx-fg);border:1px solid var(--psx-line);
  border-radius:10px;padding:4px 4px 4px 10px;box-shadow:0 4px 14px rgba(0,0,0,.14)}
#psx input{flex:1;min-width:0;width:200px;border:0;outline:0;background:transparent;color:inherit;font:inherit;padding:6px 0}
#psx input::placeholder{color:var(--psx-mute)}
#psx button{border:0;background:transparent;color:var(--psx-fg);font:inherit;min-width:30px;height:30px;border-radius:7px;cursor:pointer}
#psx button:hover,#psx button:focus-visible{background:var(--psx-acc-soft);outline:none}
#psx button:disabled{opacity:.35;cursor:default;background:transparent}
#psx .psx-n{font-size:12px;color:var(--psx-mute);min-width:3.2em;text-align:center;font-variant-numeric:tabular-nums}
#psx ul{list-style:none;margin:4px 0 0;padding:4px;max-height:min(60vh,360px);overflow:auto;background:var(--psx-bg);color:var(--psx-fg);
  border:1px solid var(--psx-line);border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.18)}
#psx li{display:flex;gap:10px;align-items:baseline;padding:7px 9px;border-radius:7px;cursor:pointer}
#psx li b{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;min-width:4.2em}
#psx li span{color:var(--psx-mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#psx li[aria-selected=true]{background:var(--psx-acc-soft)}
#psx li.psx-none{color:var(--psx-mute);cursor:default}
.psx-hit{outline:2px solid var(--psx-hit)!important;outline-offset:2px;border-radius:3px;scroll-margin:80px}
.psx-cur{outline:3px solid var(--psx-hit)!important;background-color:rgba(242,183,5,.18)!important}
@media print{#psx{display:none}}
"""

JS = r"""
(() => {
  const NAMES = __PSX_NAMES__;
  const box = document.createElement('div'); box.id = 'psx';
  box.innerHTML = '<div class="psx-bar" role="search"><input type="search" placeholder="Search ticker or company" aria-label="Search by ticker or company name" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="false" aria-controls="psx-list"><span class="psx-n" aria-live="polite"></span><button type="button" class="psx-prev" aria-label="Previous mention" disabled>&#9650;</button><button type="button" class="psx-next" aria-label="Next mention" disabled>&#9660;</button></div><ul id="psx-list" role="listbox" hidden></ul>';
  document.body.prepend(box);
  const inp = box.querySelector('input'), list = box.querySelector('ul'), cnt = box.querySelector('.psx-n');
  const prev = box.querySelector('.psx-prev'), next = box.querySelector('.psx-next');
  const norm = s => (s || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
  let opts = [], sel = -1, hits = [], at = -1, sym = null;

  function textNodes() {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {acceptNode: n =>
      (!n.nodeValue.trim() || n.parentElement.closest('#psx,script,style,noscript,template,title')) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
    const out = []; while (w.nextNode()) out.push(w.currentNode); return out;
  }
  function onPage() {  // tickers in the map that the page mentions, as whole uppercase words
    const seen = new Set(), re = /\b[A-Z][A-Z0-9.]{0,5}\b/g;
    for (const n of textNodes()) for (const m of n.nodeValue.matchAll(re)) if (NAMES[m[0]]) seen.add(m[0]);
    return seen;
  }
  function search(q) {
    const nq = norm(q), uq = q.trim().toUpperCase(); if (!nq) return [];
    const seen = onPage(), res = [];
    for (const s of seen) {
      const nm = NAMES[s] || [];
      let score = s === uq ? 0 : s.startsWith(uq) ? 1 : 9;
      for (const a of nm) { const na = norm(a); if (na.startsWith(nq)) score = Math.min(score, 2); else if ((' ' + na).includes(' ' + nq)) score = Math.min(score, 3); else if (na.includes(nq)) score = Math.min(score, 4); }
      if (score < 9) res.push([score, s, nm[0] || '']);
    }
    return res.sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1])).slice(0, 12);
  }
  function show(q) {
    opts = search(q); sel = opts.length ? 0 : -1;
    list.innerHTML = opts.length ? opts.map(([, s, n], i) => `<li role="option" id="psx-o${i}" aria-selected="${i === sel}" data-s="${s}"><b>${s}</b><span>${n.replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c])}</span></li>`).join('')
      : (q.trim() ? '<li class="psx-none">Not on this page</li>' : '');
    list.hidden = !list.innerHTML; inp.setAttribute('aria-expanded', String(!list.hidden));
  }
  function mark(i) { sel = i; [...list.children].forEach((li, k) => li.setAttribute('aria-selected', String(k === i))); const li = list.children[i]; if (li) li.scrollIntoView({block: 'nearest'}); }
  function clear() { document.querySelectorAll('.psx-hit,.psx-cur').forEach(e => e.classList.remove('psx-hit', 'psx-cur')); hits = []; at = -1; cnt.textContent = ''; prev.disabled = next.disabled = true; }
  function find(s) {
    const nm = (NAMES[s] || []).filter(a => a.length > 3 && a !== s).map(a => a.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const reT = new RegExp(`(^|[^A-Za-z0-9])${s.replace('.', '\\.')}(?![A-Za-z0-9])`), reN = nm.length ? new RegExp(nm.join('|'), 'i') : null;
    const out = [];
    for (const n of textNodes()) {
      const v = n.nodeValue; if (!(reT.test(v) || (reN && reN.test(v)))) continue;
      const el = n.parentElement;  // the innermost element holding the mention
      if (!out.includes(el)) out.push(el);
    }
    return out.filter(e => !out.some(o => o !== e && e.contains(o)));
  }
  function reveal(el) {  // open closed <details> and switch to the tab holding el
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (p.tagName === 'DETAILS' && !p.open && !(el.tagName === 'SUMMARY' && el.parentElement === p)) p.open = true;
      if (p.hidden || p.getAttribute('aria-hidden') === 'true' || getComputedStyle(p).display === 'none') {
        const id = p.id, lab = p.getAttribute('aria-labelledby');
        let t = (lab && document.getElementById(lab)) || (id && document.querySelector(`[aria-controls="${id}"]`));
        if (!t && id) t = [...document.querySelectorAll('[role=tab],[data-tab],[data-t]')].find(b => { const v = b.dataset.tab || b.dataset.t; return v && (id === v || id.endsWith('-' + v)); });
        if (t) t.click();
        else if (p.hidden) {  // a collapsed row or block: click the nearest visible header before it (group row, toggle), else just show it
          let q = p.previousElementSibling; while (q && q.hidden) q = q.previousElementSibling;
          if (q && (q.getAttribute('aria-expanded') === 'false' || q.matches('.grp,[data-g],[role=button],summary'))) q.click();
          if (p.hidden) p.hidden = false;
        }
      }
    }
  }
  function visible(el) { return el.getClientRects().length > 0; }
  function go(k) {
    if (!hits.length) return;
    at = (k + hits.length) % hits.length; const el = hits[at];
    reveal(el);
    document.querySelectorAll('.psx-cur').forEach(e => e.classList.remove('psx-cur')); el.classList.add('psx-cur');
    if (visible(el)) el.scrollIntoView({behavior: 'smooth', block: 'center'});
    cnt.textContent = `${at + 1} of ${hits.length}`;
  }
  function pick(s) {
    clear(); sym = s; list.hidden = true; inp.setAttribute('aria-expanded', 'false');
    inp.value = s + (NAMES[s] && NAMES[s][0] ? ' \u00b7 ' + NAMES[s][0] : '');
    hits = find(s);
    if (!hits.length) { cnt.textContent = '0 found'; return; }
    // start at the name's own heading or row (text that opens with the ticker or name), not a passing mention
    const lead = new RegExp('^\\W{0,3}(' + [s, ...(NAMES[s] || [])].map(a => a.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')(?![A-Za-z0-9])', 'i');
    let k = hits.findIndex(e => lead.test(e.textContent.trim()) && /^(H[1-5]|SUMMARY|TH|TD|DT|BUTTON|B|STRONG|SPAN|CODE)$/.test(e.tagName));
    if (k < 0) k = hits.findIndex(e => lead.test(e.textContent.trim()));
    if (k > 0) hits = hits.slice(k).concat(hits.slice(0, k));
    hits.forEach(e => e.classList.add('psx-hit')); prev.disabled = next.disabled = hits.length < 2;
    go(0);
  }
  inp.addEventListener('input', () => { clear(); sym = null; show(inp.value); });
  inp.addEventListener('focus', () => { if (!sym && inp.value) show(inp.value); });
  inp.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' && !list.hidden && opts.length) { e.preventDefault(); mark(Math.min(sel + 1, opts.length - 1)); }
    else if (e.key === 'ArrowUp' && !list.hidden && opts.length) { e.preventDefault(); mark(Math.max(sel - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (!list.hidden && sel >= 0) pick(opts[sel][1]); else if (hits.length) go(at + (e.shiftKey ? -1 : 1)); else { const o = search(inp.value); if (o.length) pick(o[0][1]); } }
    else if (e.key === 'Escape') { inp.value = ''; sym = null; clear(); list.hidden = true; inp.setAttribute('aria-expanded', 'false'); }
  });
  list.addEventListener('mousedown', e => { const li = e.target.closest('li[data-s]'); if (li) { e.preventDefault(); pick(li.dataset.s); } });
  document.addEventListener('mousedown', e => { if (!box.contains(e.target)) { list.hidden = true; inp.setAttribute('aria-expanded', 'false'); } });
  prev.addEventListener('click', () => go(at - 1)); next.addEventListener('click', () => go(at + 1));
  document.addEventListener('keydown', e => { if ((e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) || ((e.ctrlKey || e.metaKey) && e.key === 'k')) { e.preventDefault(); inp.focus(); inp.select(); } });
})();
"""


def inject(html):
    """Add the search box to a full page; idempotent (an already-injected page is returned unchanged)."""
    if 'id="psx-style"' in html: return html
    js = JS.replace('__PSX_NAMES__', json.dumps(names(), separators=(',', ':')).replace('</', '<\\/'))
    snippet = f'<style id="psx-style">{CSS}</style>\n<script>{js}</script>\n'
    i = html.lower().rfind('</body>')
    return html[:i] + snippet + html[i:] if i >= 0 else html + snippet


if __name__ == '__main__':  # python3 page_search.py IN.html OUT.html
    import sys
    open(sys.argv[2], 'w').write(inject(open(sys.argv[1]).read()))
