"""How much the forecasters' sources overlap, per ticker (Kilian 2026-09-29: agents should not all regurgitate the
same information).

For each runs/DATE/SYM/ it reads the five agent_*.json files (and round1/ if present) and writes source_overlap.json:
per agent the number of sources, how many no other agent cited, its source types and lane; pairwise Jaccard overlap
of cited URLs (normalized: scheme, www, query, fragment and trailing slash dropped); distinct URLs / total citations;
and the URLs cited by 3+ agents. The supervisor reads it before reconciling.

Usage: python3 source_overlap.py DATE [SYM ...]   (all tickers with agent files when none are given)"""
import glob, itertools, json, os, re, sys
from urllib.parse import urlparse
HERE = os.path.dirname(os.path.abspath(__file__))


def norm(u):
    if not u: return None
    p = urlparse(u.strip()); host = re.sub(r'^www\.', '', p.netloc.lower())
    return (host + p.path.rstrip('/')).lower() or None


def analyse(d):
    files = sorted(glob.glob(os.path.join(d, 'agent_*.json')))
    if not files: return None
    A = {}
    for f in files:
        a = json.load(open(f)); src = a.get('sources') or []
        urls = {norm(s.get('url')) for s in src if isinstance(s, dict)} - {None}
        types = {}
        for s in src:
            if isinstance(s, dict): t = s.get('type') or 'untyped'; types[t] = types.get(t, 0) + 1
        A[a.get('agent_id') or os.path.basename(f)[:-5]] = dict(urls=urls, n=len(src), types=types, lane=a.get('lane'))
    count = {}
    for v in A.values():
        for u in v['urls']: count[u] = count.get(u, 0) + 1
    pairs = {f'{a}~{b}': round(len(A[a]['urls'] & A[b]['urls']) / max(len(A[a]['urls'] | A[b]['urls']), 1), 3)
             for a, b in itertools.combinations(sorted(A), 2)}
    total = sum(len(v['urls']) for v in A.values())
    return dict(
        agents={k: dict(n_sources=v['n'], n_urls=len(v['urls']), unique_to_agent=sum(1 for u in v['urls'] if count[u] == 1),
                        types=v['types'], lane=v['lane']) for k, v in A.items()},
        pairwise_jaccard=pairs, mean_jaccard=round(sum(pairs.values()) / len(pairs), 3) if pairs else None,
        distinct_urls=len(count), total_citations=total, distinct_share=round(len(count) / total, 3) if total else None,
        cited_by_3_plus=sorted(u for u, c in count.items() if c >= 3))


if __name__ == '__main__':
    date = sys.argv[1]; syms = [s.upper() for s in sys.argv[2:]] or sorted(
        os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(HERE, 'runs', date, '*', 'agent_1.json')))
    for s in syms:
        rd = os.path.join(HERE, 'runs', date, s); out = dict(symbol=s, as_of=date, final=analyse(rd))
        if os.path.isdir(os.path.join(rd, 'round1')): out['round1'] = analyse(os.path.join(rd, 'round1'))
        json.dump(out, open(os.path.join(rd, 'source_overlap.json'), 'w'), indent=1)
        r1 = out.get('round1') or out['final']
        print(f"{s:6} round1: distinct {r1['distinct_urls']}/{r1['total_citations']} ({r1['distinct_share']}), mean Jaccard {r1['mean_jaccard']}, "
              f"cited by 3+: {len(r1['cited_by_3_plus'])}")
