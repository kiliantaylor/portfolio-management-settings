"""Reported-quarter EPS tie-out (Kilian 2026-11-23: "it shouldn't miss at all"; "fix whatever issue allowed the APP and BKNG mistakes
to happen so it can't again").

Why it exists: the engine's quarter EPS is (EBIT - interest) x (1 - tax) / shares. Nothing compared that formula with what the company
actually filed, so a non-operating line the model never carried (APP's interest and other income, BKNG's) showed up only as an
unexplained "model gap" in data/eps_bridge.json.

What it does, per reported quarter the model set holds (eps_path.Path.pairs: Zacks history x the model's actual quarter rows):
  1. GAAP from the filed lines (SEC XBRL, preprint_log.filings_q): (pre-tax - tax - NCI - preferred) / filed diluted shares. This must
     equal the filed diluted EPS to $0.01 (the filed EPS is rounded to the cent); if not, a filed line is missing and the row says so.
     Diluted shares filed rounded (KEYS: millions) are taken at the count inside that rounding that net income / filed EPS implies
     (_shares_at_filed_precision); equity-security marks filed only as realized + unrealized tags are summed (_marks_from_parts).
     The EPS numerator the filer uses (Kilian 2026-11-23, INTC/ABT/AMBQ): a diluted-numerator adjustment from the EPS note in
     data/eps_numerator.json (sourced; INTC escrowed-share gain); else, when net income as filed misses, the tagged two-class numerator
     (NetIncomeLossAvailableToCommonStockholders*) inside its filed rounding (_alloc_at_filed_precision; ABT); a filer whose quarter EPS
     is YTD less earlier quarters (AMBQ Q2 2026) ties only as a marked OVERRIDE checked on the filed figures (_ytd_override).
  2. The walk from the model formula to Zacks, every step a named filed line, summing EXACTLY to the residual eps_path reports:
       ebit       filed operating income in place of the model row's EBIT (adjusted-basis sets: the model's adjusted EBIT vs GAAP OI;
                  that difference comes back in `one_offs`)
       nonop      filed pre-tax less operating income, ex equity marks, in place of the model's interest (rate x net debt / 4, or the
                  set's own driver_build.quarters.interest / .other_income lines)
       marks      filed equity-security marks (Zacks keeps them only on GAAP-incl-marks names)
       tax        filed tax in place of the model's tax rate
       claims     filed NCI and preferred
       discontinued  filed income from discontinued operations
       shares     filed diluted shares in place of the model's share count
       one_offs   Zacks - filed GAAP - the named conversion: the items the company's non-GAAP takes out (zacks_basis.json)
     Discontinued operations (filed) are a step of their own.
  3. Forecast check (the APP/BKNG miss): a set whose reported quarters show non-operating income ex marks the model's forecast quarters
     do not carry is a FAIL: the forecast quarters must carry a sourced non-operating line (driver_build.quarters.other_income and/or
     .interest) or the set must name, per quarter, why the filed amount is not recurring (balance.nonop_excluded: {end: reason, source}).
     No tolerance (Kilian 2026-11-23: "it shouldn't miss at all"). Used by name_update finish and run across all sets nightly.
     A reported quarter that does not rebuild to its filed EPS also fails. NO OVERRIDE (Kilian 2026-11-25: "there is absolutely no bypass
     permitted"): refuse() takes no reason; nonop_excluded entries need reason + source and are shown to Kilian at finish (kilian_lines).

Research only: it measures, it never changes a model.
CLI: python3 eps_tieout.py [SYM ...]   (no symbols: every name in data/eps_bridge.json, except removed names)"""
import datetime as dt, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'routines'))
OUT = os.path.join(HERE, 'data', 'eps_tieout.json')
CENT = 0.005 + 1e-9  # filed EPS is rounded to the cent: a rebuild within half a cent ties


XC = dict(disc=['IncomeLossFromDiscontinuedOperationsNetOfTax', 'IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity'],
          nonop_tag=['NonoperatingIncomeExpense'],  # beyond preprint_log.QC: discontinued operations; the filed non-operating subtotal
          avail_dil=['NetIncomeLossAvailableToCommonStockholdersDiluted'], avail_basic=['NetIncomeLossAvailableToCommonStockholdersBasic'],
          eps_basic=['EarningsPerShareBasic'])  # the EPS numerator the filer states (two-class / participating securities, ABT)
NUM = os.path.join(HERE, 'data', 'eps_numerator.json')  # filed numerator adjustments the tags do not carry (INTC escrowed-share gain), with source


def _num(sym):
    try: return {k[:10]: v for k, v in (json.load(open(NUM)).get(sym) or {}).items()}
    except FileNotFoundError: return {}


def _period(cf, tags, start, end):
    """One filed figure for an exact period (start, end), any of the tags, latest filing wins."""
    for t in tags:
        for tax in (cf.get('facts') or {}).values():
            for es in ((tax.get(t) or {}).get('units') or {}).values():
                v = [e for e in es if e.get('start') == start and e.get('end') == end]
                if v: return sorted(v, key=lambda e: e.get('filed', ''))[-1]['val']
    return None


def _alloc_at_filed_precision(ni, avail, dl, ep, bs, epb):
    """Two-class filers (ABT Q1 2026 Note 4: 'Net earnings allocated to common shares ... were $1.1 billion'): the EPS numerator is net
    earnings less the part allocated to participating securities (unvested restricted shares), stated only rounded. Like the share
    count in _shares_at_filed_precision: used only when net income as filed does not tie; the allocated amount is taken inside its
    filed rounding, at the centre of the range where it ties BOTH filed diluted and filed basic EPS. Nothing in range ties: None."""
    if avail is None or not dl or ep is None: return None, None
    step = next((p for p in (1e9, 1e8, 1e7, 1e6, 1e5, 1e4, 1e3) if abs(avail / p - round(avail / p)) < 1e-9), None)
    if step is None: return None, None
    lo, hi = max(avail - step / 2, (ep - CENT) * dl), min(avail + step / 2, (ep + CENT) * dl)
    if bs and epb is not None: lo, hi = max(lo, (epb - CENT) * bs), min(hi, (epb + CENT) * bs)
    if lo > hi: return None, None
    v = (lo + hi) / 2
    return v, (f'two-class: filed net income allocated to common shares {avail / 1e6:,.0f}M is rounded to {step / 1e6:g}M; rebuild uses '
               f'{v / 1e6:,.1f}M (participating securities {(ni - v) / 1e6:,.1f}M), the centre of the range inside that rounding that ties filed '
               f'diluted {ep:.2f} and basic {epb if epb is not None else "n/a"}')


def filings(sym):
    """preprint_log.filings_q plus discontinued operations and the non-operating subtotal (fills pre-tax income where a filer
    tags operating income and the non-operating subtotal but not pre-tax, e.g. MRVL's recent quarters)."""
    import preprint_log as pl
    F, cf = pl.filings_q(sym)
    for k, v in XC.items(): F[k] = pl.qvals(cf, v)
    _marks_from_parts(F, cf)
    _oi_from_statement(F, cf)
    _release_lines(sym, F)
    F['_cf'], F['_num'] = cf, _num(sym)
    return F


REL = os.path.join(HERE, 'data', 'release_lines.json')  # filed 8-K results-release lines for a quarter whose 10-K / 10-Q XBRL is not out yet


def _release_lines(sym, F):
    """Kilian card 2026-11-25 'Read Q4 from the 8-K release' (AMAT Q4 FY2026: release 2026-11-12, 10-K due December): a quarter
    between its results release (8-K Ex. 99.1) and the 10-K / 10-Q has no XBRL quarter, so the rebuild had nothing to read.
    data/release_lines.json holds that quarter's GAAP lines as the release prints them ($: pretax, ebit, tax, dil, eps, marks), each
    entry with its source. Used only for a quarter end XBRL does not carry; once the 10-K / 10-Q is filed its tags win and the entry
    is ignored. The rebuild is the same arithmetic; the row says it was rebuilt from the release."""
    try: R = (json.load(open(REL)).get(sym)) or {}
    except FileNotFoundError: return
    for end, v in R.items():
        if not v.get('source') or any(_val(F, k, end) is not None for k in ('pretax', 'ebit', 'tax', 'eps')): continue
        for k in ('pretax', 'ebit', 'tax', 'dil', 'eps', 'marks'):
            if v.get(k) is not None: F.setdefault(k, {})[end] = float(v[k])
        F.setdefault('release_source', {})[end] = v['source']


MARK_PARTS = dict(  # equity-security marks filed as realized and unrealized lines, not one combined tag (KEYS "gain (loss) on
    # equity investments" note; INTC unrealized). Each part: the net gain/loss tag, else gain less loss.
    realized=(['EquitySecuritiesFvNiRealizedGainLoss'], ['EquitySecuritiesFvNiRealizedGain'], ['EquitySecuritiesFvNiRealizedLoss']),
    unrealized=(['EquitySecuritiesFvNiUnrealizedGainLoss'], ['EquitySecuritiesFvNiUnrealizedGain'], ['EquitySecuritiesFvNiUnrealizedLoss']))


def _marks_from_parts(F, cf):
    """Quarters with no combined equity-securities mark (preprint_log.QC marks): realized + unrealized from their own tags. Each tag is
    read on its own, so a filer that moved between tags across quarters is still covered quarter by quarter."""
    import preprint_log as pl
    M = F.setdefault('marks', {}); src = F.setdefault('marks_source', {})
    parts = {}
    for name, (net, gain, loss) in MARK_PARTS.items():
        N, G, L = pl.qvals(cf, net), pl.qvals(cf, gain), pl.qvals(cf, loss)
        parts[name] = {e: N[e] if e in N else (G.get(e) or 0.0) - (L.get(e) or 0.0) for e in set(N) | set(G) | set(L)}
    for e in set(parts['realized']) | set(parts['unrealized']):
        if e in M: continue
        M[e] = (parts['realized'].get(e) or 0.0) + (parts['unrealized'].get(e) or 0.0)
        src[e] = 'realized + unrealized equity-securities tags (no combined EquitySecuritiesFvNiGainLoss)'


OI_STMT = dict(rev=['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax'], costs=['CostsAndExpenses'],
               intn=['InterestIncomeExpenseNet', 'InterestIncomeExpenseNonoperatingNet'], nonop_rest=['NonoperatingIncomeExpense'])


def _oi_from_statement(F, cf, tol=0.5e6):
    """Filers with no operating-income line on the face of the statement (DY: revenue - total costs and expenses, then 'interest
    expense, net' and 'other income, net' down to pre-tax): operating income = revenue - costs and expenses, used only for a quarter
    with no OperatingIncomeLoss tag AND where it ties to the filed pre-tax through the filed non-operating lines (pre-tax - interest,
    net - other income, net) within $0.5M, so no line can be left out unseen."""
    import preprint_log as pl
    X = {k: pl.qvals(cf, v) for k, v in OI_STMT.items()}
    F.setdefault('ebit_source', {})
    for e, pt in (F.get('pretax') or {}).items():
        if e in (F.get('ebit') or {}): continue
        r, c, i, n = (X[k].get(e) for k in ('rev', 'costs', 'intn', 'nonop_rest'))
        if None in (r, c, i, n) or abs((r - c) + i + n - pt) > tol: continue
        F.setdefault('ebit', {})[e] = r - c
        F['ebit_source'][e] = 'revenue - CostsAndExpenses (no OperatingIncomeLoss tag); ties to pre-tax via InterestIncomeExpenseNet + NonoperatingIncomeExpense'


def _shares_at_filed_precision(dl, ni, ep):
    """Diluted shares as the filing states them, at the precision it states them. A filer that reports shares in millions (KEYS: 173
    for Q3 FY26) states a count anywhere in 172.5-173.5M; dividing filed net income by the rounded 173 can land a cent off the filed EPS
    (397 / 173 = 2.295 vs 2.30) with every income line present. The rebuild then uses the count in that stated range closest to
    net income / filed EPS. Net income still has to come out to the filed EPS at a share count the filing allows, so a missing line
    larger than the rounding of the share count still shows. A count that ties as filed, or one filed to the share, is used as filed.
    Returns (shares, note)."""
    if not dl or ep is None or not ni or abs(ni / dl - ep) <= CENT: return dl, None  # ties as filed: count used as filed
    step = next((p for p in (1e6, 1e5, 1e4, 1e3) if abs(dl / p - round(dl / p)) < 1e-9), None)
    if step is None or not ep: return dl, None
    lo, hi = dl - step / 2, dl + step / 2
    s = min(max(ni / ep, lo), hi)
    if abs(s - dl) < 1 or abs(ni / s - ep) > CENT: return dl, None  # no count the filing allows ties: shown as filed, row fails
    return s, f'filed diluted shares {dl / 1e6:,.3f}M are rounded to {step / 1e6:g}M; rebuild uses {s / 1e6:,.3f}M (filed net income / filed EPS, inside that rounding)'


def _val(F, key, end, tol=20):
    d = dt.date.fromisoformat(end)
    for k, v in (F.get(key) or {}).items():
        if abs((dt.date.fromisoformat(k) - d).days) <= tol: return v
    return None


def _ytd_override(row, F, na, gaap, ep, end):
    """AMBQ Q2 2026: the filed quarter EPS is the filed year-to-date EPS less the filed earlier quarter(s), not the quarter's own net loss
    / shares (data/eps_numerator.json ytd_derived). Marked as an OVERRIDE (told to Kilian, RULES.md 29) only if the filed figures say so:
    the year-to-date and each earlier quarter rebuild to their filed EPS to the cent, and YTD EPS - earlier EPS = the filed quarter EPS."""
    cf = F['_cf']; start, prior_end = na['ytd_derived']
    ni_tags = ['NetIncomeLoss']; sh_tags = ['WeightedAverageNumberOfDilutedSharesOutstanding']; ep_tags = ['EarningsPerShareDiluted']
    y_ni, y_sh, y_ep = (_period(cf, t, start, end) for t in (ni_tags, sh_tags, ep_tags))
    p_ni, p_sh, p_ep = (_period(cf, t, start, prior_end) for t in (ni_tags, sh_tags, ep_tags))
    if None in (y_ni, y_sh, y_ep, p_ni, p_sh, p_ep): return
    ok = abs(y_ni / y_sh - y_ep) <= CENT and abs(p_ni / p_sh - p_ep) <= CENT and abs(round(y_ep - p_ep, 2) - ep) < 1e-9
    if not ok: return
    row.update(gaap_ties=True, override=True,
               override_note=f"OVERRIDE (RULES.md 29, told to Kilian): quarter rebuilds to {gaap:.3f}, filed {ep:.2f} = filed YTD {y_ep:.2f} "
                             f"({y_ni / 1e6:,.3f}M / {y_sh / 1e6:,.3f}M) - filed {prior_end} {p_ep:.2f} ({p_ni / 1e6:,.3f}M / {p_sh / 1e6:,.3f}M); "
                             f"{na['where']}; {na['source']}")


def model_nonop(P, q):
    """The model's pre-tax non-operating line for quarter row q, set units (positive = income)."""
    if 'interest' in q or 'other_income' in q: return (q.get('other_income') or 0.0) - (q.get('interest') or 0.0)
    return -P.rate * P.nd / 4


def walk(P, F, sc, h, q):
    """One reported quarter: dict of the walk (per share) from the model formula to Zacks, plus the GAAP rebuild check."""
    end = q['end']
    pt, eb, tx, mk, pf, nc, dl, ep, dc, nt = (_val(F, k, end) for k in ('pretax', 'ebit', 'tax', 'marks', 'pref', 'nci', 'dil', 'eps', 'disc', 'nonop_tag'))
    if pt is None and eb is not None and nt is not None: pt = eb + nt  # pre-tax = operating income + the filed non-operating subtotal (MRVL);
    # never the reverse: a filer's NonoperatingIncomeExpense can be "other income" alone, without interest (DY), which would misstate the split
    row = dict(end=end, zacks=None, filed_eps=ep)
    rs = _val(F, 'release_source', end)
    if rs: row['release_note'] = f'rebuilt from the results release (no XBRL quarter yet): {rs}'
    miss = [k for k, v in (('pretax', pt), ('operating income', eb), ('tax', tx), ('diluted shares', dl)) if v is None]
    if miss: row.update(status='no_filing', why=f"filed {', '.join(miss)} not found for {end}"); return row
    u = lambda x: (x or 0.0) / sc  # filed $ -> set units
    pt, eb, tx, mk, pf, nc, dc = u(pt), u(eb), u(tx), u(mk), u(pf), u(nc), u(dc)
    dl = dl / sc if sc > 1 else dl  # shares in the set's units (millions when money is in $M)
    ni = pt - tx - nc - pf + dc
    na = (F.get('_num') or {}).get(end[:10]) or {}
    adj, adj_note = 0.0, None
    if 'diluted_adj' in na:  # filed numerator adjustment from the EPS note / footnote (data/eps_numerator.json, sourced)
        adj = u(na['diluted_adj']); adj_note = f"filed diluted-EPS numerator adjustment {na['diluted_adj'] / 1e6:+,.1f}M: {na['what']} ({na['where']}; {na['source']})"
    elif ep is not None and abs(ni / dl - ep) > CENT:  # two-class numerator the filer tags (rounded): only when net income as filed misses
        av = _val(F, 'avail_dil', end); av = av if av is not None else _val(F, 'avail_basic', end)
        bs, epb = _val(F, 'basic', end), _val(F, 'eps_basic', end)
        v, note = _alloc_at_filed_precision(ni * sc, av, dl * sc, ep, bs, epb)
        if v is not None: adj, adj_note = v / sc - ni, note
    dl, dl_implied = _shares_at_filed_precision(dl * (sc if sc > 1 else 1.0), (ni + adj) * sc, ep)
    dl = dl / sc if sc > 1 else dl
    gaap = (ni + adj) / dl
    row.update(gaap_rebuilt=round(gaap, 4), gaap_ties=(ep is not None and abs(gaap - ep) <= CENT))
    if dl_implied: row['shares_note'] = dl_implied
    if adj_note: row['numerator_note'] = adj_note
    if not row['gaap_ties'] and na.get('ytd_derived'): _ytd_override(row, F, na, gaap, ep, end)
    if not row['gaap_ties']: row['why'] = f'rebuilt {gaap:.3f} vs filed {ep:.2f}: a filed line is missing from the rebuild'
    Sm = P.shares(end); t = q['tax']; Im = model_nonop(P, q)
    m = (q['ebit'] + Im) * (1 - t) / Sm if 'interest' not in q and 'other_income' not in q else \
        ((q['ebit'] + Im) * (1 - t) - q.get('preferred', 0.0)) / Sm
    fr = next((r for r in (P.B or {}).get('quarters') or [] if abs((dt.date.fromisoformat(r['end']) - dt.date.fromisoformat(end)).days) <= 20), None)
    conv, _ = P.conversion(q, m, fr)
    z = P._use(h, m + conv)
    nonop_x = pt - eb - mk
    m1 = (eb + Im) * (1 - t) / Sm
    m2 = (eb + nonop_x) * (1 - t) / Sm
    m3 = (eb + nonop_x + mk) * (1 - t) / Sm
    m4 = (pt - tx) / Sm
    m5 = (pt - tx - nc - pf) / Sm
    m6 = (pt - tx - nc - pf + dc) / Sm
    m7 = (pt - tx - nc - pf + dc + adj) / Sm
    steps = dict(ebit=m1 - m, nonop=m2 - m1, marks=m3 - m2, tax=m4 - m3, claims=m5 - m4, discontinued=m6 - m5, eps_numerator=m7 - m6, shares=gaap - m7,
                 one_offs=z - gaap - conv)
    resid = z - m - conv
    row.update(zacks=z, model_formula=round(m, 4), conversion=round(conv, 4), residual=round(resid, 4),
               walk={k: round(v, 4) for k, v in steps.items()}, walk_sum_ok=abs(sum(steps.values()) - resid) < 1e-9,
               lines=dict(model_ebit=round(q['ebit'], 1), filed_oi=round(eb, 1), model_nonop=round(Im, 1), filed_nonop_ex_marks=round(nonop_x, 1),
                          filed_marks=round(mk, 1), discontinued=round(dc, 1), model_tax=round(t, 4), filed_tax=round(tx / pt, 4) if pt else None,
                          model_shares=round(Sm, 2), filed_shares=round(dl, 2), nci=round(nc, 1), preferred=round(pf, 1)),
               status='ok')
    return row


def excluded(mi):
    """balance.nonop_excluded as {end: {reason, source}} plus the entries missing a reason or a source (Kilian 2026-11-25: the only
    route for a quarter a set does not carry; each needs both, and each is shown to Kilian at finish, not only logged)."""
    raw = ((mi.get('balance') or {}).get('nonop_excluded') or {})
    ok, bad = {}, []
    for end, v in raw.items():
        r = (v.get('reason') if isinstance(v, dict) else None) or ''
        src = (v.get('source') if isinstance(v, dict) else None) or ''
        if str(r).strip() and str(src).strip(): ok[str(end)[:10]] = dict(reason=str(r).strip(), source=str(src).strip())
        else: bad.append(str(end)[:10])
    return ok, bad


def forecast_check(P, rows):
    """FAIL when reported quarters show non-operating income ex marks and the forecast quarters carry no sourced line for it, when a
    reported quarter does not rebuild to its filed EPS (RULES.md 29: not a pass), or when a nonop_excluded entry lacks a reason or source."""
    excl, bad_excl = excluded(P.mi)
    if bad_excl:
        return dict(ok=False, carries_line=None, how=f"balance.nonop_excluded entries without both a reason and a source: {', '.join(bad_excl)}")
    untied = [r for r in rows if r.get('status') == 'ok' and not r.get('gaap_ties')]
    if untied:  # Kilian 2026-11-25 "absolutely no bypass": a quarter that does not rebuild to the filed EPS stops the finish
        return dict(ok=False, carries_line=None, how='reported quarter(s) do not rebuild to the filed EPS: ' + '; '.join(f"{r['end'][:10]} {r.get('why')}" for r in untied)
                    + ': find the missing filed line (eps_tieout.XC / data/eps_numerator.json, sourced)')
    fq = [q for q in P.Q if not q.get('actual')]
    carries = bool(fq) and all(('other_income' in q or 'interest' in q) for q in fq)
    nof = [r for r in rows if r.get('status') != 'ok']
    if nof and not all(r['end'][:10] in excl for r in nof):  # nothing passes unseen: a quarter the filings cannot rebuild is not a pass
        return dict(ok=False, carries_line=carries, how='reported quarters not checked: ' + '; '.join(r.get('why', '') for r in nof)
                    + ': add the filed lines from the 10-Q / release to balance.nonop_excluded (reason, source) or fix the XBRL concepts in eps_tieout.XC')
    shown = [r for r in rows if r.get('status') == 'ok' and abs(r['lines']['filed_nonop_ex_marks']) > 0 and r['end'][:10] not in excl]
    if not shown or carries: return dict(ok=True, carries_line=carries,
                                         how='forecast quarters carry a sourced non-operating line' if carries else 'no filed non-operating income ex marks')
    return dict(ok=False, carries_line=False,
                how='reported quarters show non-operating income ex marks (' + ', '.join(f"{r['end'][:7]} {r['lines']['filed_nonop_ex_marks']:+,.1f}" for r in shown)
                    + ') but the forecast quarters use only rate x net debt: add driver_build.quarters.other_income / .interest from the filings, '
                      'or name each quarter in balance.nonop_excluded with the reason and source')


def run(sym, hist, log=True):
    import eps_path as ep, preprint_log as pl
    P = ep.path(sym, hist, log=False)
    F = filings(sym)
    sc = 1e6 if (P.mi.get('unit') or '$M') != '$' else 1.0
    rows = [walk(P, F, sc, h, q) for h, q in P.pairs]
    out = dict(sym=sym, model_date=P.R, logged_utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
               zacks_measure=(P.B or {}).get('zacks'), quarters=rows, forecast=forecast_check(P, rows))
    if log:
        L = json.load(open(OUT)) if os.path.exists(OUT) else {}
        L[sym] = out; tmp = f'{OUT}.{os.getpid()}.tmp'; json.dump(L, open(tmp, 'w'), indent=1, default=str); os.replace(tmp, OUT)
    return out


def refuse(U, s, stage='finish'):
    """name_update finish (Kilian 2026-11-23; hard stop with NO override, Kilian 2026-11-25: "there is absolutely no bypass permitted").
    Stops when this update's set leaves out non-operating income its filings show (forecast_check), when a reported quarter does not
    rebuild to the filed EPS, or when the set cannot be checked at all. Prints the walk either way. Returns the forecast check plus
    `excluded`: every balance.nonop_excluded quarter with its reason and source, which finish lists for the reply to Kilian.
    The only route for a quarter the set does not carry is balance.nonop_excluded (reason + source); there is no --why."""
    import eps_path as ep, model_engine as me
    def stop(msg):
        sys.exit(f'REFUSED (eps-tieout, {stage}): {s} {U}: {msg} (valuation/eps_tieout.py, RULES.md 29). No override exists (Kilian 2026-11-25: '
                 '"there is absolutely no bypass permitted"). Fix the set, or, for a quarter the set does not carry, name it in '
                 'balance.nonop_excluded with reason and source. If a fix seems impossible, tell Kilian the reason and ask.')
    f = os.path.join(HERE, 'runs', U, s, 'supervisor.json')
    try: mi = me.extract(me.load(f))
    except Exception as e: stop(f'set not read ({str(e)[:120]}): an unchecked set is not a pass')
    if not mi.get('_quarters') and not (mi.get('driver_build') or {}).get('quarters'):
        print(f'eps_tieout: {s} {U}: annual set, no engine quarter EPS; not applicable'); return dict(ok=True, how='annual set', excluded={})
    try: P = ep.path(s, _hist(s), ms=(U, os.path.relpath(f, HERE), mi), log=False)
    except ep.PathError as e: stop(f'{e}: an unchecked set is not a pass')
    F = filings(s)
    sc = 1e6 if (P.mi.get('unit') or '$M') != '$' else 1.0
    rows = [walk(P, F, sc, h, q) for h, q in P.pairs]; fc = forecast_check(P, rows)
    for r in rows:
        if r.get('status') == 'ok':
            print(f"eps_tieout {s} {r['end']}: Zacks {r['zacks']:.2f} = model {r['model_formula']:.3f} + conversion {r['conversion']:+.3f} + "
                  + ' + '.join(f'{k} {v:+.3f}' for k, v in r['walk'].items() if abs(v) >= 0.0005) + ('' if r['gaap_ties'] else f"  [{r.get('why')}]") + (f"  [{r['override_note']}]" if r.get('override') else ''))
        else: print(f"eps_tieout {s} {r['end']}: {r.get('why')}")
    if not fc['ok']: stop(fc['how'])
    excl, _ = excluded(P.mi)
    fc['excluded'] = excl
    fc['ytd_derived'] = [r['override_note'] for r in rows if r.get('override')]
    return fc


def kilian_lines(fc):
    """Lines finish puts in its flags (the reply to Kilian quotes them): each excluded quarter with reason and source."""
    if not fc: return []
    out = [f"EPS tie-out: {end} non-operating income left out of the forecast (balance.nonop_excluded): {v['reason']} [source: {v['source']}]"
           for end, v in sorted((fc.get('excluded') or {}).items())]
    return out + [f'EPS tie-out: {n}' for n in fc.get('ytd_derived') or []]


def _hist(sym):
    import importlib.util
    sp = importlib.util.spec_from_file_location('cc_tie', os.path.join(HERE, 'scoring', 'consensus_calls.py')); cc = importlib.util.module_from_spec(sp)
    a = sys.argv; sys.argv = [a[0]]
    try: sp.loader.exec_module(cc)
    finally: sys.argv = a
    return ((cc.jl(cc.CAL, {}).get(sym)) or {}).get('history') or []


def main(argv):
    import universe
    syms = argv or [s for s in json.load(open(os.path.join(HERE, 'data', 'eps_bridge.json'))) if s not in set(universe.removed_names() if hasattr(universe, 'removed_names') else [])]
    bad = 0
    for s in syms:
        try: o = run(s, _hist(s))
        except Exception as e: print(f'{s}: not run: {str(e)[:120]}'); continue
        print(f"== {s} (model {o['model_date']}) forecast: {'OK' if o['forecast']['ok'] else 'FAIL'}: {o['forecast']['how']}")
        bad += not o['forecast']['ok']
        for r in o['quarters']:
            if r.get('status') != 'ok': print(f"  {r['end']}: {r.get('why')}"); continue
            w = ', '.join(f'{k} {v:+.3f}' for k, v in r['walk'].items() if abs(v) >= 0.0005)
            print(f"  {r['end']}: Zacks {r['zacks']:.2f} model {r['model_formula']:.3f} conv {r['conversion']:+.3f} residual {r['residual']:+.3f} = {w}"
                  f" | GAAP rebuilt {r['gaap_rebuilt']:.3f} vs filed {r['filed_eps']} {'ties' if r['gaap_ties'] else 'DOES NOT TIE'}")
            for k in ('numerator_note', 'shares_note', 'override_note', 'release_note'):
                if r.get(k): print(f'      {k}: {r[k]}')
    return 1 if bad else 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
