"""Mirror data/trades.json onto the Fill Log page (https://claude.ai/artifact/59bT8J6KpmduVYwcQdhWA6).
(Kilian 2026-10-26: 'fill log is outdated'. Fills he reported in chat or by screenshot went straight into trades.json
and never reached the page, so its blotter and held-name chips fell behind.)

Hourly step 1b: list the page's `fills` collection with ArtifactData (action list, out_dir=DIR), then
    python3 fill_mirror.py DIR/fills/*.json > batch.json
It prints {"writes": [...]} for every trades.json row the page does not have yet (status "logged"), ready for an
ArtifactData batch (50 at a time). Rows the page already holds, under their fill_id or a mirror id, are skipped, so a rerun is safe.
Fees are not shown on the page."""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); TRADES = os.path.join(HERE, 'data', 'trades.json')

def mirror_id(t, seen):
    base = t.get('fill_id') or re.sub(r'[^A-Za-z0-9_.~:@+-]', '', f"m_{t['date']}_{t['symbol']}_{t['side']}_{int(t['shares'])}_{t['price']}")
    n = seen.get(base, 0); seen[base] = n + 1
    return base if n == 0 else f"{base}_{n + 1}"

if __name__ == '__main__':
    have = set()
    for p in sys.argv[1:]:
        d = json.load(open(p)); have.add(d.get('id') or os.path.splitext(os.path.basename(p))[0])
    seen, writes = {}, []
    for t in json.load(open(TRADES))['trades']:
        i = mirror_id(t, seen)
        if i in have: continue
        writes.append({'op': 'set', 'collection': 'fills', 'doc_id': i, 'data': {
            'date': t['date'], 'symbol': t['symbol'], 'side': t['side'], 'shares': int(t['shares']), 'price': float(t['price']),
            'status': 'logged', 'source': 'trades.json', 'entered_at': t['date'] + 'T12:00:00.000Z',
            'note': 'before the book started' if t.get('funding') == 'external' else ''}})
    print(json.dumps({'writes': writes}))
