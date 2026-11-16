#!/usr/bin/env python3
"""The live job's contract with valuation/: every function and script live/*.py calls must exist there.

    python3 live/contract.py VALUATION_DIR        exit 1 and name each missing piece

Why (2026-11-16): a 2026-11-14 rename in valuation/combine.py (standard_model -> model_leg) broke live/slice.py
--research with no warning until a push was attempted. This reads live/*.py without running it, finds each
`module.name` used on a module that lives in valuation/ (or market_color/), plus getattr(module, 'name') and each
'script.py' the job runs, and checks them against valuation/'s own source (top-level def, class, assignment or import),
again without importing anything. Run by live/slice.py before every slice (nightly push, research, tape), by the live
workflow on every run against the slice it uses, and by the contract workflow on every change to live/.
"""
import ast, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def top_names(path):
    names = set()
    for n in ast.parse(open(path).read(), path).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): names.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                for x in ast.walk(t):
                    if isinstance(x, ast.Name): names.add(x.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split('.')[0] for a in n.names)
        elif isinstance(n, (ast.If, ast.Try)):  # names defined under a top-level if/try count too
            for x in ast.walk(n):
                if isinstance(x, (ast.FunctionDef, ast.ClassDef)): names.add(x.name)
                elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store): names.add(x.id)
    return names


def uses(path, modules):
    """(module, name, line) for each module.name / getattr(module, 'name') on a valuation module, and scripts run."""
    tree = ast.parse(open(path).read(), path)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name for a in n.names if a.name in modules)
    out, scripts = [], set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in imported:
            out.append((n.value.id, n.attr, n.lineno))
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'getattr' and len(n.args) >= 2
              and isinstance(n.args[0], ast.Name) and n.args[0].id in imported
              and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)):
            out.append((n.args[0].id, n.args[1].value, n.lineno))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r'[a-z_][a-z0-9_]*\.py', n.value):
            scripts.add((n.value, n.lineno))
    return out, scripts


def check(val, live=HERE):
    val = os.path.abspath(val)
    dirs = [val, os.path.join(os.path.dirname(val), 'market_color')]
    mods = {}
    for d in dirs:
        for p in glob.glob(os.path.join(d, '*.py')):
            mods.setdefault(os.path.basename(p)[:-3], p)
    own = {os.path.basename(p) for p in glob.glob(os.path.join(live, '*.py'))}
    bad, names = [], {}
    for f in sorted(glob.glob(os.path.join(live, '*.py'))):
        if os.path.basename(f) == 'contract.py': continue
        refs, scripts = uses(f, mods)
        for m, a, ln in refs:
            if m not in names: names[m] = top_names(mods[m])
            if a not in names[m]:
                bad.append(f'{os.path.basename(f)}:{ln} uses {m}.{a}, which {os.path.relpath(mods[m], os.path.dirname(val))} no longer defines')
        for s, ln in scripts:
            if s in own or s[:-3] in mods: continue
            bad.append(f'{os.path.basename(f)}:{ln} runs {s}, which is not in valuation/, market_color/ or live/')
    if not mods: bad.append(f'no valuation modules found under {val}')
    return bad


if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit('usage: live/contract.py VALUATION_DIR')
    bad = check(sys.argv[1])
    for b in bad: print('LIVE CONTRACT BROKEN: ' + b, file=sys.stderr)
    if bad:
        print('Fix the caller in live/ (settings repo) or restore the name in valuation/ before pushing; the live job '
              'and the intraday research push both stop until then.', file=sys.stderr)
        sys.exit(1)
    print('live contract ok')
