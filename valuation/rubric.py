"""Conviction rubric (Kilian 2026-10-05 15:42Z chose option A, "0.42, 7%, 1.3"; spec adhoc/conviction_rubric_draft.md).

The old-scale label that gates new buys is computed here from numbers, not chosen by the supervisor:
  p  = book-level increment dP (book_dp.py, Kilian 2026-10-15 05:33Z card "Use increment"): change in P(book's 12M Sharpe
       beats SPY's) when the name joins the book at its solo Sharpe-test size. Until 2026-10-15 p was the supervisor's
       P(the stock's own 12M Sharpe beats SPY's); that is still reported as p_supervisor but no longer gates.
  u  = target / spot - 1, newest result.json
  rr = reward_risk in that result.json ((take-profit - spot) / (spot - stop))
  fvu = fair value / spot - 1 (Kilian 2026-10-05 16:20Z "also include fair value in the rubric threshold")
  u and fvu both add the trailing dividend yield (divs.py, 2026-10-12), so the bars are total returns
  high:   dP >= +0.03, u >= 15%, rr >= 1.5, fvu >= 15%      medium: dP >= +0.01, u >= 7%, rr >= 1.3, fvu >= 7%      else low
Down one notch (one in total) for open flags the supervisor lists in sizing.open_flags (unresolved fact-check failure,
thesis-break, accounting or credit flag) or when round-2 agent fair values span more than 25% of spot. Overlap with
holdings never lowers it (the optimizer prices correlation). The supervisor may move it one notch with
sizing.conviction_override + sizing.conviction_override_reason; the page shows the reason.
Usage: python3 rubric.py [SYM ...]  prints the label and inputs.
"""
import glob, json, os, sys
import divs
HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = ['low', 'medium', 'high']
TH = {'high': (0.03, 0.15, 1.5, 0.15), 'medium': (0.01, 0.07, 1.3, 0.07)}  # (book dP, upside, reward/risk, fair-value upside); dP bars since 2026-10-15 (were p 0.52 / 0.42)
SPAN_MAX = 0.25

_IDX = {}
def _newest(sym, name):  # one listing per file name per process (a glob per name took minutes on the shared folder)
    if name not in _IDX:
        _IDX[name] = {}
        for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*', '*', name))): _IDX[name][f.split(os.sep)[-2]] = f
    return _IDX[name].get(sym)

def warm(syms):
    """Read the files label() reads for these names, 12 at a time, when the read-once cache (fscache.py) is on, so the
    callers' one-name-at-a-time loops find them in memory (2026-10-21; labels unchanged)."""
    fc = sys.modules.get('fscache')
    if not (fc and fc._real): return
    import concurrent.futures as cf
    def one(s):
        try: label(s, 0.0)
        except Exception: pass
    with cf.ThreadPoolExecutor(12) as ex: list(ex.map(one, syms))


def label(sym, dp=None):  # dp: book-level increment from allocate.py; else the newest runs/*/book_dp.json
    rp = _newest(sym, 'result.json')
    g = (json.load(open(rp)).get('citation_gate') or {}) if rp else {}  # 2026-10-06: only research that passed the citation gate
    sp = os.path.join(HERE, 'runs', g['research'], sym, 'supervisor.json') if g.get('research') else _newest(sym, 'supervisor.json')
    if sp and not os.path.exists(sp): sp = None
    if not sp or not rp: return None
    sup, res = json.load(open(sp)), json.load(open(rp))
    sz = sup.get('sizing') or {}
    if not sz.get('confidence') and os.path.exists(os.path.join(os.path.dirname(sp), 'sizing_pass.json')):
        sz = json.load(open(os.path.join(os.path.dirname(sp), 'sizing_pass.json')))
    p_sup, spot = sz.get('confidence'), res['spot']
    if dp is None:
        import book_dp; _t = book_dp.lookup(sym); dp = _t['dp'] if _t else None
    if not isinstance(dp, (int, float)): return None
    p = dp
    u, rr = res['target'] / spot - 1, res.get('reward_risk') or 0
    sfv = sup.get('fair_value'); sfv = sfv.get('value') if isinstance(sfv, dict) else sfv
    fv = res.get('fair_value_median') or sfv  # supervisor's reconciled fair value
    fvu = fv / spot - 1 if isinstance(fv, (int, float)) else -1.0
    dyl = divs.dy(sym, spot); u += dyl; fvu += dyl  # Kilian 2026-10-12: upside bars count the year's dividends (target and FV are ex-dividend prices)
    base = next((k for k in ('high', 'medium') if p >= TH[k][0] and u >= TH[k][1] and rr >= TH[k][2] and fvu >= TH[k][3]), 'low')
    fvs = [((json.load(open(a)).get('fair_value') or {}).get('value')) for a in glob.glob(os.path.join(os.path.dirname(sp), 'agent_*.json'))]
    fvs = [v for v in fvs if isinstance(v, (int, float))]
    span = (max(fvs) - min(fvs)) / spot if len(fvs) > 1 else 0.0
    flags = list(sz.get('open_flags') or [])
    why = []
    if flags: why.append('open flags: ' + '; '.join(map(str, flags)))
    if span > SPAN_MAX: why.append(f'agent fair values span {span:.0%} of spot')
    lab = LEVELS[max(0, LEVELS.index(base) - (1 if why else 0))]
    ov, ovr = sz.get('conviction_override'), sz.get('conviction_override_reason')
    if ov in LEVELS and ovr and abs(LEVELS.index(ov) - LEVELS.index(lab)) == 1: lab, override = ov, ovr
    else: override = None
    return dict(label=lab, base=base, p=p, p_kind='book_dp', p_supervisor=p_sup, upside=round(u, 4), reward_risk=rr, fair_value=fv, fv_upside=round(fvu, 4), div_yield=round(dyl, 4), span=round(span, 3), notch_down=why,
                override_reason=override, research=sp.split('/')[-3], priced=rp.split('/')[-3])

if __name__ == '__main__':
    syms = sys.argv[1:] or sorted({p.split('/')[-2] for p in glob.glob(os.path.join(HERE, 'runs', '*', '*', 'supervisor.json'))})
    for s in syms:
        r = label(s)
        if r: print(f"{s:6} {r['label']:6} (base {r['base']}) dP={r['p']:+.3f} u={r['upside']:+.1%} rr={r['reward_risk']} fvu={r['fv_upside']:+.1%} span={r['span']:.0%} {r['notch_down'] or ''} {r['research']}/{r['priced']}")
