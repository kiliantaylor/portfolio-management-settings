"""Reported actuals for the driver sets, pulled by script from SEC XBRL (Kilian 2026-10-28 03:03Z: "yes pull from xbrl by
script"). A quarter's actuals land in the driver set with no agent; agents only touch forward driver values that need judgment.

  python3 xbrl_actuals.py check SYM [...]     each mapped driver's FY base from XBRL vs the live set's base (must be within TOL)
  python3 xbrl_actuals.py pull SYM [...]      quarters and fiscal years per mapped driver -> data/xbrl_actuals/SYM.json
  python3 xbrl_actuals.py roll U SYM          writes the pull into the update round runs/U/SYM (name_update.py start calls it)
  python3 xbrl_actuals.py verify U SYM        gate check by script of the rows the roll changed (run_digest.py gate-update calls it)
  python3 xbrl_actuals.py find SYM VALUE [FY_END]   lists facts in the FY ending FY_END within 3% of VALUE (mapping aid)

Source: the XBRL instance of each 10-K / 10-Q (EDGAR *_htm.xml), not the companyfacts API, because segment and product-line
figures are dimensional facts that companyfacts leaves out. Every value carries its filing (form, accession, folder URL).
Quarters: 10-Q three-month facts; a quarter filed only inside a year-to-date figure (cash-flow items, the fourth quarter)
is the YTD to its end less the YTD to the end of the quarter before (Q4 = fiscal year - nine months). Instants (backlog, PP&E) are period ends.
Map: data/xbrl_driver_map.json, {SYM: {driver_id: {expr, scale, kind, note}}}. expr is arithmetic on facts written
f('Concept', 'Axis=Member', ...), concept without prefix = us-gaap; i('Concept', ...) is the instant at the period end, io(...)
the instant at the period's opening and ia(...) the average of the period's opening and closing instants; scale divides (1e6 for $M, 1e9 for $B); expr may be a list of
alternatives (first that evaluates wins, e.g. old and new segment members); tol_abs allows an absolute gap for a near-zero base. A driver with no
map entry (unit volumes, prices, non-GAAP margins, outside data) stays agent-sourced. A mapped driver whose FY base misses
the set's base by more than TOL is reported as `off` and also stays agent-sourced until its map is fixed.
User-Agent: the contact SEC asks for (privacy rule exception, Kilian 2026-10-22: his email, sec.gov only)."""
import datetime as dt, json, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE)
UA = 'portfolio research kilian@fire-exit.com'  # sec.gov only
CACHE = '/tmp/xbrl_inst_cache'; os.makedirs(CACHE, exist_ok=True)
MAP = os.path.join(HERE, 'data', 'xbrl_driver_map.json'); OUT = os.path.join(HERE, 'data', 'xbrl_actuals')
TOL = 0.03; N_FILINGS = 9  # ~two fiscal years of 10-K/10-Q: current-year quarters plus the prior year for YTD pace
J = lambda p: json.load(open(p))


def _get(url, path):
    if os.path.exists(path): return open(path, 'rb').read()
    for k in range(4):
        r = subprocess.run(['curl', '-sS', '-A', UA, '-o', path + '.tmp', '-w', '%{http_code}', url], capture_output=True, text=True)
        time.sleep(0.15)
        if r.stdout.strip() == '200': os.replace(path + '.tmp', path); return open(path, 'rb').read()
        time.sleep(2 ** k)
    raise RuntimeError(f'{url} -> HTTP {r.stdout.strip() or r.stderr.strip()}')


def filings(sym, n=N_FILINGS):
    import secfacts
    sym, cik = secfacts.cik_of(sym); r = secfacts.submissions(cik)['filings']['recent']; out = []
    for i, fm in enumerate(r['form']):
        if fm in ('10-K', '10-Q', '10-K/A', '10-Q/A') and r['primaryDocument'][i].endswith('.htm'):
            out.append(dict(form=fm, filed=r['filingDate'][i], period=r['reportDate'][i], accn=r['accessionNumber'][i],
                            doc=r['primaryDocument'][i], url=secfacts.folder(cik, r['accessionNumber'][i]) + r['primaryDocument'][i], cik=cik))
        if len(out) >= n: break
    return out


def facts(f):
    """[(concept, start, end, dims, value)] from one filing's instance; start None for instants. dims = sorted ((axis, member))."""
    a = f['accn'].replace('-', ''); name = f['doc'].replace('.htm', '_htm.xml')
    x = _get(f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{a}/{name}", f'{CACHE}/{a}_{name}').decode('utf8', 'ignore')
    ctx = {}
    for m in re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', x, re.S):
        b = m.group(2); st = re.search(r'startDate>([\d-]+)<', b); en = re.search(r'(?:endDate|instant)>([\d-]+)<', b)
        dims = tuple(sorted(re.findall(r'dimension="([^"]+)">\s*([^<\s]+)\s*<', b)))
        if en: ctx[m.group(1)] = (st.group(1) if st else None, en.group(1), dims)
    out = []
    for m in re.finditer(r'<([A-Za-z][\w-]*):(\w+)\b([^>]*?)contextRef="([^"]+)"[^>]*>\s*(-?[\d.]+(?:[eE][-+]?\d+)?)\s*</\1:\2>', x):
        c = ctx.get(m.group(4))
        if c: out.append((m.group(1) + ':' + m.group(2), c[0], c[1], c[2], float(m.group(5))))
    return out


def _days(s, e): return (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days


class Book:
    """All facts of a name's recent filings; later filings win for the same (concept, period, dims)."""
    def __init__(self, sym):
        self.sym = sym; self.fl = filings(sym); self.v = {}
        for f in reversed(self.fl):  # oldest first, so newer filings overwrite
            for c, s, e, d, val in facts(f): self.v[(c.lower(), s, e, d)] = (val, f)  # companies re-case custom tags

    def _norm(self, concept): return (concept if ':' in concept else 'us-gaap:' + concept).lower()

    def _dims(self, members):
        out = []
        for m in members:
            ax, mem = m.split('=', 1)
            out.append((ax if ':' in ax else 'srt:' + ax if ax in ('ProductOrServiceAxis', 'StatementGeographicalAxis', 'SegmentsAxis', 'ConsolidationItemsAxis') else 'us-gaap:' + ax, mem))
        return tuple(sorted(out))

    def flow(self, concept, members, s, e):
        k = (self._norm(concept), s, e, self._dims(members))
        if k in self.v: return self.v[k]
        for (c, s2, e2, d), hit in self.v.items():  # allow a few days' slack on period boundaries (52/53-week years)
            if c == k[0] and d == k[3] and s2 and abs(_days(s, s2)) <= 7 and abs(_days(e, e2)) <= 7: return hit
        return None

    def instant(self, concept, members, at):
        k0 = self._norm(concept); d0 = self._dims(members)
        for (c, s2, e2, d), hit in self.v.items():
            if c == k0 and d == d0 and s2 is None and abs(_days(at, e2)) <= 7: return hit
        return None

    def periods(self):
        """Fiscal years (10-K, ~365 days) and quarters (~91 days) seen in the filings, from the dei/us-gaap revenue-like facts."""
        fy, q = set(), set()
        for (c, s, e, d) in self.v:
            if s is None or d: continue
            n = _days(s, e)
            if 350 <= n <= 380: fy.add((s, e))
            elif 84 <= n <= 98: q.add((s, e))
        for fs, fe in fy:  # fourth quarters have no three-month fact of their own (derived as FY - 9M in evaluate)
            inq = [e for s, e in q if fs <= s and e < fe]
            if inq and 84 <= _days(max(inq), fe) - 1 <= 98:
                q.add(((dt.date.fromisoformat(max(inq)) + dt.timedelta(days=1)).isoformat(), fe))
        return sorted(fy, key=lambda p: p[1]), sorted(q, key=lambda p: p[1])


def evaluate(book, expr, s, e, scale=1.0):
    """Value of a map expr over period (s, e) and the filings it used; None when a fact is missing.
    A quarter that only exists as FY - 9M YTD (fourth quarter) is derived that way."""
    used = []
    def get_flow(concept, members):
        hit = book.flow(concept, members, s, e)
        if hit: used.append(hit[1]); return hit[0]
        if 84 <= _days(s, e) <= 98:  # quarter filed only inside a year-to-date figure (cash flows, Q4): YTD to e - YTD to the day before s
            k0, d0 = book._norm(concept), book._dims(members); prev = (dt.date.fromisoformat(s) - dt.timedelta(days=1)).isoformat()
            for fs in sorted({s2 for (c, s2, e2, d) in book.v if c == k0 and d == d0 and s2 and s2 < s and abs(_days(e, e2)) <= 7}):
                big = book.flow(concept, members, fs, e); small = book.flow(concept, members, fs, prev)
                if big and small: used.extend([big[1], small[1]]); return big[0] - small[0]
        raise KeyError(f'{concept} {members} {s}..{e}')
    def f(concept, *members): return get_flow(concept, members)
    def i(concept, *members):
        hit = book.instant(concept, members, e)
        if not hit: raise KeyError(f'{concept} {members} @{e}')
        used.append(hit[1]); return hit[0]
    def io(concept, *members):
        hit = book.instant(concept, members, (dt.date.fromisoformat(s) - dt.timedelta(days=1)).isoformat())
        if not hit: raise KeyError(f'{concept} {members} @open {s}')
        used.append(hit[1]); return hit[0]
    def ia(concept, *members):
        o = book.instant(concept, members, (dt.date.fromisoformat(s) - dt.timedelta(days=1)).isoformat())
        c = book.instant(concept, members, e)
        if not (o and c): raise KeyError(f'{concept} {members} avg {s}..{e}')
        used.extend([o[1], c[1]]); return (o[0] + c[0]) / 2
    err = None
    for ex in (expr if isinstance(expr, list) else [expr]):  # alternatives, e.g. before and after a segment re-cut
        used.clear()
        try: v = eval(ex, {'__builtins__': {}}, dict(f=f, i=i, io=io, ia=ia, abs=abs, min=min, max=max)) / scale; break
        except (KeyError, ZeroDivisionError) as e2: err = str(e2)
    else: return None, err
    src = sorted({(u['form'], u['filed'], u['accn'], u['url']) for u in used}, key=lambda t: t[1])
    return v, [dict(form=a, filed=b, accn=c, url=d) for a, b, c, d in src]


def live_set(sym):
    """(path, model_inputs) of the newest research round holding a driver set for SYM."""
    import glob, model_engine as me
    fs = sorted(glob.glob(os.path.join(HERE, 'runs', '2026-*', sym, 'supervisor.json')), reverse=True)
    for p in fs:
        try: mi = me.extract(me.load(p))
        except Exception: continue
        if mi.get('drivers') and mi.get('driver_build'): return p, mi
    return None, None


def _map(sym): return (J(MAP).get(sym) if os.path.exists(MAP) else None) or {}


def check(sym, book=None, quiet=False):
    p, mi = live_set(sym); m = _map(sym)
    if not mi: print(sym, 'no live driver set'); return {}
    book = book or Book(sym); fe = mi['base']['fy_end']
    fy = next(((s, e) for s, e in book.periods()[0] if abs(_days(fe, e)) <= 7), None)
    res = {}
    for d in mi['drivers']:
        spec = m.get(d['id'])
        if not spec: res[d['id']] = dict(status='agent', why='no XBRL tag mapped'); continue
        if not fy: res[d['id']] = dict(status='off', why=f'no FY ending {fe} in the last {N_FILINGS} filings'); continue
        v, src = evaluate(book, spec['expr'], *fy, spec.get('scale', 1.0))
        if v is None: res[d['id']] = dict(status='off', why=f'missing fact: {src}'); continue
        gap = v / d['base'] - 1 if d['base'] else float('inf')
        ok = abs(gap) <= TOL or abs(v - d['base']) <= spec.get('tol_abs', 0)  # tol_abs: a near-zero base (a margin) where 3% is meaningless
        res[d['id']] = dict(status='scripted' if ok else 'off', xbrl=round(v, 6), base=d['base'], gap=round(gap, 4) if gap != float('inf') else None, sources=src)
    if not quiet:
        print(f'{sym}  set {os.path.relpath(p, HERE)}  base FY ending {fe}')
        for k, r in res.items(): print(f"  {k:16s} {r['status']:9s}" + (f" xbrl {r['xbrl']:,.6g} vs base {r['base']:,.6g} ({r['gap']:+.1%})" if 'gap' in r else f" {r['why']}"))
    return res


def pull(sym):
    p, mi = live_set(sym)
    if not mi: print(sym, 'no live driver set'); return None
    book = Book(sym); chk = check(sym, book, quiet=True); m = _map(sym); fys, qs = book.periods()
    fe = mi['base']['fy_end']; cur = [q for q in qs if q[1] > fe]; base_q = [q for q in qs if q[1] <= fe and _days(q[1], fe) < 300]
    out = dict(symbol=sym, set=os.path.relpath(p, HERE), base_fy_end=fe, pulled=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
               method='SEC XBRL instances of the filings listed per value; YTD-only quarters = YTD - prior YTD', drivers={})
    for d in mi['drivers']:
        c = chk.get(d['id'], {}); row = dict(status=c.get('status', 'agent'), base_check={k: c[k] for k in ('xbrl', 'base', 'gap', 'why') if k in c})
        if c.get('status') == 'scripted':
            spec = m[d['id']]; kind = spec.get('kind', 'flow')
            row['quarters'] = []
            for s, e in base_q + cur:
                v, src = evaluate(book, spec['expr'], s, e, spec.get('scale', 1.0))
                if v is not None: row['quarters'].append(dict(start=s, end=e, fy='base' if e <= fe else 'y1', value=round(v, 6), sources=src))
            yq = [x for x in row['quarters'] if x['fy'] == 'y1']; n = len(yq)
            if n and kind == 'flow' and len(d.get('years') or []) >= 1:  # y1 pace: YTD vs the same quarters of the base year, scaled to the y1 path
                prior = [x for x in row['quarters'] if x['fy'] == 'base'][:n]
                if len(prior) == n and sum(x['value'] for x in prior):
                    ytd = sum(x['value'] for x in yq); pri = sum(x['value'] for x in prior)
                    row['y1_pace'] = dict(quarters=n, ytd=round(ytd, 6), prior_ytd=round(pri, 6), ytd_growth=round(ytd / pri - 1, 4),
                                          path_growth=round(d['years'][0] / d['base'] - 1, 4) if d['base'] else None)
            elif n and kind != 'flow':
                row['latest'] = yq[-1]
            if any(e == fy[1] for fy in fys for (s, e) in cur):
                row['new_fy'] = 'a fiscal year after the base has closed: the base can roll forward (model update)'
        out['drivers'][d['id']] = row
    os.makedirs(OUT, exist_ok=True); json.dump(out, open(os.path.join(OUT, f'{sym}.json'), 'w'), indent=1)
    return out


def summary(o):
    lines = [f"{o['symbol']} (base FY {o['base_fy_end']})"]
    for k, r in o['drivers'].items():
        s = f"  {k:16s} {r['status']:9s}"
        if r.get('y1_pace'): y = r['y1_pace']; s += f" YTD {y['quarters']}Q {y['ytd_growth']:+.1%} y/y vs path {y['path_growth']:+.1%}"
        elif r.get('latest'): s += f" latest {r['latest']['end']} {r['latest']['value']:,.6g}"
        elif r['base_check'].get('gap') is not None: s += f" base gap {r['base_check']['gap']:+.1%}"
        elif r['base_check'].get('why'): s += ' ' + r['base_check']['why'][:80]
        lines.append(s)
    return '\n'.join(lines)


def _kd(x):
    return f"{x.get('label') or x['id']}: {x['base']:,.4g} -> {'/'.join(f'{v:,.4g}' for v in x['years'])} ({x.get('basis')})"


def roll(U, sym):
    """Lands the pull in the update round runs/U/SYM. Every scripted driver gets `actuals` (quarters with their filings, y1
    pace). A flow driver with quarters filed after the base round (new to the set) gets its first projected year moved by
    their surprise against the path: y1 += sum over new quarters of (actual - same quarter a year earlier x y1 / base);
    quarters filed before the base round are already in the modeler's y1. When the year has closed, y1 = the actual. Later
    years are scaled by the same factor, so the path's growth rates stay; its inputs_detail
    row and derivation say so and update.json changed_inputs lists it for the gate. Ratio, level and instant drivers get
    the actuals only: their forward values stay with the modeler. Quarterly sets (model_drivers quarters, 2026-10-29): every
    filed quarter's value replaces the driver's own value for that quarter (any kind); the forecast quarters stay with the
    modeler, who also moves quarters.actual and adds the quarter's reported line items. update.json xbrl_actuals lists scripted
    vs agent drivers."""
    import model_engine as me
    rd = os.path.join(HERE, 'runs', U, sym); sp = os.path.join(rd, 'supervisor.json'); up = os.path.join(rd, 'update.json')
    u = J(up) if os.path.exists(up) else {}; B = u.get('base') or U
    t = J(sp)
    try: mi = me.extract(t)
    except Exception: print(sym, 'no driver set in', sp); return None
    if not mi.get('drivers'): print(sym, 'no driver set in', sp); return None
    o = pull(sym)
    if not o: return None
    m = _map(sym); rolled, noted = [], []
    det = {r.get('input'): r for r in mi.get('inputs_detail') or []}
    for x in mi['drivers']:
        r = o['drivers'].get(x['id']) or {}
        if r.get('status') != 'scripted': continue
        x['actuals'] = {k: r[k] for k in ('quarters', 'y1_pace', 'latest', 'new_fy') if k in r}
        x['actuals'].update(base_check=r['base_check'], pulled=o['pulled'], map=m[x['id']].get('note', ''))
        noted.append(x['id'])
        if mi.get('quarters') and x.get('quarters'):  # quarterly set (2026-10-29): the filed quarters replace the driver's own values
            E = mi['quarters']['ends']; ch = []
            for qq in r.get('quarters') or []:
                j = next((i for i, e in enumerate(E) if abs(_days(e, qq['end'])) <= 20), None)
                if j is None or abs(x['quarters'][j] - qq['value']) <= 1e-6 * max(abs(qq['value']), 1e-9): continue
                ch.append(dict(label=mi['quarters']['labels'][j], end=qq['end'], old=x['quarters'][j], new=qq['value'],
                               accn=sorted({src['accn'] for src in qq['sources']}))); x['quarters'][j] = qq['value']
            if not ch: continue
            why = (f"XBRL actuals {U}: filed quarters set " + '; '.join(f"{c['label']} {c['old']:,.6g} -> {c['new']:,.6g}" for c in ch) +
                   f". Filings: {', '.join(sorted({a for c in ch for a in c['accn']}))} (data/xbrl_actuals/{sym}.json).")
            x['derivation'] = why + ' ' + (x.get('derivation') or '')
            x['actuals']['rolled'] = dict(quarterly=True, changed=ch, quarters_after=list(x['quarters']))
            row = det.get(f"driver:{x['id']}")
            if row is not None: row['derivation'] = x['derivation']
            rolled.append(x['id']); continue
        yq = [q for q in r.get('quarters') or [] if q['fy'] == 'y1']
        prior = [q for q in r.get('quarters') or [] if q['fy'] == 'base']
        new = [j for j, q in enumerate(yq) if max(src['filed'] for src in q['sources']) > B and j < len(prior)]
        p = r.get('y1_pace')
        if not (new and p and m[x['id']].get('kind', 'flow') == 'flow' and x['years'] and x['base']): continue
        old = list(x['years']); n = p['quarters']; rate = old[0] / x['base']
        surprise = sum(yq[j]['value'] - prior[j]['value'] * rate for j in new)
        y1 = p['ytd'] if n >= 4 else old[0] + surprise
        k = y1 / old[0] if old[0] else 1.0
        x['years'] = [round(y1, 6)] + [round(v * k, 6) for v in old[1:]]
        accn = sorted({src['accn'] for q in yq for src in q['sources']})
        nq = ', '.join(f"{yq[j]['end']} {yq[j]['value']:,.6g} vs {prior[j]['value']:,.6g} x {rate:.4f}" for j in new)
        why = (f"XBRL actuals {U}: " + (f"year closed, y1 = actual {y1:,.6g}" if n >= 4 else
               f"y1 {old[0]:,.6g} + surprise of new quarters ({nq}) {surprise:+,.6g} = {y1:,.6g}") +
               f"; later years x {k:.4f}. Filings: {', '.join(accn)} (data/xbrl_actuals/{sym}.json).")
        x['derivation'] = why + ' ' + (x.get('derivation') or '')
        x['actuals']['rolled'] = dict(y1_old=old[0], y1_new=round(y1, 6), scale=round(k, 6), new_quarters=[yq[j]['end'] for j in new], years_after=list(x['years']))
        row = det.get(f"driver:{x['id']}")
        if row is not None:
            row['value'] = _kd(x); row['derivation'] = x['derivation']
            row['source'] = f"{row.get('source') or ''}; SEC XBRL {', '.join(accn)}".lstrip('; ')
        rolled.append(x['id'])
    json.dump(t, open(sp, 'w'), indent=1)
    if u:
        ci = list(u.get('changed_inputs') or [])
        u['changed_inputs'] = ci + [f'driver:{i}' for i in rolled if f'driver:{i}' not in ci]
        u['xbrl_actuals'] = dict(pulled=o['pulled'], file=f'data/xbrl_actuals/{sym}.json', scripted=noted, y1_rolled=rolled,
                                 agent=[k for k, r in o['drivers'].items() if r['status'] != 'scripted'])
        json.dump(u, open(up, 'w'), indent=1)
    print(summary(o)); print(f'{sym}: actuals on {len(noted)} drivers, y1 re-set on {rolled or "none"} (quarters filed after {B})')
    return dict(scripted=noted, rolled=rolled)


def verify(U, sym):
    """Gate check by script (Kilian card 2026-10-28 03:19Z "Script checks XBRL rows"): a driver row the roll changed passes
    when every recorded quarter matches a fresh re-pull from the same filings (0.1%) and the modeler left the rolled years
    and the base as the script wrote them. Rows that fail, or that the modeler changed further, go to the gate agents.
    Returns {passed: [{input, value, filings, note}], to_agents: [{input, why}]}."""
    import model_engine as me
    rd = os.path.join(HERE, 'runs', U, sym); mi = me.extract(J(os.path.join(rd, 'supervisor.json')))
    u = J(os.path.join(rd, 'update.json')); B = u.get('base')
    base_mi = {}
    try: base_mi = {x['id']: x for x in me.extract(J(os.path.join(HERE, 'runs', B, sym, 'supervisor.json'))).get('drivers') or []}
    except Exception: pass
    m = _map(sym); book = None; out = dict(passed=[], to_agents=[])
    for x in mi.get('drivers') or []:
        ro = (x.get('actuals') or {}).get('rolled')
        if not ro: continue
        inp = f"driver:{x['id']}"; why = None
        if ro.get('quarterly'):
            if x.get('quarters') != ro.get('quarters_after'): why = 'modeler changed the quarters after the script roll'
        elif x['years'] != ro.get('years_after'): why = 'modeler changed the years after the script roll'
        elif x['id'] in base_mi and base_mi[x['id']]['base'] != x['base']: why = 'base changed'
        if why: pass
        elif x['id'] not in m: why = 'no XBRL map entry'
        else:
            book = book or Book(sym); spec = m[x['id']]
            for q in x['actuals'].get('quarters') or []:
                v, _ = evaluate(book, spec['expr'], q['start'], q['end'], spec.get('scale', 1.0))
                if v is None or abs(v - q['value']) > 0.001 * max(abs(q['value']), 1e-9):
                    why = f"quarter {q['end']}: recorded {q['value']} vs re-pull {v}"; break
        if why: out['to_agents'].append(dict(input=inp, why=why)); continue
        fl = sorted({src['url'] for q in x['actuals'].get('quarters') or [] for src in q['sources']})
        out['passed'].append(dict(input=inp, value=_kd(x), filings=fl,
                                  note=f"{len(x['actuals'].get('quarters') or [])} quarters re-pulled and matched; " +
                                       (f"{len(ro['changed'])} filed quarter(s) set from the filings (quarterly set)" if ro.get('quarterly') else
                                        f"y1 {ro['y1_old']} -> {ro['y1_new']} by the roll formula, later years x {ro['scale']}")))
    return out


def find(sym, value, fe=None):
    book = Book(sym); fys = book.periods()[0]; fy = next((p for p in fys if fe and abs(_days(fe, p[1])) <= 7), None) or fys[-1]; hits = []
    for (c, s, e, d), (v, f) in book.v.items():
        ok = (s and abs(_days(fy[0], s)) <= 7 and abs(_days(fy[1], e)) <= 7) or (s is None and abs(_days(fy[1], e)) <= 7)
        if ok and v and abs(v / value - 1) <= 0.03: hits.append((abs(v / value - 1), c, s, e, d, v))
    for h in sorted(hits)[:25]: print(f'{h[5]:>18,.0f}  {h[1]}  {"instant" if h[2] is None else "flow"} {h[3]}  {" ".join(f"{a}={m}" for a, m in h[4])}')


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit()
    if a[0] == 'check':
        for s in a[1:]: check(s)
    elif a[0] == 'pull':
        for s in a[1:]:
            o = pull(s)
            if o: print(summary(o))
    elif a[0] == 'roll': roll(a[1], a[2])
    elif a[0] == 'verify': print(json.dumps(verify(a[1], a[2]), indent=1))
    elif a[0] == 'find': find(a[1], float(a[2]), a[3] if len(a) > 3 else None)
    else: print(__doc__)
