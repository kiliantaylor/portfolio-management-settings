"""The book the sizing works from. data/book.json mode "scratch" (Kilian 2026-09-29): no positions, only the cash to
deploy; mode "live" (Kilian 2026-10-01, first fills): positions built from data/trades.json, cash = starting_cash minus
net purchases (trades with funding "external" add a position without spending cash); mode "holdings": ../portfolio/holdings.json (the old brokerage account). allocate.py, review.py, combine.py and prep_briefs.py read it here."""
import datetime as dt, json, os
HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    b = json.load(open(os.path.join(HERE, 'data', 'book.json')))
    if b.get('mode') == 'scratch':
        return dict(positions=[], cash_in_account=0.0, cash_to_deploy_external=float(b['cash']), mode='scratch',
                    totals=dict(account_total=0.0), note=b.get('_note'))
    if b.get('mode') == 'live':
        return live(b)
    H = json.load(open(os.path.join(HERE, '..', 'portfolio', 'holdings.json'))); H['mode'] = 'holdings'
    return H


def last_close(sym):
    import csv
    for d in (os.path.join(HERE, '..', 'portfolio', 'data'), os.path.join(HERE, 'data')):
        f = os.path.join(d, f'daily_{sym}.csv')
        if os.path.exists(f):
            rows = list(csv.DictReader(open(f)))
            if rows: return rows[-1]['date'], float(rows[-1]['close'])
    return None, None


def live(b):
    """Positions from the trade log. cost_basis is TOTAL per position (as in holdings.json); lots holds each buy."""
    T = json.load(open(os.path.join(HERE, 'data', 'trades.json')))['trades']
    pos, cash, realized = {}, float(b['starting_cash']), []
    for t in sorted(T, key=lambda x: x['date']):
        n = t['shares'] if t['side'] == 'buy' else -t['shares']; p = pos.setdefault(t['symbol'], dict(symbol=t['symbol'], shares=0, cost_basis=0.0, lots=[]))
        if t.get('funding') != 'external':  # lots bought outside the project money (Kilian 2026-10-07) don't come out of cash
            cash -= n * t['price'] + t.get('fees', 0)
        if n > 0:
            p['cost_basis'] += n * t['price'] + t.get('fees', 0); p['lots'].append(dict(date=t['date'], shares=n, price=t['price'] + t.get('fees', 0) / n))  # the buy fee is part of each share's basis
        else:  # sells come off the oldest lots first (FIFO); each lot's gain is short- or long-term by its own buy date
            left = -n
            while left and p['lots']:
                l0 = p['lots'][0]; k = min(left, l0['shares'])
                held_days = (dt.date.fromisoformat(t['date']) - dt.date.fromisoformat(l0['date'])).days
                realized.append(dict(date=t['date'], symbol=t['symbol'], shares=k, gain=round(k * (t['price'] - l0['price']) - t.get('fees', 0) * k / -n, 2),
                                     long_term=held_days > 365))
                p['cost_basis'] -= k * l0['price']; l0['shares'] -= k; left -= k
                if not l0['shares']: p['lots'].pop(0)
        p['shares'] += n
    out = []
    for p in pos.values():
        if p['shares'] <= 0: continue
        d, px = last_close(p['symbol'])
        out.append(dict(p, cost_basis=round(p['cost_basis'], 2), price=px, price_date=d, market_value=round(px * p['shares'], 2) if px else None,
                        first_bought=min(l['date'] for l in p['lots'])))
    mv = sum(p['market_value'] or 0 for p in out)
    # Kilian 2026-10-07 01:23Z/01:31Z: size against the book (Kilian 2026-10-08: $24,000); book.json external_cash is the outside money, and buys stop there
    ext = float(b.get('external_cash', 0)); ta = tax_set_aside(realized)  # Kilian 2026-10-20 card 'Carve it out': this year's tax is not investable
    return dict(positions=out, cash_in_account=round(cash, 2), cash_to_deploy_external=round(ext - ta, 2), external_cash_gross=ext, tax_set_aside=ta, mode='live', starting_cash=float(b['starting_cash']),
                totals=dict(equities_market_value=round(mv, 2), equities_cost_basis=round(sum(p['cost_basis'] for p in out), 2),
                            account_total=round(mv + cash, 2)), note=b.get('_note'), realized=realized)


def tax_set_aside(realized, year=None):
    """Tax owed on this year's realized gains (review_settings tax, flat mode: the kiddie threshold at his own rates, the
    rest at the parent's, as review.py year_tax does; dividends count toward the threshold and are taxed at his own
    short-term rate). Kilian 2026-10-20 card 'Carve it out': the sizing book leaves this out. 0 unless carve_out_tax."""
    cfg = json.load(open(os.path.join(HERE, 'data', 'review_settings.json'))); t = cfg['tax']
    if not cfg.get('carve_out_tax') or t.get('ignore_taxes') or t.get('mode') != 'flat': return 0.0
    yr = year or dt.date.today().isoformat()[:4]
    st = sum(x['gain'] for x in realized if x['date'][:4] == yr and not x['long_term'])
    lt = sum(x['gain'] for x in realized if x['date'][:4] == yr and x['long_term'])
    if st < 0 < lt: lt, st = max(lt + st, 0), min(lt + st, 0)
    elif lt < 0 < st: st, lt = max(st + lt, 0), min(st + lt, 0)
    div = t.get('other_unearned_income', 0); c_st, c_lt = t['flat_child_short_term_rate'], t['flat_child_long_term_rate']
    if st + lt <= 0: return round(div * c_st, 2)
    st, lt = max(st, 0), max(lt, 0); g = st + lt
    room = max(t.get('kiddie_threshold', 2700) - div, 0) if t.get('kiddie_tax', True) else 1e18
    f = min(g, room) / g
    return round(div * c_st + st * (f * c_st + (1 - f) * t['flat_short_term_rate']) + lt * (f * c_lt + (1 - f) * t['flat_long_term_rate']), 2)
