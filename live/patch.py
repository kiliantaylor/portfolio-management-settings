"""Small patch that turns the published page's DATA (base.json) into a fresh build's DATA.

A patch is {"base": key, "version": utc, "set": [[path, value], ...], "del": [path, ...]}. A path step is a dict key,
or "@SYM" for the entry of a list of dicts keyed by "symbol" (results, tranches). Setting a path that ends in "@SYM"
replaces that entry or appends it, deleting one removes it, and "#order" sets the list's symbol order, so a name joining
or leaving the page costs one entry, not the whole list. The page applies it with applyLive() in report_template.html,
which must stay in step with apply() below."""
import json

def _bysym(x):
    return isinstance(x, list) and x and all(isinstance(e, dict) and 'symbol' in e for e in x)

def diff(a, b, path=(), out=None):
    out = out if out is not None else {'set': [], 'del': []}
    if isinstance(a, dict) and isinstance(b, dict):
        for k in b:
            if k not in a: out['set'].append([list(path) + [k], b[k]])
            elif a[k] != b[k]: diff(a[k], b[k], path + (k,), out)
        out['del'] += [list(path) + [k] for k in a if k not in b]
    elif _bysym(a) and _bysym(b):
        old = {e['symbol']: e for e in a}
        for eb in b:
            ea = old.get(eb['symbol'])
            if ea is None: out['set'].append([list(path) + ['@' + eb['symbol']], eb])
            elif ea != eb: diff(ea, eb, path + ('@' + eb['symbol'],), out)
        new = {e['symbol'] for e in b}
        out['del'] += [list(path) + ['@' + x] for x in old if x not in new]
        kept = [e['symbol'] for e in a if e['symbol'] in new] + [e['symbol'] for e in b if e['symbol'] not in old]
        if kept != [e['symbol'] for e in b]:  # apply() keeps old entries in place and appends new ones
            out['set'].append([list(path) + ['#order'], [e['symbol'] for e in b]])
    else:
        out['set'].append([list(path), b])
    return out

def _step(node, k):
    if isinstance(k, str) and k.startswith('@') and isinstance(node, list):
        return next(e for e in node if isinstance(e, dict) and e.get('symbol') == k[1:])
    return node[k]

def _put(node, k, v):
    if isinstance(node, list) and k == '#order':
        by = {e['symbol']: e for e in node}; node[:] = [by[x] for x in v if x in by]
    elif isinstance(node, list) and isinstance(k, str) and k.startswith('@'):
        i = next((i for i, e in enumerate(node) if isinstance(e, dict) and e.get('symbol') == k[1:]), None)
        if i is None: node.append(v)
        else: node[i] = v
    else: node[k] = v

def _drop(node, k):
    if isinstance(node, list) and isinstance(k, str) and k.startswith('@'): node[:] = [e for e in node if not (isinstance(e, dict) and e.get('symbol') == k[1:])]
    else: node.pop(k, None)

def apply(data, patch):  # deletions first, so an "@SYM" step never finds a removed entry
    for path in patch['del']:
        node = data
        for k in path[:-1]: node = _step(node, k)
        _drop(node, path[-1])
    for path, v in patch['set']:
        node = data
        for k in path[:-1]: node = _step(node, k)
        _put(node, path[-1], v)
    return data

if __name__ == '__main__':
    import sys
    a, b = (json.load(open(p)) for p in sys.argv[1:3])
    p = diff(a, b); assert apply(json.loads(json.dumps(a)), p) == b
    print(len(json.dumps(p)), 'bytes,', len(p['set']), 'sets,', len(p['del']), 'dels')
