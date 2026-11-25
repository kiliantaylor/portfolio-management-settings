"""gate_carry.py: only figures that moved are re-opened (Kilian 2026-11-23). python3 test_gate_carry.py"""
import json, os, tempfile
import gate_carry as G

def run():
    root = tempfile.mkdtemp(); G.RUNS = root
    b = os.path.join(root, '2026-11-17', 'XYZ', 'gate'); os.makedirs(b)
    rd = os.path.join(root, '2026-11-20', 'XYZ'); os.makedirs(os.path.join(rd, 'gate'))
    json.dump(dict(base='2026-11-17', gate_rounds=[{}]), open(os.path.join(rd, 'update.json'), 'w'))
    res = [dict(origin='supervisor guidance', item='rev guide', value='100-110', source_url='u1', result='ok', _src='shard', published='2026-11-01'),
           dict(origin='supervisor claim', item='claim (src 3)', value='Old sentence 5%.', source_url='u2', result='ok', _src='shard'),
           dict(origin='supervisor model', item='capex', value='9', source_url='u3', result='unsupported', _src='shard')]
    json.dump(dict(results=res), open(os.path.join(b, 'merged.json'), 'w'))
    it = lambda i, v, u, basis='guidance': dict(origin='x', item=i, value=v, basis=basis, urls=[u], fact_ids=[], derivation='')
    groups = {'u1': [it('rev guide', '100-110', 'u1'), it('publication date', 'published date', 'u1', 'timeliness')],
              'u2': [it('claim (src 3)', 'Old sentence 5%.', 'u2', 'claim'), it('claim (src 3)', 'Edited sentence 6%.', 'u2', 'claim')],
              'u3': [it('capex', '9', 'u3', 'estimate')]}
    g, a, c, basis = G.carry(rd, 1, groups, [])
    checks = [('unchanged figure carried', 'u1' not in g and any(x['item'] == 'rev guide' for x in c)),
              ('date-only open dropped when the carried row has a date', 'u1' not in g),
              ('unchanged sentence carried, edited one opened', [x['value'] for x in g.get('u2', [])] == ['Edited sentence 6%.']),
              ('failed result never carries', 'u3' in g),
              ('carried rows tagged', all(x.get('carried_from_round') == 'base 2026-11-17' for x in c)),
              ('value change re-opens', 'u1' in G.carry(rd, 1, {'u1': [it('rev guide', '100-112', 'u1')]}, [])[0])]
    bad = [n for n, ok in checks if not ok]
    print('test_gate_carry:', 'FAILED ' + '; '.join(bad) if bad else f'{len(checks)} checks ok')
    return not bad

if __name__ == '__main__': raise SystemExit(0 if run() else 1)
