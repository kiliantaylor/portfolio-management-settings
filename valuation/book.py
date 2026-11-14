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
    """Positions from the trade log. cost_basis is TOTAL per position (as in holdings.json); lots holds each buy.
    Wash sales (Kilian 2026-11-11 'yes', thread 'Fix washed losses in cost basis'; IRC 1091, matched as wash_sales() does):
    a loss share sold on day d is washed by a share of the same name bought within 30 days before d (still held, not sold in
    that sale) or within 30 days after, oldest loss first, each replacement share once. The washed loss leaves realized (so
    it leaves tax_set_aside), is added to that replacement share's basis, and the sold share's holding period carries over
    (lot hold_from), so the replacement's later sale books the deferred loss with the right term. Washed replacement shares
    are split into their own lot (free=0) ahead of the same buy's other shares, so FIFO sells them first, as wash_sales does."""
    T = json.load(open(os.path.join(HERE, 'data', 'trades.json')))['trades']
    pos, cash, realized, pend = {}, float(b['starting_cash']), [], {}  # pend: sym -> loss rows still open to a wash

    def wash(p, i, w, k, d_buy):  # move k loss shares of row w onto lot i's free shares (split off ahead of the rest)
        l = p['lots'][i]; lps = w['_lps']; k = min(k, l['free'], w['_open'])
        if k <= 0: return 0
        held = (dt.date.fromisoformat(w['date']) - dt.date.fromisoformat(w['_hold_from'])).days
        part = dict(l, shares=k, free=0, price=l['price'] + lps,
                    hold_from=(dt.date.fromisoformat(d_buy) - dt.timedelta(days=held)).isoformat(), wash_from=w['date'])
        l['shares'] -= k; l['free'] -= k
        if l['shares']: p['lots'].insert(i, part)
        else: p['lots'][i] = part
        p['cost_basis'] += k * lps; w['_open'] -= k; w['gain'] = round(w['gain'] + k * lps, 2)
        w['washed'] = round(w.get('washed', 0) + k * lps, 2); w.setdefault('washed_by', []).append(dict(date=l['date'], shares=k))
        return k

    for t in sorted(T, key=lambda x: x['date']):
        n = t['shares'] if t['side'] == 'buy' else -t['shares']; s = t['symbol']; p = pos.setdefault(s, dict(symbol=s, shares=0, cost_basis=0.0, lots=[]))
        if t.get('funding') != 'external':  # lots bought outside the project money (Kilian 2026-10-07) don't come out of cash
            cash -= n * t['price'] + t.get('fees', 0)
        if n > 0:
            p['cost_basis'] += n * t['price'] + t.get('fees', 0)  # the buy fee is part of each share's basis
            p['lots'].append(dict(date=t['date'], shares=n, price=t['price'] + t.get('fees', 0) / n, free=n, hold_from=t['date']))
            for w in pend.get(s, []):  # a buy after a loss sale washes its open shares, oldest sale first
                if w['_open'] and t['date'] <= w['_window_end']:
                    wash(p, len(p['lots']) - 1, w, w['_open'], t['date'])
        else:  # sells come off the oldest lots first (FIFO); each lot's gain is short- or long-term by its own (carried) buy date
            left, rows, d = -n, [], dt.date.fromisoformat(t['date'])
            while left and p['lots']:
                l0 = p['lots'][0]; k = min(left, l0['shares'])
                held_days = (d - dt.date.fromisoformat(l0['hold_from'])).days
                g = k * (t['price'] - l0['price']) - t.get('fees', 0) * k / -n
                r = dict(date=t['date'], symbol=s, shares=k, gain=round(g, 2), long_term=held_days > 365)
                realized.append(r)
                if g < 0: r.update(gain_before_wash=r['gain'], _lps=-g / k, _open=k, _hold_from=l0['hold_from'],
                                   _window_end=(d + dt.timedelta(days=WASH_DAYS)).isoformat()); rows.append(r)
                p['cost_basis'] -= k * l0['price']; l0['shares'] -= k; l0['free'] = max(min(l0['free'], l0['shares']), 0); left -= k
                if not l0['shares']: p['lots'].pop(0)
            for w in rows:  # shares bought in the 30 days before the sale and still held wash it at once
                i = 0
                while w['_open'] and i < len(p['lots']):
                    l = p['lots'][i]
                    if l['free'] and (d - dt.date.fromisoformat(l['date'])).days <= WASH_DAYS:
                        if wash(p, i, w, w['_open'], l['date']) and l['shares']: i += 1  # split: the washed part now sits at i, l at i + 1
                    i += 1
            pend.setdefault(s, []).extend(rows)
        p['shares'] += n
    for r in realized:
        if '_open' in r: r.update(wash_open_shares=r['_open'], wash_open_loss=round(r['_open'] * r['_lps'], 2), wash_window_end=r['_window_end'])
        for k in [k for k in r if k.startswith('_')]: r.pop(k)
    out = []
    for p in pos.values():
        if p['shares'] <= 0: continue
        d, px = last_close(p['symbol'])
        out.append(dict(p, cost_basis=round(p['cost_basis'], 2), price=px, price_date=d, market_value=round(px * p['shares'], 2) if px else None,
                        first_bought=min(l['date'] for l in p['lots']), hold_from=min(l['hold_from'] for l in p['lots'])))
    mv = sum(p['market_value'] or 0 for p in out)
    # Kilian 2026-10-07 01:23Z/01:31Z: size against the book (Kilian 2026-10-08: $24,000); book.json external_cash is the outside money, and buys stop there
    ext = float(b.get('external_cash', 0)); ta = tax_set_aside(realized)  # Kilian 2026-10-20 card 'Carve it out': this year's tax is not investable
    return dict(positions=out, cash_in_account=round(cash, 2), cash_to_deploy_external=round(ext - ta, 2), external_cash_gross=ext, tax_set_aside=ta, mode='live', starting_cash=float(b['starting_cash']),
                totals=dict(equities_market_value=round(mv, 2), equities_cost_basis=round(sum(p['cost_basis'] for p in out), 2),
                            account_total=round(mv + cash, 2)), note=b.get('_note'), realized=realized,
                washed_loss_deferred=round(sum(r.get('washed', 0) for r in realized), 2))


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


WASH_DAYS = 30


def wash_sales(as_of=None):
    """Kilian 2026-11-11 'yes' (wash-sale warning on adds): IRC 1091 matching of loss sales to replacement shares from
    data/trades.json. A loss share sold on day d is washed by a share of the same name bought within 30 days before d (still
    held, not sold in that sale) or within 30 days after; each replacement share washes one loss share, oldest first. The
    washed loss is disallowed now and moves into the replacement share's basis. Returns one row per loss sale with the
    shares still open to a wash (open_shares, open_loss) and the last day a buy would wash them (window_end). Drives the
    warning; live() applies the same matching to realized, tax_set_aside and the replacement lots' basis (since 2026-11-11)."""
    T = json.load(open(os.path.join(HERE, 'data', 'trades.json')))['trades']
    lots, out = {}, []  # lots: sym -> [dict(date, shares, price, free)]; free = shares not yet used as a replacement
    for t in sorted(T, key=lambda x: x['date']):
        s, d = t['symbol'], dt.date.fromisoformat(t['date']); L = lots.setdefault(s, [])
        if t['side'] == 'buy':
            n = t['shares']; lot = dict(date=t['date'], shares=n, price=t['price'] + t.get('fees', 0) / n, free=n); L.append(lot)
            for w in out:  # a buy after a loss sale washes its open shares first, oldest sale first
                if w['symbol'] == s and w['open_shares'] and t['date'] <= w['window_end'] and lot['free']:
                    k = min(w['open_shares'], lot['free']); lot['free'] -= k; w['open_shares'] -= k
                    w['washed'].append(dict(date=t['date'], shares=k, loss=round(k * w['loss_per_share'], 2)))
            continue
        left, loss_sh, loss = t['shares'], 0, 0.0
        while left and L:  # FIFO, as live() does
            l0 = L[0]; k = min(left, l0['shares']); g = k * (t['price'] - l0['price']) - t.get('fees', 0) * k / t['shares']
            if g < 0: loss_sh += k; loss += -g
            l0['free'] = max(min(l0['free'], l0['shares'] - k), 0); l0['shares'] -= k; left -= k
            if not l0['shares']: L.pop(0)
        if not loss_sh: continue
        w = dict(symbol=s, sale_date=t['date'], shares=loss_sh, loss=round(loss, 2), loss_per_share=loss / loss_sh,
                 window_end=(d + dt.timedelta(days=WASH_DAYS)).isoformat(), washed=[])
        w['open_shares'] = loss_sh
        for l in L:  # shares bought in the 30 days before the sale and still held wash it at once
            if w['open_shares'] and l['free'] and (d - dt.date.fromisoformat(l['date'])).days <= WASH_DAYS:
                k = min(w['open_shares'], l['free']); l['free'] -= k; w['open_shares'] -= k
                w['washed'].append(dict(date=l['date'], shares=k, loss=round(k * w['loss_per_share'], 2), before_sale=True))
        out.append(w)
    for w in out:
        w['open_loss'] = round(w['open_shares'] * w['loss_per_share'], 2); w['loss_per_share'] = round(w['loss_per_share'], 4)
        w['open'] = bool(w['open_shares']) and (as_of is None or as_of <= w['window_end'])
    return out


def wash_open(as_of):
    """{sym: dict(shares, loss, window_end, sales)} for loss shares a buy on as_of would still wash. From live()'s realized
    rows (adjusted basis, so a loss on a replacement share that carries an earlier washed loss is seen too); the
    wash_sales() matching on raw prices is the fallback outside live mode."""
    b = json.load(open(os.path.join(HERE, 'data', 'book.json')))
    rows = [r for r in live(b)['realized'] if r.get('wash_open_shares')] if b.get('mode') == 'live' else None
    if rows is None:
        rows = [dict(date=w['sale_date'], symbol=w['symbol'], wash_open_shares=w['open_shares'], wash_open_loss=w['open_loss'],
                     wash_window_end=w['window_end']) for w in wash_sales(as_of) if w['open_shares']]
    o = {}
    for r in rows:
        if as_of and as_of > r['wash_window_end']: continue
        x = o.setdefault(r['symbol'], dict(shares=0, loss=0.0, window_end=r['wash_window_end'], sales=[]))
        x['shares'] += r['wash_open_shares']; x['loss'] = round(x['loss'] + r['wash_open_loss'], 2)
        x['window_end'] = max(x['window_end'], r['wash_window_end'])
        if x['sales'] and x['sales'][-1]['date'] == r['date']:
            x['sales'][-1]['shares'] += r['wash_open_shares']; x['sales'][-1]['loss'] = round(x['sales'][-1]['loss'] + r['wash_open_loss'], 2)
        else: x['sales'].append(dict(date=r['date'], shares=r['wash_open_shares'], loss=r['wash_open_loss']))
    return o
