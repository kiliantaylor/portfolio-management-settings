"""Every forecast year checked against the Street (Kilian 2026-11-24, APP thread: "yes on the build").

What went wrong: APP's FY2027 revenue ran ~10% above the Street (booked $11.49B vs Zacks $10.47B, Morgan Stanley $10.36B) and
passed the Nov 4 gate and three gate rounds on 11-24. The gate only checks that quotes are real (management's words count as
evidence), the plausibility screen (judgment_check.py) only catches builds more than 2x from every anchor, the Nov 4 set had no
FY2027 revenue consensus row at all, and no card showed the Street figure.

Three checks (RULES.md 42):
  1. consensus-missing: every forecast year of revenue and EPS has a consensus row in model.reconciled_inputs.reference.consensus
     ({metric, fy: "FY2027", value, basis, as_of, source_ids}). A year no one publishes (Zacks / Nasdaq often stop at FY+2) has a
     row with value null and none_found {date, publishers: [...], note}: the search that found nothing. name_update.py gate
     refuses before round 1 and finish refuses; no override (the row can always be written).
  2. consensus-gap: a forecast year more than GAP_PCT (5%) from its consensus needs an entry in reference.consensus_gaps
     {metric, fy, why, source_ids} with at least one OUTSIDE source explaining the gap. Outside = not the company's own filings,
     releases, calls, IR pages or plans (management's words do not count here), and not a consensus feed (the Street number
     cannot explain a gap from itself): e.g. a sell-side note above the Street, industry data, a customer's or peer's filing.
     Missing or company-only -> an `unsupported` gate row (run_digest.py gate-merge), so it is sourced first and carded after
     round 2 (rules 4, 24); finish refuses it while open unless Kilian accepted that row (gate_accept.py). The explanation itself
     is a cited claim the gate checkers open (run_digest.py gate prep).
  3. cards (judgment_check.py): every card whose options move revenue or EPS in a forecast year shows the Street figure for each
     such year (judgment_check.cards prints the line from this module; card_problems refuses a card for a year with no consensus
     row; the settings hook card_check.py refuses a revenue/EPS card message whose fiscal years have no Street line).

EPS is compared on Zacks' basis: reported quarters at Zacks' reported EPS, forecast quarters at the engine's EPS + the Zacks bridge
(eps_path.py), so a GAAP vs adjusted difference is not a gap. A name whose Zacks measure is not in data/zacks_basis.json is
compared on the engine's own basis, and the line says so.
Revenue: the engine's fiscal-year rows (model_engine.value rows, fade rows excluded). Forecast year = a row with at least one
forecast quarter (annual sets: every row).
Buys: blocked on rounds dated on or after BUY_BLOCK_FROM (older rounds get the checks at their next update, as 11-17/18/23).

    python3 consensus_gap.py table D SYM     booked vs Street per forecast year, gap, explained or not
    python3 consensus_gap.py check D SYM     findings (exit 1 if any)
    python3 consensus_gap.py scan [D]        every current set (forecast_lint.current_sets), counts per rule
Tests: test_consensus_gap.py
"""
import copy, json, os, re, sys
from urllib.parse import urlparse
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs'))
sys.path.insert(0, HERE)
GAP_PCT = 5.0            # Kilian 2026-11-24: a year more than 5% from consensus needs an outside source explaining the gap
BUY_BLOCK_FROM = '2026-11-25'
_FY = re.compile(r'^(?:FY|CY)\s?(\d{4})$', re.I)
_REV = re.compile(r'^(total |net |consensus )?(revenue|revenues|sales|net sales)(\s*\([^)]*\))?(\s*,?\s*\$?\s?[mb]n?)?$', re.I)
_EPS = re.compile(r'^(adj\.? |adjusted |non-gaap |diluted |gaap |consensus )?eps\b', re.I)
_NOT = re.compile(r'growth|margin|whisper|%|per share growth|y/y|q/q', re.I)
COMPANY_KINDS = {'filing', 'transcript', 'company_guide', 'company_plan', 'company', 'company_call', 'company_guidance'}
CONSENSUS_KINDS = {'consensus'}
_CALL = re.compile(r'earnings call|conference call|transcript|investor day|analyst day|capital markets day|fireside|'
                   r'press release|shareholder letter|annual report|\b8-K\b|\b10-[KQ]\b|\bEx\.?\s?99|earnings release|'
                   r'\bguidance\b|investor presentation', re.I)
_CONS = re.compile(r'\bconsensus\b|analyst (?:earnings )?forecast|earnings estimates?|detailed-estimates', re.I)
_CONS_HOST = ('api.nasdaq.com', 'zacks.com', 'www.zacks.com')


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def metric(m):
    m = str(m or '').strip()
    if _NOT.search(m): return None
    return 'revenue' if _REV.match(m) else 'eps' if _EPS.match(m) else None


def _year(fy):
    y = _FY.match(str(fy or '').strip()); return int(y.group(1)) if y else None


def _cik(sym):
    c = ((_j(os.path.join(HERE, 'data', 'cik_map.json'), {}) or {}).get(sym) or {}).get('cik')
    return int(c) if c else None


def source_class(x, sym, cik=None):
    """'company' (its own filings, releases, calls, IR, plans), 'consensus' (a consensus feed) or 'outside'."""
    if not isinstance(x, dict): return 'company'
    k = str(x.get('kind') or '').lower(); t = str(x.get('title') or ''); u = str(x.get('url') or x.get('path') or '')
    host = (urlparse(u).hostname or '').lower()
    if k in CONSENSUS_KINDS or host in _CONS_HOST or (host.endswith('nasdaq.com') and '/earnings' in u): return 'consensus'
    if host.endswith('sec.gov'):  # a filing: the company's own (its CIK in the path) or someone else's (a peer / customer filing)
        nums = {int(n) for n in re.findall(r'/(?:data|CIK)/?0*(\d{3,10})\b', u)}
        return 'company' if cik is None or not nums or cik in nums else 'outside'
    if k in COMPANY_KINDS: return 'company'
    if host.startswith(('investor.', 'investors.', 'ir.')): return 'company'
    if x.get('speakers') or _CALL.search(t): return 'company'
    if k not in ('sell_side',) and _CONS.search(t): return 'consensus'
    return 'outside'


def consensus(RI, S=None):
    """{(metric, year): row} latest as_of per metric and fiscal year; a row may have value None with none_found. On the same
    as_of the Zacks page beats Nasdaq's feed, which can lag it (DY FY27 15.89 vs 17.02, 2026-11-24)."""
    best = {}
    for c in ((RI.get('reference') or {}).get('consensus') or []):
        if not isinstance(c, dict): continue
        k, y = metric(c.get('metric')), _year(c.get('fy'))
        if not k or not y: continue
        if not isinstance(c.get('value'), (int, float)) and not c.get('none_found'): continue
        nf = c.get('none_found')
        a = str(c.get('as_of') or (nf.get('date') if isinstance(nf, dict) else '') or '')
        zk = k == 'eps' and 'zacks' in f"{c.get('metric')} {c.get('basis')}".lower()  # EPS is compared on Zacks' basis: a Zacks row first
        pg = any(isinstance(x, dict) and 'zacks.com' in (urlparse(str(x.get('url') or '')).hostname or '') for x in
                 [(S or [])[i - 1] for i in _ids(c.get('source_ids')) if S and 0 < i <= len(S)])
        rank = (isinstance(c.get('value'), (int, float)), zk, a, pg)  # published beats none_found; then Zacks; then latest as_of; then the Zacks page
        if (k, y) not in best or rank >= best[(k, y)][0]: best[(k, y)] = (rank, c)
    out = {kk: c for kk, (_, c) in best.items()}
    for g in RI.get('guidance_check') or []:  # a guidance_check consensus level counts for revenue (as judgment_check.anchors)
        if not isinstance(g, dict): continue
        k, y, v = metric(g.get('metric')), _year(g.get('fy')), g.get('consensus')
        if k == 'revenue' and y and isinstance(v, (int, float)) and v > 100 and (k, y) not in out:
            out[(k, y)] = dict(metric=g.get('metric'), fy=g.get('fy'), value=v, as_of=g.get('consensus_as_of'), source_ids=g.get('consensus_source_ids') or [],
                               basis='guidance_check consensus')
    return out


def none_found_ok(c):
    nf = c.get('none_found')
    if not isinstance(nf, dict): return False, 'none_found must be {date, publishers: [...], note}'
    if not str(nf.get('date') or '').strip(): return False, 'none_found has no date'
    if not [p for p in nf.get('publishers') or [] if str(p).strip()]: return False, 'none_found names no publishers checked'
    return True, ''


def _value(RI, D, s):
    import model_engine as ME
    spot, beta = ME.prior_spot_beta(D, s)
    m, _ = ME.apply_dcf_policy(copy.deepcopy(RI), s, spot, beta)
    return m, ME.value(dict(m, _allow_long=True), D)


def _zacks_path(s, D, m, v, hist):
    try:
        import eps_path as EP
        if hist is None:
            import eps_tieout; hist = eps_tieout._hist(s)
        return EP.Path(s, D, m, v, hist)
    except Exception:
        return None


def booked(RI, D, s, hist=None, _mv=None):
    """{'revenue': {year: v}, 'eps': {year: v}, 'eps_basis': str, 'years': [forecast years]} from the engine."""
    m, v = _mv or _value(RI, D, s)
    rows = [r for r in v.get('rows') or [] if not str(r.get('label', '')).lower().startswith('fade')]
    Q = v.get('quarters') or []
    def yr(r): return _year(r.get('label')) or (int(str(r['end'])[:4]) if r.get('end') else None)
    lab = {r.get('label'): yr(r) for r in rows}
    fc_years = {lab.get(q.get('fy')) for q in Q if not q.get('actual')} - {None}
    years = [yr(r) for r in rows if yr(r) and (not Q or yr(r) in fc_years)]
    rev = {yr(r): r['revenue'] for r in rows if yr(r) in years and isinstance(r.get('revenue'), (int, float))}
    eps, basis, eps_page, qb = {}, 'engine', {}, {}
    if Q:
        P = _zacks_path(s, D, m, v, hist); qs = {}
        for q in Q:
            y = lab.get(q.get('fy'))
            if y in years and isinstance(q.get('eps'), (int, float)): qs.setdefault(y, []).append(q)
        zb = P is not None and P.B is not None
        basis = 'Zacks basis' if zb else 'engine basis, Zacks measure not checked'
        import eps_path as EP
        has_sbc = zb and any(it.get('item') == 'sbc' for it in P.B.get('items') or [])
        for y, L in qs.items():
            if len(L) != 4: continue
            t = tp = act = 0.0; fq = []; act_ok = True
            for q in L:
                e = ep_ = q['eps']
                if zb and q.get('actual'):
                    h = next((h for end, h in P.rep.items() if EP._near(end, q['end'])), None)
                    if h is not None and isinstance(h.get('eps'), (int, float)): e = h['eps']
                    else: act_ok = False
                    co = next((x.get('company') for x in P.B.get('quarters') or [] if x.get('end') and EP._near(x['end'], q['end'])), None)
                    if isinstance(co, (int, float)): ep_ = co
                    act += e
                elif zb:
                    b = P.bridge(q)
                    if b.get('gap') is not None:
                        e = q['eps'] + b['gap']
                        ep_ = e - sum(i.get('per_share', 0) for i in b.get('items') or [] if i.get('item') == 'sbc')
                    fq.append(q['label'])
                else: fq.append(q['label'])
                t += e; tp += ep_
            eps[y] = round(t, 4)
            if has_sbc: eps_page[y] = round(tp, 4)
            if zb and act_ok and len(fq) < 4: qb[y] = (round(act, 4), fq)
    else:
        eps = {yr(r): r['eps'] for r in rows if yr(r) in years and isinstance(r.get('eps'), (int, float))}
    return dict(revenue=rev, eps=eps, eps_basis=basis, years=years, eps_page=eps_page, quarter_build=qb)


# Basis traps (audit/street_check_1124/report.md, 2026-11-24): (1) for names whose Zacks measure deducts stock comp, the Zacks
# detailed-estimates PAGE shows company non-GAAP EPS while Nasdaq's Zacks feed and Zacks' reported actuals are after stock comp
# (SNOW -194% on the bridged basis, -2% on the page's); (2) an FY consensus can hold one-offs the quarterly actuals leave out
# (NFLX FY2026 -14.5% raw, +0.6% as reported quarters + quarter consensus); (3) a provider's revenue on another definition (GOOGL
# Zacks sales net of TAC): the row carries basis_bridge {booked_adjust, why, source_ids}, added to booked before comparing and gated.
_PAGE = re.compile(r'non-?gaap|adjusted|company', re.I)
_AFTER_SBC = re.compile(r'less (?:stock|sbc)|sbc expensed|after (?:stock|sbc)|incl(?:\.|uding)? (?:stock|sbc)', re.I)


def page_basis(c, S):
    """True when an EPS row is on company non-GAAP (before stock comp): its words say so, or it comes from the Zacks page."""
    w = f"{c.get('metric')} {c.get('basis')} {c.get('note')}"
    if _AFTER_SBC.search(w): return False
    if _PAGE.search(w): return True
    for i in _ids(c.get('source_ids')):
        x = S[i - 1] if 0 < i <= len(S) else None
        if isinstance(x, dict) and (urlparse(str(x.get('url') or '')).hostname or '').lower() in ('zacks.com', 'www.zacks.com'): return True
    return False


def quarter_street(RI, y, qb):
    """Reported Zacks quarters + quarter consensus for the year's forecast quarters, or None when a quarter row is missing."""
    if y not in qb: return None
    act, fq = qb[y]; rows = {}
    for c in ((RI.get('reference') or {}).get('consensus') or []):
        if isinstance(c, dict) and metric(c.get('metric')) == 'eps' and isinstance(c.get('value'), (int, float)):
            f = re.sub(r'\s+', ' ', str(c.get('fy') or '')).strip().upper()
            if f not in rows or str(c.get('as_of') or '') >= str(rows[f].get('as_of') or ''): rows[f] = c
    got = [rows.get(re.sub(r'\s+', ' ', l).strip().upper()) for l in fq]
    if not fq or any(g is None for g in got): return None
    return round(act + sum(g['value'] for g in got), 4), ', '.join(f"{l} {g['value']}" for l, g in zip(fq, got))


def _gap_entries(RI):
    out = {}
    for g in ((RI.get('reference') or {}).get('consensus_gaps') or []):
        if isinstance(g, dict) and metric(g.get('metric')) and _year(g.get('fy')): out[(metric(g['metric']), _year(g['fy']))] = g
    return out


def _ids(v):
    out = []
    for x in v if isinstance(v, list) else [v]:
        if isinstance(x, int) and not isinstance(x, bool): out.append(x)
        else: out += [int(n) for n in re.findall(r'\d+', str(x or ''))]
    return out


def table(D, s, d=None, hist=None, _mv=None):
    """One row per forecast year and metric: booked, street, gap_pct, status, explained, sources."""
    d = d or _j(os.path.join(RUNS, D, s, 'supervisor.json'), {}) or {}
    RI = (d.get('model') or {}).get('reconciled_inputs') or {}
    if not RI: return dict(rows=[], error='no model.reconciled_inputs')
    B = booked(RI, d.get('as_of') or D, s, hist, _mv); S = d.get('sources') or []; C = consensus(RI, S); G = _gap_entries(RI); cik = _cik(s)
    rows = []
    for y in B['years']:
        for k in ('revenue', 'eps'):
            b = B[k].get(y)
            if b is None: continue
            c = C.get((k, y)); r = dict(metric=k, fy=f'FY{y}', booked=round(b, 4 if k == 'eps' else 1), basis=B['eps_basis'] if k == 'eps' else '')
            if c is None: r.update(status='missing'); rows.append(r); continue
            if not isinstance(c.get('value'), (int, float)):
                ok, why = none_found_ok(c)
                r.update(status='none_published' if ok else 'missing', none_found=c.get('none_found'), why_bad=why); rows.append(r); continue
            sv = c['value']; r.update(street=sv, street_basis=c.get('basis'), street_as_of=c.get('as_of'), street_src=c.get('source_ids'))
            if k == 'eps' and B.get('eps_page') and page_basis(c, S) and y in B['eps_page']:  # trap 1: compare on the page's basis
                b = B['eps_page'][y]; r.update(booked=round(b, 4), basis='company non-GAAP (Zacks page basis, stock comp added back)')
            elif k == 'eps':
                qs_ = quarter_street(RI, y, B.get('quarter_build') or {})  # trap 2: reported quarters + quarter consensus
                if qs_: sv = qs_[0]; r.update(street=sv, street_basis=f"reported Zacks quarters + quarter consensus ({qs_[1]}); FY row {c['value']}")
            bb = c.get('basis_bridge')  # trap 3: the provider's own definition, bridged by a gated, sourced amount
            if isinstance(bb, dict) and isinstance(bb.get('booked_adjust'), (int, float)) and str(bb.get('why') or '').strip() and _ids(bb.get('source_ids')):
                b = b + bb['booked_adjust']; r.update(booked=round(b, 4 if k == 'eps' else 1), basis_bridge=f"{bb['booked_adjust']:+,} ({str(bb['why'])[:80]})")
            if not sv: r.update(status='ok', gap_pct=None); rows.append(r); continue
            gp = (b / sv - 1) * 100 if sv > 0 else (b - sv) / abs(sv) * 100
            r['gap_pct'] = round(gp, 2)
            if abs(gp) <= GAP_PCT: r.update(status='ok'); rows.append(r); continue
            g = G.get((k, y)); r['explained'] = False
            if g:
                cls = []
                for i in _ids(g.get('source_ids') or g.get('sources')):
                    x = S[i - 1] if 0 < i <= len(S) else None
                    cls.append((i, source_class(x, s, cik) if x is not None else 'missing'))
                out = [i for i, c_ in cls if c_ == 'outside']
                r.update(why=g.get('why'), sources=cls, outside=out)
                r['explained'] = bool(str(g.get('why') or '').strip()) and bool(out)
            r['status'] = 'gap_explained' if r['explained'] else 'gap_unsupported'
            rows.append(r)
    return dict(rows=rows, eps_basis=B['eps_basis'])


def _where(r): return f"consensus gap {r['metric']} {r['fy']}"


def check(D, s, d=None, hist=None):
    """Findings: consensus-missing (rule 1) and consensus-gap (rule 2)."""
    try: T = table(D, s, d, hist)
    except Exception as e:
        return [dict(rule='consensus-missing', key='consensus-gap-error', where='consensus_gap.py', value=str(e)[:200],
                     why=f'consensus_gap.py could not value this set ({type(e).__name__}: {str(e)[:160]}); fix the set or the script')]
    if T.get('error'): return []
    F = []
    for r in T['rows']:
        if r['status'] == 'missing':
            F.append(dict(rule='consensus-missing', key=f"consensus-missing:{r['metric']}:{r['fy']}", where=f"consensus {r['metric']} {r['fy']}",
                          value=r['booked'], why=(f"no consensus row for {r['metric']} {r['fy']} (booked {r['booked']}): add it to reference.consensus with its source, "
                                                  "or, if no one publishes that year, a row with value null and none_found {date, publishers, note}"
                                                  + (f" ({r['why_bad']})" if r.get('why_bad') else ''))))
        elif r['status'] == 'gap_unsupported':
            have = ', '.join(f'src {i} {c}' for i, c in r.get('sources') or []) or 'no consensus_gaps entry'
            F.append(dict(rule='consensus-gap', key=f"consensus-gap:{r['metric']}:{r['fy']}", where=_where(r), value=r['booked'],
                          why=(f"{r['metric']} {r['fy']} booked {r['booked']} is {r['gap_pct']:+.1f}% from consensus {r['street']} ({r.get('street_basis') or ''}, "
                               f"{r.get('street_as_of') or ''}), over {GAP_PCT:.0f}%, with no outside source explaining the gap ({have}). Add "
                               "reference.consensus_gaps {metric, fy, why, source_ids} citing at least one source that is not the company's own words or a "
                               "consensus feed (Kilian 2026-11-24), or rebuild the year")))
    return F


def _accepted(D, s):
    a = _j(os.path.join(RUNS, D, s, 'citation_audit.json'), {}) or {}
    return {str(f.get('item')) for f in a.get('accepted_carried') or [] if f.get('status') == 'accepted_by_kilian'}


def round_fail(D, s, for_buys=False, step='finish'):
    """Open findings: gate start sees only consensus-missing (gaps go to the gate as unsupported rows); finish and buys see both,
    less gap rows Kilian accepted (gate_accept.py)."""
    if for_buys and str(D) < BUY_BLOCK_FROM: return []
    d = _j(os.path.join(RUNS, D, s, 'supervisor.json'), {}) or {}
    if not ((d.get('model') or {}).get('reconciled_inputs') or {}).get('drivers'): return []
    F = check(D, s, d); acc = _accepted(D, s)
    if step == 'gate': return [f for f in F if f['rule'] == 'consensus-missing']
    return [f for f in F if not (f['rule'] == 'consensus-gap' and f['where'] in acc)]


def refuse(D, s, step):
    F = round_fail(D, s, step=step)
    if not F: return
    sys.exit(f"REFUSED (consensus-gap, {step} {s} {D}; RULES.md 42, Kilian 2026-11-24 after APP FY2027 ran ~10% above the Street): "
             + '\n'.join(f"  [{f['rule']}] {f['where']}: {f['why']}" for f in F)
             + f'\nTable: python3 {HERE}/consensus_gap.py table {D} {s}. No override: write the row, the explanation, or have Kilian decide the year on a card (gate_accept.py).')


def gate_rows(D, s):
    """Rows for run_digest.py gate-merge: an unexplained >5% gap is `unsupported` (a must-card row after round 2)."""
    return [dict(origin='consensus_gap.py (script)', item=f['where'], value=f['value'], basis='', source_url='', result='unsupported',
                 page_shows='', note=f"{f['why']} | key {f['key']}", checked_by='consensus_gap.py (script)')
            for f in round_fail(D, s) if f['rule'] == 'consensus-gap']  # rows Kilian accepted stay closed


def gate_items(RI, su):
    """Cited items for run_digest.py gate prep: each consensus row, and each gap explanation as a claim on its sources."""
    out = []
    for c in ((RI.get('reference') or {}).get('consensus') or []):
        if isinstance(c, dict) and isinstance(c.get('value'), (int, float)) and metric(c.get('metric')):
            out.append((f"consensus {c.get('metric')} {c.get('fy')} as of {c.get('as_of')} ({c.get('basis') or ''})", c['value'], 'consensus', su(_ids(c.get('source_ids')))))
    for c in ((RI.get('reference') or {}).get('consensus') or []):
        bb = c.get('basis_bridge') if isinstance(c, dict) else None
        if isinstance(bb, dict) and isinstance(bb.get('booked_adjust'), (int, float)):
            out.append((f"basis bridge to consensus {c.get('metric')} {c.get('fy')}: {bb.get('why')}", bb['booked_adjust'], 'claim', su(_ids(bb.get('source_ids')))))
    for g in ((RI.get('reference') or {}).get('consensus_gaps') or []):
        if isinstance(g, dict) and str(g.get('why') or '').strip():
            out.append((f"why {g.get('metric')} {g.get('fy')} differs from consensus (the source must support this explanation)", g['why'], 'claim', su(_ids(g.get('source_ids') or g.get('sources')))))
    return out


def street_line(T, years=None, metrics=('revenue', 'eps')):
    """Markdown 'Street' lines for a card or the finish reply: one per covered (metric, year)."""
    L = []
    for r in T.get('rows') or []:
        if r['metric'] not in metrics or (years is not None and (r['metric'], r['fy']) not in years): continue
        nm = 'revenue ($M)' if r['metric'] == 'revenue' else f"EPS ({r.get('basis') or 'engine basis'})"
        bk = f"{r['booked']:,.1f}" if r['metric'] == 'revenue' else f"{r['booked']:.2f}"
        if r['status'] == 'none_published':
            nf = r.get('none_found') or {}
            L.append(f"Street {r['fy']} {nm}: no consensus published (checked {nf.get('date')}: {', '.join(map(str, nf.get('publishers') or []))}); booked {bk}")
        elif r['status'] == 'missing':
            L.append(f"Street {r['fy']} {nm}: MISSING (no consensus row)")
        else:
            g = f" ({r['gap_pct']:+.1f}% vs Street)" if r.get('gap_pct') is not None else ''
            L.append(f"Street {r['fy']} {nm}: {r['street']:,} ({r.get('street_basis') or ''}, {r.get('street_as_of') or ''}); booked {bk}{g}"
                     + (' (gap explained by outside src ' + ', '.join(map(str, r.get('outside') or [])) + ')' if r['status'] == 'gap_explained' else '')
                     + (' (GAP UNSUPPORTED)' if r['status'] == 'gap_unsupported' else ''))
    return L


def flag_lines(D, s):
    """Finish flags: each >5% gap (explained or accepted) and each year with no consensus published."""
    try: T = table(D, s)
    except Exception: return []
    out = []
    for r in T.get('rows') or []:
        if r['status'] in ('gap_explained', 'gap_unsupported'):
            out.append(f"STREET GAP {r['metric']} {r['fy']}: booked {r['booked']} vs consensus {r['street']} ({r['gap_pct']:+.1f}%): "
                       + (str(r.get('why') or '')[:200] if r['status'] == 'gap_explained' else 'accepted by Kilian on a card'))
        elif r['status'] == 'none_published':
            out.append(f"NO STREET FIGURE {r['metric']} {r['fy']}: none published (checked {(r.get('none_found') or {}).get('date')})")
    return out


def scan(D=None):
    import forecast_lint as FL
    cs = FL.current_sets(D); rep = {}
    for s, ra in sorted(cs.items()):
        d = _j(os.path.join(RUNS, ra, s, 'supervisor.json'), {}) or {}
        if not ((d.get('model') or {}).get('reconciled_inputs') or {}).get('drivers'): continue
        try: T = table(ra, s, d)
        except Exception as e: rep[s] = dict(round=ra, error=str(e)[:200]); continue
        c = {}
        for r in T['rows']: c[r['status']] = c.get(r['status'], 0) + 1
        rep[s] = dict(round=ra, counts=c, gaps=[f"{r['metric']} {r['fy']} {r['gap_pct']:+.1f}%" for r in T['rows'] if str(r['status']).startswith('gap')])
        print(f"{s:6} {ra}  " + ', '.join(f'{k} {v}' for k, v in sorted(c.items())) + ('  | ' + '; '.join(rep[s]['gaps']) if rep[s]['gaps'] else ''))
    return rep


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'table': print(json.dumps(table(a[1], a[2].upper()), indent=1, default=str))
    elif a[0] == 'check':
        F = check(a[1], a[2].upper()); print(json.dumps(F, indent=1)); sys.exit(1 if F else 0)
    elif a[0] == 'scan':
        r = scan(a[1] if len(a) > 1 else None)
        json.dump(r, open(os.path.join(HERE, 'data', 'consensus_gap_scan.json'), 'w'), indent=1)
    else: print(__doc__); sys.exit(1)
