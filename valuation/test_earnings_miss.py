"""Synthetic checks for earnings_miss.py (no network, nothing written to data/). Run: python3 test_earnings_miss.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import earnings_miss as em
em.MODE, em.RULE = "block", "price"  # these tests cover the block mode (kept as an option since 2026-10-19 flag mode)

em.month_end_close = lambda sym, d: (d[:8] + '01', 100.0)  # flat $100 share price: s = (EPS - consensus) / 100


def hist(rows):
    return {d: dict(fiscal_quarter=q, date_reported=d, eps=e, consensus=c, source='synthetic') for q, d, e, c in rows}


H = hist([('Q1 25', '2025-04-20', 1.00, 0.95), ('Q2 25', '2025-07-20', 1.02, 1.00), ('Q3 25', '2025-10-20', 1.05, 1.03),
          ('Q4 25', '2026-01-20', 1.10, 1.06), ('Q1 26', '2026-04-20', 1.12, 1.10),
          ('Q2 26', '2026-07-20', 0.90, 1.00),      # -$0.10 on $100 = -0.10% of price: a big miss
          ('Q3 26', '2026-10-20', 1.20, 1.10)])     # a later beat: reported after D below, so ignored there
ok = 0
def check(cond, msg):
    global ok
    assert cond, msg; ok += 1; print('ok ', msg)

r = em.evaluate('XYZ', H, '2026-10-16')
check(r['big_miss'] and r['blocked'] and r['block_from'] == '2026-07-20' and r['block_until'] == '2027-01-20', 'big miss on 07-20 blocks adds until 2027-01-20')
check(r['last_report_date'] == '2026-07-20', 'a report dated after D is ignored')
check(r['usual_range']['n_prev'] == 5 and r['usual_range']['usual'] == 0.0002 and r['usual_range']['low'] == 0.0002 and r['usual_range']['high'] == 0.0005, 'usual = median, range = [min, max] of s over the previous reports (5 on file, >= 4 needed)')
r2 = em.evaluate('XYZ', H, '2026-10-21')
check(not r2['big_miss'] and r2['blocked'] and r2['block_from'] == '2026-07-20', 'a beat after the miss does not lift the 6-month block')
r3 = em.evaluate('XYZ', H, '2027-01-20')
check(not r3['blocked'] and 'block ended 2027-01-20' in r3['reason'], 'block ends on block_until (adds allowed again that day)')
small = hist([('Q1', '2026-07-20', 0.97, 1.00)])     # -$0.03 = -0.03% of price: a miss, but not big
check(not em.evaluate('S', small, '2026-10-16')['blocked'], 'a small miss (-0.03% of price) does not block')
edge = hist([('Q1', '2026-07-20', 0.95, 1.00)])      # exactly -0.05% of price
check(em.evaluate('E', edge, '2026-10-16')['blocked'], 'a miss of exactly 0.05% of price blocks')
check(em.evaluate('XYZ', H, '2026-10-16', rule='usual')['blocked'], "rule='usual': the miss is below the low of its usual range, so it blocks too")
check(not em.evaluate('N', {}, '2026-10-16')['blocked'], 'no data: no block')
# blocks(D) reads the file and applies the window at D; it never raises
tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump({'_meta': {}, 'XYZ': r, 'S': em.evaluate('S', small, '2026-10-16')}, tmp); tmp.close()
em.OUT = tmp.name
check(set(em.blocks('2026-10-16')) == {'XYZ'}, 'blocks(D) lists only the blocked name')
check(em.blocks('2027-02-01') == {}, 'blocks(D) is empty once the window has passed')
em.OUT = tmp.name + '.missing'
check(em.blocks('2026-10-16') == {}, 'a missing file means no blocks (the nightly does not break)')
os.unlink(tmp.name)
print(f'{ok} checks passed')
