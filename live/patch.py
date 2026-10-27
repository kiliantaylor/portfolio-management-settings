"""Small patch that turns the published page's DATA (base.json) into a fresh build's DATA.

A patch is {"base": key, "version": utc, "set": [[path, value], ...], "del": [path, ...]}. A path step is a dict key,
or "@SYM" for the entry of a list of dicts keyed by "symbol" (results, tranches). The page applies it with applyLive()
in report_template.html, which must stay in step with apply() below."""
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
    elif _bysym(a) and _bysym(b) and [e['symbol'] for e in a] == [e['symbol'] for e in b]:
        for ea, eb in zip(a, b):
            if ea != eb: diff(ea, eb, path + ('@' + eb['symbol'],), out)
    else:
        out['set'].append([list(path), b])
    return out

def _step(node, k):
    if isinstance(k, str) and k.startswith('@') and isinstance(node, list):
        return next(e for e in node if isinstance(e, dict) and e.get('symbol') == k[1:])
    return node[k]

def apply(data, patch):
    for path, v in patch['set']:
        node = data
        for k in path[:-1]: node = _step(node, k)
        node[path[-1]] = v
    for path in patch['del']:
        node = data
        for k in path[:-1]: node = _step(node, k)
        node.pop(path[-1], None)
    return data

if __name__ == '__main__':
    import sys
    a, b = (json.load(open(p)) for p in sys.argv[1:3])
    p = diff(a, b); assert apply(json.loads(json.dumps(a)), p) == b
    print(len(json.dumps(p)), 'bytes,', len(p['set']), 'sets,', len(p['del']), 'dels')
