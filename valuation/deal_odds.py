"""Deal odds and deal values for special situations (playbook https://claude.ai/artifact/Q5a8R1N1oMAHWpWa3X8gk9;
Kilian picked all three design cards 2026-11-16 00:32 ET, each the recommended option).

One formula for every deal type T1-T5 (model_engine.deal_mix builds the 12-month mixture):
    research target (12m) = p x deal value + (1 - p) x standalone research value
    p_mkt = (S - B) / (D_mkt - B)
  S       the live price the odds are read at (spot_at_entry)
  D_mkt   the deal value at market: cash x df_now + ratio x acquirer spot (df_now discounts the cash from the expected close
          to entry_date at discount_rate; no close date, no discount)
  B       the break price the odds rule uses (Kilian card 'odds rule'): U x 1.15 for cash, U for stock (Malmendier, Opp &
          Saidi, NBER w18211: failed cash targets stay ~15% above the pre-bid price, failed stock targets go back to it).
          Mixed deals: U x (1 + 0.15 x cash share of D_mkt) -- judgment, linear in the cash share, no study.
  U       undisturbed: the pre-news close moved by peers
Deal value in the target (research, not market): cash x df_h + ratio x the acquirer's research target (12m) (Kilian card
'stock-deal targets'), where df_h discounts the cash from the expected close back to the 12-month horizon at discount_rate
when the close falls after it (playbook rule 5: time and probability only). The stock part is not discounted: at the
horizon the holder owns the claim on ratio acquirer shares. A stock leg's width is ratio x the acquirer's distribution.
The base rate (wrds/results/rumor_hit_rate/base_rates.json, Kilian 'SDC scaled to news' 2026-11-16) is the >5% card
check on p, as for every judgment input (Kilian 2026-11-09).

CLI
  python3 deal_odds.py event SPEC.json          -> a deal_events entry (p_mkt, B, D, provisional flag) + card context
  python3 deal_odds.py options SYM CHAIN.json B D [--expiry YYYYMMDD] [--as-of YYYY-MM-DD]  -> break mass below B, upside mass above D
  python3 deal_odds.py base TYPE [--size '$1-5B'] [--rumor 'bidder plans rumored'] [--horizon 1y]
SPEC.json keys: name, type (T1-T5), consideration (cash|stock|mixed), price_per_share (cash) or cash_per_share + ratio +
acquirer (+ acquirer_target, acquirer_sd_down, acquirer_sd_up, acquirer_spot, acquirer_basis), undisturbed,
spot_at_entry, entry_date, announced, expected_close, discount_rate, standalone (our standalone research target, for the
base-rate check), size_bucket, rumor_type, provisional, sources, price_basis."""
import datetime as dt, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BASE_RATES = os.environ.get('DEAL_BASE_RATES') or os.path.join(os.path.dirname(HERE), 'wrds', 'results', 'rumor_hit_rate', 'base_rates.json')
CASH_BREAK_UPLIFT = 0.15  # Malmendier, Opp & Saidi (JFE 2016): failed cash bids leave the target ~15% above the pre-bid price
TYPES = {'T1': 'rumor / talks', 'T2': 'confirmed approach', 'T3': 'signed, cash', 'T4': 'signed, stock or mixed', 'T5': 'topping bid'}
CONSID = ('cash', 'stock', 'mixed')
P_TOL = 0.005  # a 'market' probability must sit within this of p_mkt from the event's own fields
CARD_PCT = 5.0  # judgment inputs that move the target more than 5% vs an evidence-based alternative go to Kilian (2026-11-09)
SIGNED_FAIL = dict(calm=0.09, turmoil=0.12, source='How Deals Die (Harvard Law forum 2024-10-15): 5,058 US public targets 1996-2020')


def _date(x):
    if x is None or isinstance(x, dt.date): return x
    return dt.date.fromisoformat(str(x)[:10])


def years(a, b):
    """Year fraction from date a to date b (days / 365.25), floored at 0."""
    a, b = _date(a), _date(b)
    return max((b - a).days / 365.25, 0.0) if a and b else 0.0


def legs(e):
    """(cash per share, ratio): cash deals put the whole price in cash."""
    c = e.get('consideration') or 'cash'
    if c == 'cash': return float(e['price_per_share']), 0.0
    return float(e.get('cash_per_share') or 0.0), float(e['ratio'])


def df(e, frm):
    """Discount factor on the cash leg from the expected close back to date frm, at discount_rate (1 when no close date,
    or the close is on or before frm)."""
    t = years(frm, e.get('expected_close'))
    if t <= 0: return 1.0
    r = e.get('discount_rate')
    if r is None: raise ValueError(f"deal_events[{e.get('name')}]: expected_close {e.get('expected_close')} is after {frm}, so discount_rate is needed")
    return (1 + float(r)) ** -t


def deal_value_mkt(e):
    c, r = legs(e)
    if r and e.get('acquirer_spot') is None: raise ValueError(f"deal_events[{e.get('name')}]: stock leg needs acquirer_spot for the market-implied odds")
    return c * df(e, e.get('entry_date')) + r * float(e.get('acquirer_spot') or 0.0)


def deal_value(e, horizon):
    """Deal value in the research target: cash x df to the horizon + ratio x acquirer research target (12m)."""
    c, r = legs(e)
    if r and e.get('acquirer_target') is None: raise ValueError(f"deal_events[{e.get('name')}]: stock leg needs acquirer_target (its research target (12m), else its market-implied 12m median)")
    return c * df(e, horizon) + r * float(e.get('acquirer_target') or 0.0)


def cash_share(e):
    c, r = legs(e); Dm = deal_value_mkt(e)
    return c * df(e, e.get('entry_date')) / Dm if Dm > 0 else 1.0


def break_price(e):
    """B for the odds: U x (1 + 0.15 x cash share)."""
    return float(e['undisturbed']) * (1 + CASH_BREAK_UPLIFT * cash_share(e))


def p_market(S, B, Dm):
    if Dm == B: return None
    return (S - B) / (Dm - B)


def p_mkt_of(e):
    return p_market(float(e['spot_at_entry']), float(e['break_price']), deal_value_mkt(e))


def target(p, D, standalone): return p * D + (1 - p) * standalone


def base_rates():
    try: return json.load(open(BASE_RATES))
    except Exception: return None


def base_band(typ, size=None, rumor=None, horizon='1y'):
    """Base-rate p for the >5% card check: T1 SDC scaled to news (by size or rumor type when given), T2 disclosed approach,
    T3/T4 signed deals 1 - failure rate (calm market), T5 none on file."""
    br = base_rates(); out = []
    if typ == 'T1' and br:
        out.append(dict(p=br['p_signed'][horizon], label=f'T1 all rumors, signed within {horizon} (SDC 2012-25 scaled to news)'))
        if size and size in (br.get('by_size_1y_scaled') or {}) and horizon == '1y': out.append(dict(p=br['by_size_1y_scaled'][size], label=f'T1 size {size}, 1y'))
        if rumor and rumor in (br.get('by_rumor_type_scaled') or {}) and horizon in br['by_rumor_type_scaled'][rumor]:
            out.append(dict(p=br['by_rumor_type_scaled'][rumor][horizon], label=f'T1 {rumor}, {horizon}'))
    elif typ == 'T2' and br:
        t2 = br['t2_disclosed_approach_or_sale_review_sdc']; out.append(dict(p=t2[horizon], label=f"T2 disclosed approach / sale review, signed within {horizon} (SDC, n={t2['n']}, unscaled)"))
    elif typ in ('T3', 'T4'):
        out += [dict(p=1 - SIGNED_FAIL['calm'], label='signed deal completes, calm markets'), dict(p=1 - SIGNED_FAIL['turmoil'], label='signed deal completes, turmoil')]
    return out


def check(e, standalone, horizon):
    """Target at p_mkt vs each base-rate p; flags any move over CARD_PCT (those go to Kilian as a card)."""
    D = deal_value(e, horizon); pm = p_mkt_of(e); t0 = target(e['probability'], D, standalone); rows = []
    for b in base_band(e.get('type'), e.get('size_bucket'), e.get('rumor_type')):
        t = target(b['p'], D, standalone); pct = (t / t0 - 1) * 100
        rows.append(dict(b, target=round(t, 2), move_pct=round(pct, 2), card=abs(pct) > CARD_PCT))
    return dict(p=e['probability'], p_mkt=None if pm is None else round(pm, 4), deal_value=round(D, 4), target=round(t0, 2), base_rates=rows,
                card_needed=any(r['card'] for r in rows),
                statement=None if any(r['card'] for r in rows) else f"none of the base-rate alternatives moves the target more than {CARD_PCT:.0f}%")


def build_event(spec, horizon=None):
    """A deal_events entry from SPEC (see the module doc). p defaults to p_mkt (probability_source 'market')."""
    e = {k: v for k, v in spec.items() if k not in ('standalone',)}
    if e.get('type') not in TYPES: raise ValueError(f"type must be one of {sorted(TYPES)} (T6 = delete the event; T7 goes into the buyer's drivers; T8 is a sum of parts)")
    if (e.get('consideration') or 'cash') not in CONSID: raise ValueError(f'consideration must be one of {CONSID}')
    e.setdefault('consideration', 'cash'); e.setdefault('entry_date', dt.date.today().isoformat())
    if e.get('expected_close') and e.get('discount_rate') is None:
        import allocate; e['discount_rate'] = allocate.rf()
    e['break_price'] = round(break_price(e), 4)
    if e['consideration'] != 'cash': e['price_per_share_mkt'] = round(deal_value_mkt(e), 4)
    pm = p_mkt_of(e); flags = []
    if pm is None or not 0 < pm < 1:
        flags.append(f'p_mkt {pm} is outside (0, 1): price {e["spot_at_entry"]} is not between B {e["break_price"]:.2f} and D {deal_value_mkt(e):.2f}; the market expects a higher bid or prices a break; card the p')
    if e.get('probability') is None:
        if flags: raise ValueError('; '.join(flags))
        e['probability'] = round(pm, 4); e['probability_source'] = 'market'
    e.setdefault('probability_source', 'judgment')
    c, r = legs(e)
    e.setdefault('probability_basis', (f"Market-implied: (S - B) / (D_mkt - B) = ({e['spot_at_entry']} - {e['break_price']:.2f}) / ({deal_value_mkt(e):.2f} - {e['break_price']:.2f}) = {pm:.4f}; "
                                      f"B = U {e['undisturbed']} x (1 + 0.15 x cash share {cash_share(e):.2f}) (odds rule, Kilian 2026-11-16)"))
    if horizon is None: horizon = (_date(e['entry_date']) + dt.timedelta(days=365)).isoformat()
    out = dict(event=e, flags=flags)
    if spec.get('standalone') is not None: out['check'] = check(e, float(spec['standalone']), horizon)
    return out


def _mid(o):
    b, a = o.get('bid') or 0, o.get('ask') or 0
    return (a + b) / 2 if a > 0 and a >= b else None


def option_masses(chain, B, D, expiry=None, min_days=2, as_of=None):
    """Risk-neutral P(S_T < B) and P(S_T > D) at the nearest expiry (>= min_days out) from put / call spreads on the strikes
    either side of each level (Breeden-Litzenberger digitals). chain = Cboe delayed_quotes 'data' (options list)."""
    import re
    rows = {}; root0 = (chain.get('symbol') or '').lstrip('_')
    for o in chain.get('options') or []:
        mt = re.match(r'^(.*?)(\d{6})([CP])(\d{8})$', o['option'])
        if not mt or (root0 and mt.group(1) != root0): continue  # skip adjusted roots (e.g. MOD1 after the Gentherm spin)
        ex, cp, K = mt.group(2), mt.group(3), int(mt.group(4)) / 1000
        m = _mid(o)
        if m is not None: rows.setdefault('20' + ex, {}).setdefault(cp, {})[K] = m
    as_of = _date(as_of) or dt.date.today()
    exps = sorted(x for x in rows if (dt.date(int(x[:4]), int(x[4:6]), int(x[6:])) - as_of).days >= min_days)
    if expiry: exps = [x for x in exps if x == expiry]
    if not exps: return None
    ex = exps[0]; T = (dt.date(int(ex[:4]), int(ex[4:6]), int(ex[6:])) - as_of).days

    def digital(side, level):
        q = rows[ex].get(side) or {}; ks = sorted(q)
        lo = [k for k in ks if k <= level]; hi = [k for k in ks if k >= level]
        if not lo or not hi: return None
        k1, k2 = lo[-1], hi[0]
        if k1 == k2:
            i = ks.index(k1)
            if i == 0 or i == len(ks) - 1: return None
            k1, k2 = ks[i - 1], ks[i + 1]
        v = (q[k2] - q[k1]) / (k2 - k1)
        return dict(p=round(max(0.0, min(1.0, -v if side == 'C' else v)), 4), strikes=[k1, k2])
    return dict(expiry=ex, days=T, below_B=digital('P', B), above_D=digital('C', D), B=B, D=D,
                note='risk-neutral, from mid prices on the strikes either side; near-term break chance below B and higher-price chance above D')



# ---- trade rules (review.py, allocate.py; playbook 'Trade rules', Kilian cards 2026-11-16) ----
def deal_of(res_s):
    """The research round's deal dict (model_engine.json sets.supervisor.deal) behind a result.json, or None."""
    f = ((res_s or {}).get('research_leg') or {}).get('file')
    if not f: return None
    try: return ((json.load(open(os.path.join(HERE, f))).get('sets') or {}).get('supervisor') or {}).get('deal')
    except Exception: return None


def _sa(deal): return ((deal.get('standalone') or {}).get('median'))


def cap_exit(deal, P, rf, dps=0.0):
    """Cap case (playbook fast lane rule 2): our standalone is above the deal value, so a deal caps the upside, and the
    deal-weighted research target (12m) plus a year of dividends returns less than the T-bill rate from P: exit, due in
    the session (exits are never held). Returns the reason or None."""
    if not deal: return None
    sa, D, T = _sa(deal), deal.get('price_per_share'), deal.get('target')
    if sa is None or D is None or T is None or not sa > D: return None
    ret = (T + dps) / P - 1
    if ret >= rf: return None
    return (f"deal cap case: standalone {sa:.2f} is above the deal value {D:.2f}, so the deal caps the upside, and the deal-weighted "
            f"research target (12m) {T:.2f} returns {ret:+.1%} from {P:.2f} (with dividends), below the {rf:.1%} T-bill rate: exit (fast lane)")


def p_live(deal, P, acq_spot=None, today=None):
    """Market-implied p at price P on date today (acquirer at acq_spot, else the event's acquirer_spot), from the event as
    booked (deal['event']). None without the odds fields."""
    e = dict(deal.get('event') or {})
    if e.get('break_price') is None or e.get('spot_at_entry') is None: return None
    e['spot_at_entry'] = P
    if today: e['entry_date'] = str(today)[:10]
    if acq_spot is not None: e['acquirer_spot'] = acq_spot
    try: return p_mkt_of(e)
    except (ValueError, KeyError, TypeError): return None


def spread_test(deal, P, page_target, w_llm, bar, dps=0.0, acq_spot=None, today=None):
    """No spread-only buys (playbook slow lane rule 2): a deal name is bought or added only if it clears the usual Entry below
    with the research leg at the market-implied p, i.e. the edge is our standalone view, not a view on the deal odds.
    The page target at p_mkt = page target + w_llm x (research target at p_mkt - research target at the booked p) (linear in
    the research leg; the page blends the legs' distributions, so this is a close approximation). Returns dict with
    block (reason or None)."""
    if not deal: return dict(block=None)
    if deal.get('provisional'): return dict(block='provisional (fast-lane) deal entry: exits only until the formal round is gate-clean')
    sa, D, T = _sa(deal), deal.get('price_per_share'), deal.get('target')
    pl = p_live(deal, P, acq_spot, today)
    if pl is None: return dict(block=None if deal.get('type') is None else 'no market-implied odds for this deal entry')
    B = deal['break_price']
    if pl <= 0: return dict(p_mkt=round(pl, 4), block=f'price {P:.2f} is at or below the break price {B:.2f}, so the market prices a break; adds wait for a driver update (T6 rule)')
    if pl >= 1: return dict(p_mkt=round(pl, 4), block=f'price {P:.2f} is at or above the deal value at market, so the market prices a higher bid (market p {pl:.2f}); no buys on a hoped-for topping bid')
    if sa is not None and sa <= B: return dict(p_mkt=round(pl, 4), block=f'standalone {sa:.2f} is at or below the break price {B:.2f}, so any edge is the deal spread alone')
    rt_m = target(pl, D, sa); tm = page_target + w_llm * (rt_m - T); ret = tm / P - 1 + dps / P
    out = dict(p_mkt=round(pl, 4), research_target_mkt=round(rt_m, 2), page_target_mkt=round(tm, 2), target_return_mkt=round(ret, 4))
    if bar is not None and ret <= bar:
        out['block'] = (f'at the market-implied p {pl:.2f} the target would be {tm:.2f}, a {ret:.1%} return with dividends, not above its '
                        f'beta-adjusted {bar:.1%}: the edge is the deal odds, not our research (no spread-only buys)')
    else: out['block'] = None
    return out

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'event':
        sp = json.load(open(a[1])); o = build_event(sp); print(json.dumps(o, indent=1))
        if os.path.exists(os.path.join(HERE, 'deal_filings.py')) and sp.get('symbol'):  # the nightly EDGAR scan watches the target and acquirer
            try:
                sys.path.insert(0, HERE); import deal_filings
                deal_filings.watch_add(sp['symbol'], acquirer=sp.get('acquirer'), why=f"{sp.get('type')} {sp.get('name', '')}")
            except Exception as x: print('watch list not updated:', x)
    elif a[0] == 'options':
        ch = json.load(open(a[2])); ch = ch.get('data', ch)
        ex = a[a.index('--expiry') + 1] if '--expiry' in a else None
        ao = a[a.index('--as-of') + 1] if '--as-of' in a else None
        print(json.dumps(option_masses(ch, float(a[3]), float(a[4]), ex, as_of=ao), indent=1))
    elif a[0] == 'base':
        kw = {k: a[a.index('--' + k) + 1] for k in ('size', 'rumor', 'horizon') if '--' + k in a}
        print(json.dumps(base_band(a[1], **kw), indent=1))
    else: print(__doc__); sys.exit(1)
