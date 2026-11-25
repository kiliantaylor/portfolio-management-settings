"""Tests for vol_target.py (book vol target, Kilian 2026-11-24). python3 test_vol_target.py"""
import json, math, os, tempfile
import vol_target as V

def run():
    tmp = tempfile.mkdtemp(); V.STATE = os.path.join(tmp, 'vt.json')
    cfg = {'vol_target': {'mode': 'live', 'target': 0.20, 'window': 21, 'step': 0.10}}
    r = V.decide('2026-01-02', 0.40, 21, cfg); assert abs(r['x'] - 0.5) < 1e-9 and r['moved']            # 20 / 40 = 0.50, from 1.0
    r = V.decide('2026-01-05', 0.36, 21, cfg); assert abs(r['x'] - 0.5) < 1e-9 and not r['moved']        # 0.556: less than 0.10 away
    r = V.decide('2026-01-06', 0.30, 21, cfg); assert abs(r['x'] - 0.6667) < 1e-3 and r['moved']         # 0.667: moves
    r = V.decide('2026-01-06', 0.30, 21, cfg); assert len(V.load()['history']) == 3                       # same date replaces its row
    r = V.decide('2026-01-07', 0.10, 21, cfg); assert r['x'] == 1.0                                       # never above 1
    assert V.current_x('2026-01-08', cfg) == 1.0 and abs(V.current_x('2026-01-07', {'vol_target': {'mode': 'shadow'}}) - 1.0) < 1e-12
    # shadow rows never count as the x a live book was sized at
    V.STATE = os.path.join(tmp, 'vt2.json'); sh = {'vol_target': dict(cfg['vol_target'], mode='shadow')}
    V.decide('2026-01-02', 0.40, 21, sh); assert V.prev_x('2026-01-05', 'live') == (1.0, None) and V.prev_x('2026-01-05')[0] == 0.5
    # realized vol: alternating +1% / -1% days at weight 1 -> sd 1% a day
    c = {'A': {}}; p = 100.0
    for i in range(30): c['A'][f'2026-02-{i + 1:02d}'] = p; p *= 1.01 if i % 2 == 0 else 1 / 1.01
    v, n = V.realized_vol({'A': 1.0}, c, '2026-02-30', 21); assert n == 21 and abs(v - 0.01 * math.sqrt(252)) < 0.01, v
    v2, _ = V.realized_vol({'A': 0.5}, c, '2026-02-30', 21); assert abs(v2 - v / 2) < 1e-9                # scales with weight
    print('test_vol_target ok')

if __name__ == '__main__': run()
