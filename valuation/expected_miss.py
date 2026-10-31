"""Expected-miss add hold (Kilian 2026-10-30 01:45Z card 'Hold adds before expected misses', AMZN Q3 thread): when OUR model's EPS for
a name's next unreported quarter is 5% or more below the Street (Zacks consensus), adds wait until after that print. Applies to every
name (held adds, staged tranches, new buys). Never sells: trims, exits and stops are unchanged.

  python3 expected_miss.py D [SYM ...]    nightly (step 3b0, after earnings_miss.py): writes data/expected_miss.json
  python3 expected_miss.py --show         prints the current records

Per name:
  quarter  = the model's first unreported quarter (quarterly sets: quarters.actual), else the next-report text's fiscal quarter
  ours     = routines/preprint_log.our_quarter (the same number the pre-print log files: model quarter on the Zacks basis)
  street   = Nasdaq.com analyst earnings forecast (Zacks consensus) for that quarter, else the next-report text's consensus
  gap      = ours / street - 1 (street > 0); hold when gap <= -GAP and |gap| <= preprint_log.CHECK (beyond that it is a basis question,
             flagged, no hold)
  block    = from D until the day after the next report date (unknown date: quarter end + 45 days, provisional; re-set nightly, and the
             hold ends once that quarter shows in the reported history)
earnings_miss.blocks(D) adds these records to the big-miss blocks, so review.py and allocate.py hold adds the same way.
"""
import datetime as dt, json, os, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'routines'))
OUT = os.path.join(HERE, 'data', 'expected_miss.json')
GAP = 0.05


def street_q(sym, fq):
    """Zacks consensus EPS for fiscal quarter fq ('Dec 2026') from Nasdaq.com's earnings forecast, else None."""
    u = f'https://api.nasdaq.com/api/analyst/{sym}/earnings-forecast'
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}), timeout=30))['data']
        for r in ((d or {}).get('quarterlyForecast') or {}).get('rows') or []:
            if str(r.get('fiscalEnd', '')).strip() == fq: return float(r['consensusEPSForecast']), u
    except Exception: pass
    return None, u


def model_quarter(sym):
    """('Dec 2026', end) of the model's first unreported quarter for quarterly sets, else (None, None)."""
    import preprint_log as pl
    ms = pl.model_set(sym)
    if not ms: return None, None
    q = (ms[2].get('quarters') or {})
    try:
        e = dt.date.fromisoformat(q['ends'][int(q['actual'])]); return e.strftime('%b %Y'), e.isoformat()
    except Exception: return None, None


def model_eps(sym, fq):
    """The quarterly set's own EPS for fiscal quarter fq, else None."""
    import copy, preprint_log as pl, model_engine as me
    ms = pl.model_set(sym)
    if not ms: return None
    try:
        val = me.value(dict(copy.deepcopy(ms[2]), _allow_long=True), ms[0])
        qd = pl.mend(pl.mkey(fq))
        r = next((r for r in val.get('quarters') or [] if not r['actual'] and abs((dt.date.fromisoformat(r['end']) - qd).days) <= 20), None)
        return None if r is None else float(r['eps'])
    except Exception: return None


def build(D, syms=None):
    import preprint_log as pl
    out = dict(warnings=[])
    if not syms: syms, _ = pl.names(D, out)
    C = pl.calendar(syms, out)
    recs = {}
    for s in syms:
        c = C.get(s) or {}; nr = c.get('next_report') or {}; hist = c.get('history') or []
        fq, _ = model_quarter(s)
        fq = fq or pl.fq_of(nr, hist)
        if not fq: continue
        if any(h.get('fiscal_quarter') == fq for h in hist): continue  # already reported; the model update has not rolled yet
        st, src = street_q(s, fq)
        if st is None and pl.fq_of(nr, hist) == fq: st, src = pl.street_eps(nr), 'Nasdaq.com next-report text (Zacks consensus)'
        if st is None or st <= 0: continue
        try: ours, note, det = pl.our_quarter(s, dict(fiscal_quarter=fq), hist)
        except Exception as e: ours, note = None, f'derivation failed: {str(e)[:100]}'
        if ours is None:  # quarterly set, preprint_log cannot bridge (e.g. fewer than 4 reports in the calendar): the model's own quarter EPS
            mq = model_eps(s, fq)
            if mq is None: continue
            ours, note = mq, f'model quarter EPS {mq:.3f} on the set\'s own basis (preprint_log: {note})'
        gap = ours / st - 1
        r = dict(fiscal_quarter=fq, ours_eps=round(ours, 4), street_eps=st, gap_pct=round(100 * gap, 1), ours_note=note[:300],
                 street_source=src, next_report=nr.get('date'), computed_D=D)
        if gap <= -GAP and abs(gap) <= pl.CHECK:
            nd = nr.get('date')
            qe = dt.date(*map(int, (pl.mend(pl.mkey(fq)).isoformat()).split('-')))
            until = (dt.date.fromisoformat(nd) + dt.timedelta(days=1)).isoformat() if nd and nd >= D else (qe + dt.timedelta(days=45)).isoformat()
            r.update(block_from=D, block_until=until, expected_miss=True,
                     reason=f"our {fq} EPS {ours:.2f} is {-gap:.0%} below the Street {st:.2f}: adds wait until after that print"
                            + (f" ({nd})" if nd and nd >= D else f' (date not announced; provisional {until}, re-set nightly)'),
                     source='expected_miss.py (Kilian 2026-10-30 card)')
        elif gap <= -GAP:
            r['basis_check'] = f'gap {gap:+.0%} is beyond {pl.CHECK:.0%}: likely a basis gap, no hold'; out['warnings'].append(f'{s}: {r["basis_check"]}')
        recs[s] = r
    recs['_meta'] = dict(D=D, gap=GAP, computed_utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), warnings=out['warnings'][:30])
    tmp = OUT + '.tmp'; json.dump(recs, open(tmp, 'w'), indent=1); os.replace(tmp, OUT)
    return recs


def blocks(D):
    """{SYM: record} holding adds at market date D. Never raises."""
    try:
        d = json.load(open(OUT)); out = {}
        for s, r in d.items():
            if s.startswith('_') or not isinstance(r, dict) or not r.get('expected_miss'): continue
            if r['block_from'] <= D < r['block_until']: out[s] = r
        return out
    except Exception as e:
        print('expected_miss.blocks failed, no expected-miss holds applied:', e, file=sys.stderr); return {}


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: raise SystemExit(__doc__)
    if a[0] == '--show': print(json.dumps(json.load(open(OUT)), indent=1)); raise SystemExit
    R = build(a[0], [x.upper() for x in a[1:]] or None)
    for s, r in R.items():
        if not s.startswith('_'): print(f"{s:6} {r['fiscal_quarter']} ours {r['ours_eps']} street {r['street_eps']} gap {r['gap_pct']:+.1f}%" + ('  HOLD ADDS' if r.get('expected_miss') else ''))
