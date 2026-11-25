"""Growth term of the exit multiple (Kilian 2026-11-10, thread "Adjust exit multiples for slowing growth": "yes use the split").

The k = 0.8 rule (peer median x (own NTM / median)^0.8) holds the multiple's growth context fixed. When forward growth slows
(or speeds up) between now and the horizon, the horizon multiple is scaled by

    factor = exp(0.193 x (dG_cons + 0.00165) + 0.097 x (dG_ours - dG_cons))

  G(t)    forward EPS growth FY2/FY1 - 1 as I/B/E/S maps it at date t (FY1 = first fiscal year not yet reported; a year is
          treated as reported 50 days after its end).
  dG      G(horizon) - G(as_of). dG_ours from the set's own NOPAT path; dG_cons from consensus EPS (data/estimates/SYM.json,
          nasdaq_est.py, Zacks via Nasdaq.com), years matched by fiscal year end.
  0.193   slope of the k-rule miss on the slowdown expected at t (WRDS 2005-24 walk-forward; t 3.6; 2000-02 bust 0.26).
  0.097   surprise slope 0.457 x forecaster hit rate 0.212 (share of an analyst's growth gap to consensus that comes true,
          I/B/E/S 2000-23): the part of our path that differs from consensus counts for less.
  0.00165 pooled mean expected dG (centres the term).
Evidence: wrds/results/exit_multiple_growth/README.md. A PEG rule and the cross-sectional slope were tested and rejected.
The factor multiplies every horizon multiple (P/E, EV/EBITDA, EV/sales, EV/EBIT, SOTP segment multiples; FCF yield is divided);
the P/E test is applied to EV multiples as a stated extension. If consensus lacks a year or is distorted (one-off gains in
FY1, e.g. GOOGL's stake marks), pass --no-consensus: the whole of dG_ours then takes 0.193 (flag it).
Record in the set as comps_summary.growth_term {factor, dG_ours, dG_cons, consensus_source, retrieved_utc} and apply it to the
multiple values; a factor that moves the research target (12m) by more than 5% is a card (judgment_check.py).

  python3 exit_growth.py SYM [ROUND] [--no-consensus]     prints the factor and the engine-sized effect on the live set
"""
import sys, json, math, copy, datetime as dt, os
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
B_EXP, B_DIFF, MEAN, REP = 0.193, 0.457 * 0.212, -0.00165, 50


def _fy1(ends, t):
    for j, e in enumerate(ends):
        if e + dt.timedelta(days=REP) > t: return j


def factor(mi, as_of, sym, use_consensus=True):
    import model_engine as me
    rows = me.project(me.resolve_drivers(me.apply_buyback_reprice(mi)[0]))
    fy0 = dt.date.fromisoformat(mi['base']['fy_end'])
    ends = [fy0 + dt.timedelta(days=round(365.25 * r['i'])) for r in rows]
    t0 = dt.date.fromisoformat(as_of); H = t0 + dt.timedelta(days=365)
    j0, jH = _fy1(ends, t0), _fy1(ends, H)
    if j0 is None or jH is None or jH + 1 >= len(rows): raise ValueError(f'{sym}: projection too short for G at the horizon')
    e = [r['nopat'] for r in rows]
    G = lambda j: e[j + 1] / e[j] - 1
    dO = G(jH) - G(j0); out = dict(G_now_ours=G(j0), G_H_ours=G(jH), dG_ours=dO, years=[r.get('label') for r in rows])
    dC = None
    if use_consensus:
        f = os.path.join(HERE, 'data', 'estimates', f'{sym}.json')
        d = json.load(open(f)); recs = [r for r in d['records'] if r.get('horizon') == 'fiscal year' and r.get('eps_estimate_average')]
        def cons(endd):
            best = min(recs, key=lambda r: abs((dt.date.fromisoformat(r['date']) - endd).days), default=None)
            return float(best['eps_estimate_average']) if best and abs((dt.date.fromisoformat(best['date']) - endd).days) <= 40 else None
        c = [cons(x) for x in ends]
        need = [j0, j0 + 1, jH, jH + 1]
        if all(c[j] is not None and c[j] > 0 for j in need):
            dC = (c[jH + 1] / c[jH] - 1) - (c[j0 + 1] / c[j0] - 1)
            out.update(G_now_cons=c[j0 + 1] / c[j0] - 1, G_H_cons=c[jH + 1] / c[jH] - 1, dG_cons=dC,
                       consensus_source=d.get('source', ''), retrieved_utc=d.get('retrieved_utc'))
        else: out['flag'] = 'consensus missing a year: whole dG_ours at 0.193'
    else: out['flag'] = 'consensus not used (--no-consensus): whole dG_ours at 0.193'
    out['factor'] = math.exp(B_EXP * ((dC if dC is not None else dO) - MEAN) + (B_DIFF * (dO - dC) if dC is not None else 0))
    return out


def scaled(mi, f):
    mi = copy.deepcopy(mi)
    hms = (mi.get('horizon_multiples') or []) + ([mi['horizon_multiple']] if mi.get('horizon_multiple') else [])
    for h in hms: h['value'] = h['value'] / f if h['metric'] == 'FCF yield' else h['value'] * f
    for s in mi.get('segments') or []: s['multiple'] *= f
    return mi


if __name__ == '__main__':
    import name_update as nu, model_engine as me
    a = [x for x in sys.argv[1:] if not x.startswith('--')]; sym = a[0]
    rd = a[1] if len(a) > 1 else nu.rounds(sym)[-1]; mi = nu._set_of(rd, sym)
    o = factor(mi, rd, sym, '--no-consensus' not in sys.argv)
    base = me.value(mi, rd)['horizon_value']; v = me.value(scaled(mi, o['factor']), rd)['horizon_value']
    o.update(round=rd, live=round(base, 2), adjusted=round(v, 2), effect_pct=round(100 * (v / base - 1), 2))
    print(json.dumps({k: (round(x, 4) if isinstance(x, float) else x) for k, x in o.items()}, indent=1))
