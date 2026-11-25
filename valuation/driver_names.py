"""Names with a driver model, cheaply (Kilian 2026-11-17: "models should be autoupdated with relevant decisions flagged to me,
if not daily then at least whenever there is relevant news").

A name has a driver model when the research round its target rests on (runs/D/SYM/result.json research_as_of, from the name's newest
result file in the last 7 nights on or before the given date) has a driver-led set (last_print.driver_model, the same test as the buy
gate). Every driver-model name is active (active_names.py), so tonight's re-mark has a result.json for each. Names that
universe.py excludes (defense, financials, removed_names) are left out.

    python3 driver_names.py [D]      prints {SYM: research_as_of}
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.path.join(HERE, 'runs')
sys.path.insert(0, HERE)


def recent_runs(D=None, n=7):
    """The newest n nights (<= D) that have result files."""
    out = []
    for d in sorted((x for x in os.listdir(RUNS) if len(x) == 10 and x[4] == '-'), reverse=True):
        if D and d > D: continue
        p = os.path.join(RUNS, d)
        if any(os.path.exists(os.path.join(p, s, 'result.json')) for s in os.listdir(p) if s.isupper()): out.append(d)
        if len(out) >= n: break
    return out


def names(D=None):
    """{SYM: research_as_of} for every driver-model name, from each name's newest result.json in the last 7 nights <= D (a
    name skipped by a re-mark, e.g. AMBQ on 2026-11-16, still counts)."""
    import last_print, universe
    seen, out = set(), {}
    for d in recent_runs(D):
        for s in sorted(os.listdir(os.path.join(RUNS, d))):
            f = os.path.join(RUNS, d, s, 'result.json')
            if s in seen or not (s.isupper() and os.path.exists(f)): continue
            seen.add(s)
            try: ra = json.load(open(f)).get('research_as_of')
            except Exception: continue
            try:
                if universe.reason(s): continue
            except Exception: pass
            if last_print.driver_model(s, ra): out[s] = ra
    return dict(sorted(out.items()))


if __name__ == '__main__':
    print(json.dumps(names(sys.argv[1] if len(sys.argv) > 1 else None), indent=1))
