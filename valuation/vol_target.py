"""Book volatility target (Kilian 2026-11-24 17:38Z card 'Build 20% vol target instead', thread 'Test long-only drawdown
controls'; backtest audit/dd_controls_1124/report.md, dd_controls_test.py x_paths 'vt 0.2/21').

Each close: measure the realized vol of the base book (what normal sizing would hold after today's trades: held names
plus today's new buys, as shares of the sizing book, cash in the 4% sleeve at zero vol) over the last WINDOW (21) trading
days, weights fixed at today's, then

    x_raw = min(1, TARGET / vol)            TARGET 20%, never above 1: no leverage, no index product
    x     = x_raw if |x_raw - x_prev| >= STEP (0.10) else x_prev      x_prev = the last close before D (1.0 at the start)

x scales the stock sleeve: every held-name target, the next-share ladder and the caps are x times what normal sizing
wants (review.py: the same problem with gamma* / x and the caps x cap), new buys too (allocate.py, x of the last close).
The freed money stays in the 4% cash sleeve. review_settings.json vol_target {mode, target, window, step}:
  mode 'off'    nothing computed;
  mode 'shadow' x computed, recorded in data/vol_target.json and shown on the page, nothing scaled (dry run);
  mode 'live'   x scales sizing.
data/vol_target.json {history: [{date, vol, x_raw, x, x_prev, moved, mode, n_days, reason}]}: one row per close date
(a re-run of the same date replaces its row; LIVE_QUOTES runs read the row and never write).

Usage: python3 vol_target.py show            the latest rows
       python3 vol_target.py vol D SYM=w ...  realized vol of a weight set to D's close (check)"""
import json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'data', 'vol_target.json')
DEFAULTS = dict(mode='off', target=0.20, window=21, step=0.10)


def settings(cfg=None):
    if cfg is None:
        try: cfg = json.load(open(os.path.join(HERE, 'data', 'review_settings.json')))
        except Exception: cfg = {}
    return dict(DEFAULTS, **{k: v for k, v in (cfg.get('vol_target') or {}).items() if not k.startswith('_')})


def load():
    try: return json.load(open(STATE))
    except Exception: return {'history': []}


def row_for(date):
    """The stored row for close date D, if any."""
    return next((r for r in load().get('history', []) if r['date'] == date), None)


def prev_x(date, mode=None):
    """x at the last close before D (1.0 when there is none: the book starts at full sizing). In mode 'live' a shadow row
    does not count (the book was never sized at it), so the first live close steps from 1.0."""
    rows = [r for r in load().get('history', []) if r['date'] < date]
    if not rows: return 1.0, None
    r = max(rows, key=lambda r: r['date'])
    if mode == 'live' and r.get('mode') != 'live': return 1.0, None
    return float(r['x']), r['date']


def current_x(date, cfg=None):
    """x to size with on D: the row for D if it exists, else the last close before D; 1.0 unless mode is 'live'."""
    st = settings(cfg)
    if st['mode'] != 'live': return 1.0
    r = row_for(date)
    return float(r['x']) if r and r.get('mode') == 'live' else float(prev_x(date, 'live')[0])


def realized_vol(weights, closes, date, n):
    """Annualized realized vol of sum_i w_i r_i,t over the last n daily returns to D's close (population sd, as the
    backtest). closes: {SYM: {date: close}}. Names lacking a close on a day are left out of that day (weight at 0)."""
    w = {s: v for s, v in weights.items() if abs(v) > 1e-12 and closes.get(s)}
    if not w: return 0.0, 0
    days = sorted({d for s in w for d in closes[s] if d <= date})
    days = days[-(n + 1):]
    r = []
    for a, b in zip(days[:-1], days[1:]):
        t = 0.0
        for s, v in w.items():
            c = closes[s]
            if a in c and b in c and c[a] > 0: t += v * (c[b] / c[a] - 1)
        r.append(t)
    if len(r) < 2: return 0.0, len(r)
    m = sum(r) / len(r)
    return math.sqrt(max(sum((x - m) ** 2 for x in r) / len(r), 0.0) * 252), len(r)


def decide(date, vol, n_days, cfg=None, write=True, extra=None):
    """Apply the step rule for close D and record it. Returns the row."""
    st = settings(cfg)
    x_raw = min(1.0, st['target'] / vol) if vol > 1e-9 else 1.0
    xp, xp_date = prev_x(date, st['mode'])
    moved = abs(x_raw - xp) >= st['step'] - 1e-12
    x = x_raw if moved else xp
    if x >= 1.0 - 1e-12:
        reason = f"book vol {vol:.1%} over the last {n_days} days is at or below the {st['target']:.0%} target: full sizing"
        if not moved and x_raw < 1: reason = f"book vol {vol:.1%} over the last {n_days} days would set x to {x_raw:.2f}, less than {st['step']:.2f} from {xp:.2f}: no change (full sizing)"
    elif moved:
        reason = f"book vol {vol:.1%} over the last {n_days} days, above the {st['target']:.0%} target: x = {st['target']:.0%} / {vol:.1%} = {x:.2f} (was {xp:.2f})"
    else:
        reason = f"book vol {vol:.1%} over the last {n_days} days would set x to {x_raw:.2f}, less than {st['step']:.2f} from {xp:.2f}: x stays {x:.2f}"
    row = dict(date=date, vol=round(vol, 4), n_days=n_days, x_raw=round(x_raw, 4), x_prev=round(xp, 4), x_prev_date=xp_date,
               x=round(x, 4), moved=bool(moved and abs(x - xp) > 1e-12), mode=st['mode'], target=st['target'], window=st['window'],
               step=st['step'], reason=reason, **(extra or {}))
    if write:
        s = load(); h = [r for r in s.get('history', []) if r['date'] != date] + [row]
        s['history'] = sorted(h, key=lambda r: r['date'])
        s['note'] = __doc__.split('\n\n')[0]
        tmp = STATE + '.tmp'; json.dump(s, open(tmp, 'w'), indent=1); os.replace(tmp, STATE)
    return row


def set_buyback(date, totals):
    """Record on D's row the share counts a step up restores (Kilian 2026-11-24 card 'Buy back right away')."""
    st = load(); r = next((r for r in st.get('history', []) if r['date'] == date), None)
    if r is None: return
    r['buyback'] = {k: int(v) for k, v in totals.items()}
    tmp = STATE + '.tmp'; json.dump(st, open(tmp, 'w'), indent=1); os.replace(tmp, STATE)


def buyback_owed(date):
    """Buy-back share counts still in force on D: the last step's row (on or before D) if it was a step up. A later step
    (either way) replaces it. Returns ({SYM: total_after}, step date)."""
    rows = [r for r in load().get('history', []) if r['date'] <= date and r.get('moved') and r.get('mode') == 'live']
    if not rows: return {}, None
    r = max(rows, key=lambda r: r['date'])
    return (dict(r.get('buyback') or {}), r['date']) if r['x'] > r['x_prev'] else ({}, None)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'vol':
        sys.path.insert(0, HERE); import allocate as A
        D = sys.argv[2]; w = {a.split('=')[0]: float(a.split('=')[1]) for a in sys.argv[3:]}
        print(realized_vol(w, {s: A.closes(s) for s in w}, D, settings()['window']))
    else:
        for r in load().get('history', [])[-10:]: print(json.dumps(r))
