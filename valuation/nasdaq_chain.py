"""Fallback option chain from Nasdaq, used only when the Cboe delayed chain is stale (Kilian 2026-11-24 card: "Nasdaq only
when Cboe is stale"). Writes Cboe-format JSON (data.close, current_price, last_trade_time, options[{option, bid, ask}]) to
chains/nasdaq/D/SYM.json so market_dist.py SYM --chains chains/nasdaq/D reads it unchanged. Note: api.nasdaq.com's
robots.txt disallows automated clients; Kilian chose this fallback knowing that (11-24 thread).
Usage: python3 nasdaq_chain.py D SYM [SYM ...]   (exit 1 if any name failed or its last trade is not from D)"""
import json, sys, os, re, urllib.request, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
def num(x):
    try: return float(str(x).replace(',', ''))
    except Exception: return None
def fetch(D, s):
    u = (f'https://api.nasdaq.com/api/quote/{s}/option-chain?assetclass=stocks&limit=5000&fromdate={D}'
         f'&todate={int(D[:4]) + 3}-12-31&money=all&type=all')
    d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=90))['data']
    # intraday: "$107.75 (AS OF Nov 24, 2026 2:25 PM)"; after the close the time is dropped: "(AS OF NOV 24, 2026)" -> 16:00
    lt = d['lastTrade']; m = re.search(r'\$([\d,.]+) \(AS OF (\w+ \d+, \d{4})(?: (\d+:\d+ [AP]M))?\)', lt, re.I)
    if not m: raise ValueError(f'unreadable lastTrade {lt!r}')
    px = num(m.group(1)); t = dt.datetime.strptime(m.group(2).title() + ' ' + (m.group(3) or '4:00 PM'), '%b %d, %Y %I:%M %p')
    opts, grp = [], None
    for r in d['table']['rows']:
        if r.get('expirygroup'): grp = dt.datetime.strptime(r['expirygroup'], '%B %d, %Y'); continue
        if r.get('strike') is None or grp is None: continue
        K = num(r['strike'])
        # the option root from the contract link (e.g. mod---261218c00050000 vs mod1--...): adjusted contracts (a root
        # with a digit, MOD1 after the Gentherm spin) keep their own root so market_dist.py skips them as it does on Cboe
        mm = re.search(r'/([a-z0-9.]+?)-*\d{6}[cp]\d{8}$', r.get('drillDownURL') or '')
        root = mm.group(1).upper() if mm else s
        for cp, p in (('C', 'c_'), ('P', 'p_')):
            b, a = num(r[p + 'Bid']), num(r[p + 'Ask'])
            if b is None or a is None or a <= 0: continue
            opts.append(dict(option=f"{root}{grp:%y%m%d}{cp}{int(round(K * 1000)):08d}", bid=b, ask=a, last_trade_time=t.isoformat()))
    return dict(data=dict(symbol=s, close=px, current_price=px, last_trade_time=t.isoformat(), options=opts,
                          source='api.nasdaq.com option-chain ' + lt))
if __name__ == '__main__':
    D, syms, bad = sys.argv[1], [x.upper() for x in sys.argv[2:]], 0
    out = os.path.join(HERE, 'chains', 'nasdaq', D); os.makedirs(out, exist_ok=True)
    for s in syms:
        try:
            j = fetch(D, s); dd = j['data']
            if dd['last_trade_time'][:10] != D or not dd['options']: raise ValueError(f"last trade {dd['last_trade_time']}, {len(dd['options'])} quotes")
            json.dump(j, open(f'{out}/{s}.json', 'w')); print(s, dd['close'], dd['last_trade_time'], len(dd['options']))
        except Exception as e: bad = 1; print(f'{s} FAILED {e}'[:300])
    sys.exit(bad)
