"""Append fills entered on the Fill Log page (https://claude.ai/artifact/59bT8J6KpmduVYwcQdhWA6) to data/trades.json.
(Kilian 2026-10-19: adopted the fill-entry page so fills no longer go through chat.)

The routine reads the page's `fills` collection with ArtifactData (query status == "new", out_dir=DIR), then runs
    python3 fill_intake.py DIR/fills/*.json
which appends each new fill once (keyed by its page doc id, kept as fill_id) and prints the doc ids it logged, so the
routine can mark those rows status "logged" on the page. A row already in trades.json is skipped, so a rerun is safe.
Rows that fail validation are printed under "rejected" and left as "new" on the page for a look."""
import json, os, re, sys, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); TRADES = os.path.join(HERE, 'data', 'trades.json')

def load(path):
    d = json.load(open(path))
    doc = d.get('data', d)
    return d.get('id') or os.path.splitext(os.path.basename(path))[0], doc

def check(doc):
    if not re.fullmatch(r'[A-Z.]{1,6}', str(doc.get('symbol', ''))): return 'bad ticker'
    if doc.get('side') not in ('buy', 'sell'): return 'bad side'
    if not (isinstance(doc.get('shares'), (int, float)) and doc['shares'] > 0 and float(doc['shares']).is_integer()): return 'bad shares'
    if not (isinstance(doc.get('price'), (int, float)) and doc['price'] > 0): return 'bad price'
    try: d = dt.date.fromisoformat(doc.get('date', ''))
    except ValueError: return 'bad date'
    if d > dt.date.today(): return 'future date'
    return None

if __name__ == '__main__':
    book = json.load(open(TRADES))
    have = {t.get('fill_id') for t in book['trades']}
    logged, rejected, skipped = [], [], []
    for p in sys.argv[1:]:
        fid, doc = load(p)
        if doc.get('status') != 'new': continue
        if fid in have: skipped.append(fid); continue
        why = check(doc)
        if why: rejected.append({'id': fid, 'why': why}); continue
        book['trades'].append({'date': doc['date'], 'symbol': doc['symbol'], 'side': doc['side'], 'shares': int(doc['shares']),
                               'price': float(doc['price']), 'fees': 0.0, 'fill_id': fid,
                               'price_source': f"Fill Log page entry {doc.get('entered_at', '')}" + (f" ({doc['note']})" if doc.get('note') else '')})
        logged.append(fid); have.add(fid)
    if logged:
        tmp = TRADES + '.tmp'; json.dump(book, open(tmp, 'w'), indent=1); os.replace(tmp, TRADES)
    print(json.dumps({'logged': logged, 'already_in_trades': skipped, 'rejected': rejected}))
