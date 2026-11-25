"""Timeliness screen for forecast sources in the citation gate (Kilian 2026-11-17, DY: "constructconnect is clearly outdated get
something else, do we need to add a timeliness screen to the citation gate so you stop doing that too").

What went wrong: the DY gate passed ConstructConnect's Spring 2026 construction forecast in a November 2026 round. The page said
what the set claimed, so the old gate (does the page say it?) was satisfied, but Census actuals had already overtaken the
forecast (its +14.8% 2027 on the CY2026 total sat ~8% below the Sep 2026 run-rate). The source's `date` was the day the agent
opened it (2026-11-17), not the day it was published, so nothing looked old.

The rule (valuation/RULES.md rule 23): a forecast source is STALE when
  (a) it is older, by its PUBLICATION date, than the window for its kind (WINDOW_DAYS below), or
  (b) actual data for the series it forecasts came out after it was published (`actuals.released` > `published`).
A stale source is refused unless its `timeliness` entry shows one of:
  - newer_forecast: [src ids] of a newer forecast of the same series, itself not stale, that the set now cites; or
  - none_newer: {"searched": "YYYY-MM-DD" (within SEARCH_MAX_AGE days of the gate), "queries": [>= 2 searches],
                 "checked": [publishers / editions looked at]} AND, when (b) applies or the source is past its window,
    "reconciled": how the set's path compares with the actuals released since the forecast (a number, not "in line").
A forecast source with no publication date anywhere is refused too (`no_pub_date`).

Source fields (supervisor.json `sources`, forecaster `sources`):
  {"title", "url", "date" (the day it was opened, as before), "published": "YYYY-MM-DD" (the date the page or report states;
   month or season editions: the first day of that month / season), "kind": one of KINDS,
   "actuals": {"series": "", "through": "YYYY-MM", "released": "YYYY-MM-DD", "src": N} (when the forecast series has public
   actuals, e.g. Census put-in-place, EIA), "timeliness": {...clearance above...}}
Company guidance is not screened here (guidance_check carries guide_as_of and is re-checked each print); company filings,
transcripts, releases and our own agent files are not forecast sources.

Usage:
  python3 timeliness.py check SET_OR_SUPERVISOR.json [SUPERVISOR.json] [--date D]  flags for one set (driver-set gates, GATE.md)
  python3 timeliness.py scan [--date D]   every live driver-model set -> data/timeliness_scan.json (+ printed table)
"""
import datetime, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', f'{HERE}/runs')
sys.path.insert(0, HERE)

# Max age by kind, in days from publication to the gate. Set so the screen fires once a publisher's usual next edition is due:
# consensus/aggregator pages move daily (21 = the gate's existing re-open rule in agents/citation_gate.md); sell-side notes are
# refreshed around each print (90); industry forecasters (ConstructConnect, Dodge, AIA, Gartner, IDC, TrendForce, SemiAnalysis)
# publish quarterly or semiannually (120 = one quarter + a month's slack); agencies (EIA STEO monthly, IEA/IMF annual) 180;
# long-range studies (multi-year market sizing, academic) 365. The window only starts the search: what clears a stale source
# is a newer forecast, or a shown search that there is none.
WINDOW_DAYS = {'consensus': 21, 'sell_side': 90, 'industry_forecast': 120, 'agency_forecast': 180, 'long_range_study': 365}
KINDS = list(WINDOW_DAYS) + ['not_forecast']
SEARCH_MAX_AGE = 7

FORECAST_WORDS = re.compile(r'\b(forecast(s|ed|ing)?|outlook|projections?|projected|estimates?|consensus|to reach|cagr|'
                            r'predicts?|prediction|price target|tam|market size|market sizing)\b', re.I)
NOT_FORECAST_URL = re.compile(r'sec\.gov/|/mnt/project-files|^file:|\.gcs-web\.com|investor\.|/investors?/|ir\.', re.I)
NOT_FORECAST_TITLE = re.compile(r'\b(10-[KQ]|8-K|20-F|6-K|earnings release|ex\.?\s*99|transcript|press release|proxy|'
                                r'annual report|outlook expectations summary|agent \d|forecast file|prepared remarks|'
                                r'shareholder letter|investor presentation|slides|earnings call|results presentation|ir site|own computation|'
                                r'our (own )?(computation|build|calculation)|chart feed|price history|cfo|ceo|management|'
                                r'quarter ended|topped|earnings surprise|beats? estimates|miss(es|ed)? estimates|news release)\b', re.I)
CONSENSUS_URL = re.compile(r'nasdaq\.com/api/analyst|stockanalysis\.com|zacks\.com|marketbeat|tipranks|finance\.yahoo|'
                           r'polymarket|kalshi|marketscreener|seekingalpha\.com/symbol|wsj\.com/market-data|morningstar', re.I)
SELL_SIDE = re.compile(r'\b(goldman|morgan stanley|jpmorgan|jp morgan|j\.p\. morgan|bernstein|ubs|bofa|bank of america|citi(group)?|'
                       r'jefferies|barclays|wells fargo|evercore|mizuho|deutsche bank|cowen|td cowen|piper|raymond james|'
                       r'baird|stifel|keybanc|rbc|bmo|truist|needham|oppenheimer|cantor|wolfe|melius|hsbc|nomura|macquarie|'
                       r'sell-side|analyst note|broker)\b', re.I)
AGENCY = re.compile(r'\b(eia|steo|iea|imf|oecd|cbo|federal reserve|fomc|usda|world bank|bls|census bureau projection|'
                    r'nerc|ferc|doe)\b', re.I)
LONG_RANGE = re.compile(r'\b(20[3-5]\d)\b.*\b(market size|tam|cagr|by 20[3-5]\d)\b|\bby 20[3-5]\d\b', re.I)

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
SEASONS = {'winter': 1, 'spring': 3, 'summer': 6, 'fall': 9, 'autumn': 9}


def _d(s):
    try: return datetime.date.fromisoformat(str(s)[:10])
    except Exception:
        try: return datetime.date.fromisoformat(str(s)[:7] + '-01')
        except Exception: return None


def kind_of(src):
    """The source's kind: the `kind` field when given, else a guess from title + URL (legacy sets; new sources carry `kind`)."""
    k = (src.get('kind') or '').strip()
    if k in KINDS: return k, 'given'
    t, u = src.get('title') or '', src.get('url') or ''
    if NOT_FORECAST_URL.search(u) or NOT_FORECAST_TITLE.search(t): return 'not_forecast', 'guess'
    if not FORECAST_WORDS.search(t): return 'not_forecast', 'guess'
    if CONSENSUS_URL.search(u): return 'consensus', 'guess'
    if re.search(r'\bconsensus\b', t, re.I) and not re.search(r'construction|industry|panel', t, re.I): return 'consensus', 'guess'
    if SELL_SIDE.search(t): return 'sell_side', 'guess'
    if AGENCY.search(t): return 'agency_forecast', 'guess'
    if LONG_RANGE.search(t): return 'long_range_study', 'guess'
    return 'industry_forecast', 'guess'


def published_of(src, agent_date=None, consensus=False):
    """(date, how): the gate checker's page date first, then the source's `published`, then a date in its title. Never the
    `date` field (that is the day the agent opened the page: DY's ConstructConnect Spring 2026 carried 2026-11-17)."""
    if agent_date and _d(agent_date): return _d(agent_date), 'gate checker (page)'
    if src.get('published') and _d(src['published']): return _d(src['published']), 'published field'
    t = src.get('title') or ''
    full = [_d(x) for x in re.findall(r'\b(20\d\d-\d\d-\d\d)\b', t)]
    full = [x for x in full if x]
    if full: return min(full), 'title date'
    m = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d\d)\b', t, re.I)
    if m: return datetime.date(int(m.group(2)), MONTHS[m.group(1).lower()[:3]], 1), 'title month'
    m = re.search(r'\b(winter|spring|summer|fall|autumn)\s+(20\d\d)\b', t, re.I)
    if m: return datetime.date(int(m.group(2)), SEASONS[m.group(1).lower()], 1), 'title season'
    m = re.search(r'\b(20\d\d)-(\d\d)\b', t)
    if m and 1 <= int(m.group(2)) <= 12: return datetime.date(int(m.group(1)), int(m.group(2)), 1), 'title month'
    if consensus and _d(src.get('date')):  # a consensus page shows the figures of the day it was opened
        return _d(src['date']), 'date opened (consensus)'
    return None, 'none'


def screen(src, gate_date, sources=None, agent=None):
    """One source -> None (not a forecast, or fresh / cleared) or a dict with the reason it is refused.
    agent: the gate checker's record for this URL {published, newer_found, ...} (shard results)."""
    agent = agent or {}
    kind, how = kind_of(src)
    if agent.get('source_kind') in KINDS: kind, how = agent['source_kind'], 'gate checker'
    if kind == 'not_forecast': return None
    gd = _d(gate_date) or datetime.date.today()
    pub, phow = published_of(src, agent.get('published'), kind == 'consensus')
    base = dict(title=(src.get('title') or '')[:200], url=src.get('url'), kind=kind, kind_from=how,
                published=pub.isoformat() if pub else None, published_from=phow, window_days=WINDOW_DAYS[kind])
    if not pub:
        od = _d(src.get('date'))
        if od and (gd - od).days > WINDOW_DAYS[kind]:  # opened that long ago, so published at least that long ago
            pub, phow = od, 'date opened (published no later)'
            base.update(published=pub.isoformat(), published_from=phow)
        else:
            return dict(base, result='no_pub_date', why='forecast source with no publication date: record `published` from the page')
    age = (gd - pub).days
    reasons = []
    if age > WINDOW_DAYS[kind]: reasons.append(f'{age} days old, window for {kind} is {WINDOW_DAYS[kind]}')
    act = src.get('actuals') or {}
    rel = _d(act.get('released')) or _d(agent.get('actuals_released'))
    overtaken = bool(rel and rel > pub)
    if overtaken:
        reasons.append(f"actuals for {act.get('series') or 'its series'} through {act.get('through') or agent.get('actuals_through') or '?'} "
                       f"came out {rel.isoformat()}, after it was published")
    if agent.get('newer_found'):
        reasons.append(f"gate checker found a newer forecast: {str(agent['newer_found'])[:200]}")
    if not reasons: return None
    base.update(age_days=age, reasons=reasons)
    c = src.get('timeliness') or {}
    by_id = {i: s for i, s in enumerate(sources or [], 1)}
    newer = [by_id.get(i) for i in (c.get('newer_forecast') or []) if isinstance(i, int)]
    if newer and all(newer) and not agent.get('newer_found'):
        bad = [n for n in newer if screen(dict(n, timeliness=None), gate_date) is not None or
               (published_of(n)[0] or datetime.date.min) <= pub]
        if not bad: return None
        return dict(base, result='stale_forecast', why='the newer forecast it names is itself stale, undated or not newer')
    nn = c.get('none_newer') or {}
    if nn and not agent.get('newer_found'):
        sd = _d(nn.get('searched'))
        gaps = []
        if not sd or (gd - sd).days > SEARCH_MAX_AGE or sd > gd: gaps.append(f'search date missing or more than {SEARCH_MAX_AGE} days before the gate')
        if len([q for q in nn.get('queries') or [] if str(q).strip()]) < 2: gaps.append('fewer than two searches shown')
        if not (nn.get('checked') or []): gaps.append('no publishers / editions listed as checked')
        if overtaken and not re.search(r'\d', str(c.get('reconciled') or '')):
            gaps.append('no reconciliation with the actuals released since publication (needs numbers)')
        if not gaps: return None
        return dict(base, result='stale_forecast', why='none-newer search incomplete: ' + '; '.join(gaps))
    return dict(base, result='stale_forecast',
                why='stale forecast: cite a newer forecast of the same series (timeliness.newer_forecast) or show the search that '
                    'none exists (timeliness.none_newer + reconciled against the actuals), then re-argue any value it supported')


def cited_ids(obj):
    """Source ids a set actually uses: every source_ids / *_source_ids list and every 'src N' in its text."""
    import run_digest
    ids, txt = set(), json.dumps(obj)
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.endswith('source_ids') and isinstance(v, list): ids.update(i for i in v if isinstance(i, int))
                else: walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(obj)
    ids.update(run_digest.src_ids(txt.replace('\\"', '"')))
    return ids


def used_refs(body):
    """(source ids, text) a set uses now. The gate-fix log (citation_audit.corrections) is history: a source it names counts
    only when the newest correction naming it still uses it (names it outside `from`). Kilian 2026-11-23: CRDO round 3
    re-opened src 57 for its date after round 2 moved every cite to src 114, because old corrections still named it."""
    import copy, run_digest
    b = copy.deepcopy(body); ca = b.get('citation_audit') if isinstance(b, dict) else None
    cor = ca.pop('corrections', None) if isinstance(ca, dict) else None
    ids, txt = cited_ids(b), json.dumps(b)
    if not isinstance(cor, list): return ids, txt
    last_ids, last_urls = {}, {}
    for c in cor:
        if not isinstance(c, dict): c = dict(note=c)
        rest = {k: v for k, v in c.items() if k != 'from'}; frm = json.dumps(c.get('from')).replace('\\"', '"')
        r_ids, r_txt = cited_ids(rest), json.dumps(rest)
        for i in r_ids | set(run_digest.src_ids(frm)): last_ids[i] = i in r_ids
        for u in re.findall(r'(?:https?://|/mnt/|runs/|data/)[^\s"\'<>)\]]+', r_txt + ' ' + frm): last_urls[u] = u in r_txt
    ids |= {i for i, live in last_ids.items() if live}
    txt += ' ' + ' '.join(u for u, live in last_urls.items() if live)
    return ids, txt


def check_set(obj, sources, gate_date, scope_urls=None, agent_by_url=None):
    """Flags for the sources a set uses anywhere: reconciled inputs, driver_build notes, cards and their options, judgment
    alternatives, gate corrections (DY 11-17: ConstructConnect entered through a gate-fix correction and a driver_build note,
    places the figure gate never opened). obj: the whole supervisor.json or set (its `sources` key is skipped).
    Consensus pages are screened only when their URL is in scope_urls (the gate's rows in scope; None = all): they are
    re-pulled with the peer table at each update, while an outside forecast anywhere in the set is screened every gate."""
    agent_by_url = agent_by_url or {}
    body = {k: v for k, v in obj.items() if k != 'sources'} if isinstance(obj, dict) else obj
    ids, txt = used_refs(body)
    out = []
    for i, s in enumerate(sources or [], 1):
        if not (i in ids or (s.get('url') and s['url'] in txt)): continue
        a = agent_by_url.get(s.get('url'))
        k = (a or {}).get('source_kind') or kind_of(s)[0]
        if k == 'consensus' and scope_urls is not None and s.get('url') not in scope_urls: continue
        f = screen(s, gate_date, sources, a)
        if f: out.append(dict(f, src=i))
    return out


def gate_rows(flags, origin='timeliness.py (script)'):
    """Flags -> citation-gate result rows (failures in gate-merge)."""
    return [dict(origin=origin, item=f"src {f['src']} {f['title'][:120]}", value=f"published {f['published'] or '?'} ({f['published_from']}), {f['kind']}",
                 basis='forecast', source_url=f['url'], result=f['result'], checked_by='timeliness.py (script)',
                 page_shows='; '.join(f.get('reasons') or []), note=f['why']) for f in flags]


def _load_set(p, sp=None):
    st = json.load(open(p))
    if 'model' in st and 'sources' in st:  # a supervisor.json: the whole file (cards, alternatives, corrections too)
        return st, st.get('sources') or [], p
    RI = st
    if not sp and st.get('live_round'):
        sym = st.get('symbol') or os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(p))))
        sp = f"{RUNS}/{st['live_round']}/{sym}/supervisor.json"
    S = json.load(open(sp)) if sp and os.path.exists(sp) else {}
    if not st.get('sources') and S: RI = dict(S, model=None, driver_set=st)  # the set plus the round's cards and alternatives
    return RI, st.get('sources') or S.get('sources') or [], sp


def live_supervisor(sym, ra):
    p = f'{RUNS}/{ra}/{sym}/supervisor.json'
    return p if os.path.exists(p) else None


def scan(gate_date):
    import driver_names
    rows, missing = [], []
    for sym, ra in driver_names.names(gate_date).items():
        sp = live_supervisor(sym, ra)
        if not sp: missing.append(f'{sym} ({ra}: no supervisor.json)'); continue
        RI, src, _ = _load_set(sp)
        for f in check_set(RI, src, gate_date): rows.append(dict(f, symbol=sym, round=ra))
    main = [r for r in rows if r['kind'] != 'consensus']
    cons = {}
    for r in rows:
        if r['kind'] == 'consensus': cons[r['symbol']] = cons.get(r['symbol'], 0) + 1
    out = dict(date=gate_date, rule='RULES.md 23 timeliness', flagged=main, consensus_over_21d=cons,
               stale=[r for r in main if r['result'] == 'stale_forecast'], undated=[r for r in main if r['result'] == 'no_pub_date'],
               names_flagged=sorted({r['symbol'] for r in main}),
               missing=missing, note='legacy sources carry no `published` or `kind`: both are guessed from the title (kind_from / '
               'published_from = guess / title ...); the gate checker records the page date at each name\'s next update')
    os.makedirs(f'{HERE}/data', exist_ok=True)
    json.dump(out, open(f'{HERE}/data/timeliness_scan.json', 'w'), indent=1)
    return out


# ---------------------------------------------------------------- citation gate wiring (run_digest.py gate / gate-merge, name_update.py finish)
def _source_lists(rd):
    """[(label, sources)] for a round folder: the supervisor's list first, then each forecaster's."""
    out = []
    if os.path.exists(f'{rd}/supervisor.json'): out.append(('supervisor', json.load(open(f'{rd}/supervisor.json')).get('sources') or []))
    for p in sorted(os.listdir(rd)) if os.path.isdir(rd) else []:
        if re.match(r'agent_\d+\.json$', p):
            try: out.append((p[:-5], json.load(open(f'{rd}/{p}')).get('sources') or []))
            except Exception: pass
    return out


def gate_prep(rd, gate_date, scope_urls):
    """Called by run_digest.gate_prep: records which cited URLs are forecast sources (shard briefs mark them so the checker
    records the page's publication date) -> gate/timeliness.json. Returns the set of forecast URLs."""
    fc = {}
    if os.path.exists(f'{rd}/supervisor.json'):  # outside forecasts the set uses anywhere get opened for their date too
        S = json.load(open(f'{rd}/supervisor.json')); body = {k: v for k, v in S.items() if k != 'sources'}
        ids, txt = used_refs(body)
        for i, x in enumerate(S.get('sources') or [], 1):
            k = kind_of(x)[0]
            if k not in ('not_forecast', 'consensus') and (i in ids or (x.get('url') and x['url'] in txt)):
                fc.setdefault(x.get('url'), dict(list='supervisor', src=i, kind=k))
    for lab, src in _source_lists(rd):
        for i, x in enumerate(src, 1):
            u = x.get('url')
            if u in scope_urls and u not in fc and kind_of(x)[0] != 'not_forecast': fc[u] = dict(list=lab, src=i, kind=kind_of(x)[0])
    os.makedirs(f'{rd}/gate', exist_ok=True)
    json.dump(dict(date=gate_date, scope_urls=sorted(scope_urls), forecast_urls=fc), open(f'{rd}/gate/timeliness.json', 'w'), indent=1)
    return set(fc)


def gate_check(rd, gate_date=None, agent_rows=None):
    """Gate rows for every in-scope forecast source that is stale or undated, using the checker's page dates (shard results
    `published`, `source_kind`, `newer_found`, `actuals_released`) over the set's own fields. Re-reads supervisor.json, so a fix
    (newer source cited, timeliness.none_newer written) clears on the next check."""
    tp = f'{rd}/gate/timeliness.json'
    if not os.path.exists(tp): return []
    T = json.load(open(tp)); scope = set(T.get('scope_urls') or [])
    gate_date = gate_date or datetime.date.today().isoformat()
    ag = {}
    for r in agent_rows or []:
        u = r.get('source_url')
        if not u: continue
        a = ag.setdefault(u, {})
        for k in ('published', 'source_kind', 'newer_found', 'actuals_released', 'actuals_through'):
            if r.get(k) and not a.get(k): a[k] = r[k]
    rows, seen = [], set()
    for lab, src in _source_lists(rd):
        if lab == 'supervisor':  # every source the set uses anywhere; consensus pages only on the rows in scope
            fl = check_set(json.load(open(f'{rd}/supervisor.json')), src, gate_date, scope, ag)
        else:  # a forecaster's file (full rounds): its sources on the rows in scope
            fl = [dict(f, src=i) for i, x in enumerate(src, 1) if x.get('url') in scope
                  for f in [screen(x, gate_date, src, ag.get(x.get('url')))] if f]
        for f in fl:  # per file (Kilian 2026-11-23, MRVL: deduping by URL alone hid agent_2-4's stale copies of a page agent_1 cited)
            if (lab, f['url']) in seen: continue
            seen.add((lab, f['url'])); rows += gate_rows([f], origin=f'timeliness.py ({lab} sources)')
    return rows


def refuse(U, s, step):
    """name_update.py finish: refuse while an in-scope forecast source is stale or undated. No override (Kilian 2026-11-25)."""
    rd = f'{RUNS}/{U}/{s}'
    rows = gate_check(rd)
    if not rows: return
    lines = '\n'.join(f"  [{r['result']}] {r['item']}: {r['value']}; {r['page_shows']}" for r in rows)
    sys.exit(f"REFUSED (stale-forecast, {step}): {s} {U}: {len(rows)} forecast source(s) stale or undated (RULES.md 23, Kilian 2026-11-17).\n{lines}\n"
                    'Cite a newer forecast of the same series (sources[N].timeliness.newer_forecast) or show the search that none exists '
                    '(timeliness.none_newer {searched, queries, checked} + reconciled against the actuals), then re-argue what it supported. '
             + 'No override exists (Kilian 2026-11-25: "there is absolutely no bypass permitted"); if a fix seems impossible, tell Kilian the reason and ask.')


def _arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


if __name__ == '__main__':
    today = _arg('--date', datetime.date.today().isoformat())
    if len(sys.argv) > 1 and sys.argv[1] == 'check':
        pos = [a for a in sys.argv[2:] if not a.startswith('--') and a != _arg('--date')]
        RI, src, sp = _load_set(pos[0], pos[1] if len(pos) > 1 else None)
        fl = check_set(RI, src, today)
        for f in fl:
            print(f"FAIL {f['result']} src {f['src']}: {f['title'][:110]} | {f['kind']} published {f['published']} "
                  f"({f['published_from']}) | {'; '.join(f.get('reasons') or [])} | {f['why']}")
        print(f"timeliness: {len(fl)} forecast source(s) refused (sources from {sp or pos[0]}, gate date {today})")
        sys.exit(1 if fl else 0)
    if len(sys.argv) > 1 and sys.argv[1] == 'scan':
        o = scan(today)
        for r in sorted(o['flagged'], key=lambda r: (r['result'] != 'stale_forecast', r['symbol'])):  # consensus pages: counts only
            print(('STALE   ' if r['result'] == 'stale_forecast' else 'UNDATED ') +f"{r['symbol']:5} src {r['src']:>3} {r['kind']:17} pub {r['published'] or '?':10} ({r['published_from']:12}) "
                  f"{(r.get('age_days') or 0):>4}d  {r['title'][:90]}")
        print('consensus pages older than 21 days, by name:', o['consensus_over_21d'])
        print(f"{len(o['stale'])} stale + {len(o['undated'])} undated non-consensus forecast sources across {len(o['names_flagged'])} names: {', '.join(o['names_flagged'])}"
              + (f"; no live set found: {', '.join(o['missing'])}" if o['missing'] else ''))
        sys.exit(0)
    print(__doc__)
