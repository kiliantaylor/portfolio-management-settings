"""12-1 momentum tilt input (Kilian 2026-10-30 card "Add a momentum tilt for bottom-fifth 12-1 losers?" -> "Add -3% centre
shift"; backtest wrds/code/falling_knife.py, results wrds/results/falling_knife.csv).

    python3 momentum.py D [SYM ...]      (no SYMs: every researched name, active_names.universe(D))

12-1 momentum = close 21 sessions before D's last close over the close 252 sessions before it, minus 1 (Yahoo daily,
split- and dividend-adjusted). Each name is ranked against the S&P 500 constituents' 12-1 momentum on the same dates (the
live stand-in for the backtest's $1B+ universe; list from Wikipedia, cached in data/sp500.json and refreshed when older
than 30 days). Bottom fifth (pct < 0.20) gets log_shift = ln(1 - 0.03); every other name 0. combine.py adds it to the
EPS-revision and days-to-cover shifts (momentum_tilt()). Writes data/momentum.json; a failed fetch keeps the old file."""
import datetime as dt, io, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'momentum.json'); SPF = os.path.join(HERE, 'data', 'sp500.json')
SHIFT_PCT = -0.03; BOTTOM = 0.20; SKIP, LOOK = 21, 252


def sp500():
    old = None
    if os.path.exists(SPF):
        old = json.load(open(SPF))
        if (dt.date.today() - dt.date.fromisoformat(old['as_of'])).days <= 30: return old['symbols']
    try:
        import pandas as pd, requests
        h = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers={'User-Agent': 'research-script/1.0'}, timeout=30).text
        syms = sorted(pd.read_html(io.StringIO(h))[0]['Symbol'].str.replace('.', '-').tolist())
        if len(syms) > 450:
            json.dump(dict(as_of=dt.date.today().isoformat(), source='Wikipedia List of S&P 500 companies', symbols=syms), open(SPF, 'w'))
            return syms
    except Exception as e:
        print('S&P 500 list fetch failed:', e)
    if old: return old['symbols']
    raise SystemExit('no S&P 500 list')


def main(D, names):
    import pandas as pd, yfinance as yf
    if not names:
        import active_names; names = sorted(active_names.universe(D))
    spx = sp500(); tick = sorted(set(spx) | set(names))
    end = (dt.date.fromisoformat(D) + dt.timedelta(days=1)).isoformat()
    start = (dt.date.fromisoformat(D) - dt.timedelta(days=420)).isoformat()
    px = yf.download(tick, start=start, end=end, auto_adjust=True, progress=False, threads=True)['Close']
    px = px[px.index <= pd.Timestamp(D)]
    if len(px) < LOOK + 1: raise SystemExit(f'only {len(px)} sessions to {D}')
    a, b = px.iloc[-1 - SKIP], px.iloc[-1 - LOOK]
    mom = (a / b - 1).dropna()
    ref = mom.reindex([s for s in spx if s in mom.index]).dropna().sort_values()
    cutoff = float(ref.quantile(BOTTOM))
    pct = lambda m: float((ref < m).mean())
    out = dict(as_of=D, last_close=str(px.index[-1].date()), skip_date=str(px.index[-1 - SKIP].date()),
               start_date=str(px.index[-1 - LOOK].date()), universe='S&P 500', n_universe=int(len(ref)),
               bottom_fifth_cutoff=round(cutoff, 4), shift_pct=SHIFT_PCT, log_shift_bottom=round(math.log1p(SHIFT_PCT), 6), names={})
    for s in names:
        if s not in mom.index: out['names'][s] = dict(mom=None, pct=None, bottom_fifth=False, log_shift=0.0, reason='no price history'); continue
        m = float(mom[s]); p = pct(m); bot = p < BOTTOM
        out['names'][s] = dict(mom=round(m, 4), pct=round(p, 4), bottom_fifth=bot, log_shift=round(math.log1p(SHIFT_PCT), 6) if bot else 0.0)
    tmp = OUT + '.tmp'; json.dump(out, open(tmp, 'w'), indent=1); os.replace(tmp, OUT)
    print(json.dumps(dict(as_of=D, cutoff=out['bottom_fifth_cutoff'], names=len(names),
                          bottom=sorted(s for s, r in out['names'].items() if r['bottom_fifth']))))


if __name__ == '__main__':
    main(sys.argv[1], [s.upper() for s in sys.argv[2:]])
