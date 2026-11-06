"""AI exposure cap (Kilian 2026-11-05 cards in the thread "Cap AI exposure and set theme limits": definition "Named list
plus a rule", method "50% of stocks, buys only"; spec risk/theme_cap/ai_cap_2026-11-05.md section 4).

The rule: AI names may be at most `cap` (50%) of the money in stocks, at market value: held positions at the day's price
plus the buys planned the same day, the index fund counted in the denominator as money in stocks. It holds back buys
only (new AI names, adds to held AI names, swap-ins of an AI name); it never forces a trim. When the book is over the cap
because AI names rose, nothing is sold; AI buys wait until the share is back under. Gross: no hedge is netted (Kilian
11-05 chose no hedge; the account cannot do option spreads).

Who is AI (data/ai_cap.json, edit there):
  - the named list (AVGO NVDA AMAT MOD VST GOOGL AMZN),
  - the watchlist semis,
  - any other name whose riskcal 'semis_ai' basket loading is >= loading_min (0.3), or whose AI / data-center revenue
    is >= revenue_min (25%) in `ai_dc_revenue` (a sourced share per name, filled by research; empty means no name is in
    by revenue alone yet).

Who reads it: allocate.py (a group constraint in the joint solve, binding 'ai_cap'; whole-share and Sharpe-test steps keep
it), review.py (held AI adds and the next-share table; it writes runs/D/ai_cap.json, the post-trade picture),
swap_test.py (no swap that lifts AI over the cap), rules_check.py ('ai-cap': no due AI buy that leaves AI over the cap),
build_report.py (the page line "AI 39% of stocks (cap 50%)").

    python3 ai_cap.py status [D]      AI share of stocks now, and after D's planned trades if runs/D/ai_cap.json exists
"""
import json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, 'data', 'ai_cap.json')
DEFAULT = dict(cap=0.5, named=['AVGO', 'NVDA', 'AMAT', 'MOD', 'VST', 'GOOGL', 'AMZN'], watchlist_semis=[],
               loading_factor='semis_ai', loading_min=0.3, revenue_min=0.25, ai_dc_revenue={}, basis='gross')


def load(path=None):
    try: c = json.load(open(path or PATH))
    except (OSError, ValueError): c = {}
    for k, v in DEFAULT.items(): c.setdefault(k, json.loads(json.dumps(v)))
    return c


def loading(s, RM, factor='semis_ai'):
    """The name's riskcal basket loading, or None when the model does not cover it."""
    try:
        if RM is None or not RM.covered(s) or factor not in RM.factors: return None
        return float(RM.X[s][RM.factors.index(factor)])
    except Exception: return None


def why_ai(s, RM=None, c=None):
    """The reason s is an AI name, or None."""
    c = c or load()
    if s in c['named']: return 'named list'
    if s in c['watchlist_semis']: return 'watchlist semi'
    L = loading(s, RM, c['loading_factor'])
    if L is not None and L >= c['loading_min']: return f"{c['loading_factor']} loading {L:.2f} >= {c['loading_min']:.2f}"
    r = (c['ai_dc_revenue'].get(s) or {}) if isinstance(c['ai_dc_revenue'].get(s), dict) else {'share': c['ai_dc_revenue'].get(s)}
    if r.get('share') is not None and r['share'] >= c['revenue_min']: return f"AI/data-center revenue {r['share']:.0%} >= {c['revenue_min']:.0%}"
    return None


def members(syms, RM=None, c=None):
    """{sym: reason} for the AI names among syms."""
    c = c or load(); out = {}
    for s in syms:
        w = why_ai(s, RM, c)
        if w: out[s] = w
    return out


def share(dollars, ai):
    """dollars: {sym: market value}. Returns (AI dollars, stock dollars, AI share of stocks or None)."""
    A = sum(v for s, v in dollars.items() if s in ai and v > 0); T = sum(v for v in dollars.values() if v > 0)
    return A, T, (A / T if T > 0 else None)


def headroom(dollars, ai, cap):
    """Dollars of AI buys that still fit: (A + x) / (T + x) <= cap  <=>  x <= (cap T - A) / (1 - cap). Never negative."""
    A, T, _ = share(dollars, ai)
    return max((cap * T - A) / (1 - cap), 0.0) if cap < 1 else math.inf


def fits(dollars, ai, cap, sym, add_dollars, eps=1e-6):
    """True when buying add_dollars of sym keeps the cap (a non-AI buy always fits; it only adds room)."""
    if sym not in ai or add_dollars <= 0: return True
    return add_dollars <= headroom(dollars, ai, cap) + eps


def constraint(names, ai, cap):
    """The cap as one linear constraint a.w <= 0 on the solve's weights: sum_AI w <= cap * sum_all w."""
    return [(1 - cap) if s in ai else -cap for s in names]


def solve_capped(solve, mu, a, tol=1e-7):
    """Joint solve with the AI cap (allocate.py). solve(mu) -> (w, info) is the plain solve (box, budget, industry limit);
    the cap a.w <= 0 enters through its Lagrange multiplier nu >= 0: maximize the objective - nu a.w, i.e. the same solve
    on mu - nu a, with nu found by bisection (a.w*(nu) falls as nu rises; the problem is concave). nu = 0 when the cap does
    not bind. The caller keeps the cap feasible at w = held only (it zeroes new AI buys when the held book is over)."""
    g = lambda w: sum(x * y for x, y in zip(a, w))
    w, info = solve(list(mu))
    if g(w) <= tol: return w, dict(info, ai_cap_nu=0.0)
    lo, hi, best = 0.0, 0.01, None
    for _ in range(40):
        w2, i2 = solve([m - hi * x for m, x in zip(mu, a)])
        if g(w2) <= tol: best = (w2, i2); break
        lo, hi = hi, hi * 2
    if best is None: return w2, dict(i2, ai_cap_nu=hi, ai_cap_feasible=False)
    for _ in range(30):
        mid = (lo + hi) / 2; w3, i3 = solve([m - mid * x for m, x in zip(mu, a)])
        if g(w3) <= tol: hi, best = mid, (w3, i3)
        else: lo = mid
    return best[0], dict(best[1], ai_cap_nu=hi)


def write(D, obj):
    p = os.path.join(HERE, 'runs', D, 'ai_cap.json'); tmp = p + '.tmp'
    json.dump(obj, open(tmp, 'w'), indent=1); os.replace(tmp, p)


def read(D):
    try: return json.load(open(os.path.join(HERE, 'runs', D, 'ai_cap.json')))
    except (OSError, ValueError): return None


def page_line(o):
    """'AI 39% of stocks (cap 50%)' from a runs/D/ai_cap.json object."""
    if not o or o.get('share_after') is None: return None
    t = f"AI {o['share_after']:.0%} of stocks (cap {o['cap']:.0%})"
    if o.get('share_now') is not None and abs(o['share_now'] - o['share_after']) >= 0.005: t += f", {o['share_now']:.0%} before today's trades"
    if o.get('held_back'): t += '; held back: ' + ', '.join(f"{s} {v['shares']:+d} sh" for s, v in sorted(o['held_back'].items()))
    return t


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] != 'status': print(__doc__); sys.exit(1)
    sys.path.insert(0, HERE)
    import book, glob
    D = sys.argv[2] if len(sys.argv) > 2 else sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, 'runs', '2*')))[-1]
    o = read(D)
    if o: print(json.dumps(o, indent=1)); print(page_line(o)); sys.exit(0)
    import riskcal
    pos = {p['symbol']: p['shares'] for p in book.load()['positions'] if p.get('shares')}
    import allocate as A_  # last official close on or before D (data/daily_SYM.csv)
    sp = {s: [v for d, v in A_.closes(s).items() if d <= D][-1] for s in pos}
    RM = riskcal.load(D, sorted(pos)); ai = members(pos, RM); c = load()
    dol = {s: pos[s] * sp[s] for s in pos if sp.get(s)}; A, T, sh = share(dol, ai)
    print(dict(date=D, ai=ai, ai_dollars=round(A, 2), stock_dollars=round(T, 2), share=round(sh, 4) if sh is not None else None, cap=c['cap'],
               headroom=round(headroom(dol, ai, c['cap']), 2)))
