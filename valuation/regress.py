"""Frozen-date regression check (Kilian 2026-10-28, "implement suggestion 5"): run the pipeline's math on a saved day's inputs
with the code as it is now, and diff the outputs against what the code produced when the day was saved. Run it before any
code change to combine/allocate/score/review/conviction goes live: intended changes show up as expected diffs, accidental
ones get caught. Nothing on the shared folder is written except valuation/regress/<D>/ by `save`.

  python3 regress.py save D            snapshot D's inputs (runs/D without workbooks/pages, valuation/data, portfolio, risk,
                                        revisions_sleeve) to regress/D/snapshot.tar.gz, run the check once, store it as golden
  python3 regress.py check D           combine (dry run) on every name of D vs golden
  python3 regress.py check D --sizing  also conviction, allocate, score, review in the sandbox vs golden (minutes)

Sandbox: a local copy (/tmp/vregress/D) of the tracked code from the working tree (git ls-files) plus the snapshot; other run
dates and untracked folders are symlinked read-only by convention (combine runs with --dry-run, and the sizing scripts write
only runs/D and data/, which are local copies). Tolerance: relative 1e-6 on numbers.
"""
import json, os, re, shutil, subprocess, sys, tarfile

ROOT = '/mnt/project-files'; HERE = os.path.join(ROOT, 'valuation'); REG = os.path.join(HERE, 'regress')
SNAP_DIRS = ['valuation/data', 'portfolio', 'risk', 'revisions_sleeve']
SKIP_EXT = ('.xlsx', '.html', '.pyc', '.py')  # code comes from the working tree, not the snapshot
TOL = 1e-6


def names(rd): return sorted(s for s in os.listdir(rd) if os.path.isfile(os.path.join(rd, s, 'result.json')))


def snapshot(D):
    os.makedirs(os.path.join(REG, D), exist_ok=True); out = os.path.join(REG, D, 'snapshot.tar.gz')
    keep = lambda ti: None if ti.name.endswith(SKIP_EXT) or '__pycache__' in ti.name else ti
    with tarfile.open(out, 'w:gz') as t:
        t.add(os.path.join(HERE, 'runs', D), arcname=f'valuation/runs/{D}', filter=keep)
        for d in SNAP_DIRS:
            if os.path.exists(os.path.join(ROOT, d)): t.add(os.path.join(ROOT, d), arcname=d, filter=keep)
    return out


def sandbox(D):
    sb = f'/tmp/vregress/{D}'; shutil.rmtree(sb, ignore_errors=True); os.makedirs(sb)
    files = subprocess.run(['git', '-c', 'safe.directory=*', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
    for f in filter(None, files):
        os.makedirs(os.path.dirname(os.path.join(sb, f)) or sb, exist_ok=True); shutil.copy2(os.path.join(ROOT, f), os.path.join(sb, f))
    with tarfile.open(os.path.join(REG, D, 'snapshot.tar.gz')) as t: t.extractall(sb, filter='data')
    for base in ['', 'valuation', 'valuation/runs']:  # everything else: symlinks to the live folder (read only by convention)
        live = os.path.join(ROOT, base)
        for e in os.listdir(live):
            p = os.path.join(sb, base, e)
            if e.startswith(('.', '_trash')) or os.path.lexists(p): continue
            os.symlink(os.path.join(live, e), p)
    return sb


def run(sb, args, env=None):
    r = subprocess.run(['python3'] + args, cwd=os.path.join(sb, 'valuation'), capture_output=True, text=True, timeout=10800,
                       env=dict(os.environ, **(env or {})))
    if r.returncode: print(f'  {args[0]} exit {r.returncode}: {r.stderr.strip()[-400:]}')
    return r.stdout


def combine_out(sb, D):
    syms = names(os.path.join(sb, 'valuation', 'runs', D)); out = {}
    for line in run(sb, ['combine.py', '--dry-run', D] + syms).splitlines():
        m = re.match(r'^(\S+)\s+spot ([\d.]+)\s+target ([\d.]+).*?mkt tgt ([\d.]+)\s+llm tgt ([\d.]+)', line)
        if m: out[m.group(1)] = dict(target=float(m.group(3)), mkt=float(m.group(4)), research=float(m.group(5)))
        elif line.split()[:1] and line.split()[0] in syms: out[line.split()[0]] = dict(line=line.strip()[:160])
    return out


def sizing_out(sb, D):
    for a in (['conviction.py', D], ['allocate.py', D], ['score.py', D], ['review.py', D]): run(sb, a)
    rd = os.path.join(sb, 'valuation', 'runs', D); j = lambda f: json.load(open(os.path.join(rd, f)))
    port, rs = j('portfolio.json'), j('review_summary.json')
    out = dict(book_sharpe=port.get('sharpe_combo', {}).get('base_sharpe'), target_shares=rs['sizing'].get('target_shares'), names={})
    for s in names(rd):
        f = os.path.join(rd, s, 'review.json')
        if os.path.exists(f):
            r = json.load(open(f)); out['names'][s] = {k: r.get(k) for k in ('action', 'trade_shares', 'add_below', 'trim_above', 'mu')}
    return out


def diff(a, b, path=''):
    if isinstance(a, dict) and isinstance(b, dict):
        return [x for k in sorted(set(a) | set(b)) for x in diff(a.get(k), b.get(k), f'{path}.{k}' if path else k)]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return [] if abs(a - b) <= TOL * max(1.0, abs(a), abs(b)) else [f'{path}: {a} -> {b}']
    return [] if a == b else [f'{path}: {a} -> {b}']


def main():
    a = sys.argv[1:]; cmd, D = a[0], a[1]; sizing = '--sizing' in a or cmd == 'save'
    gf = os.path.join(REG, D, 'golden.json')
    if cmd == 'save': print('snapshot', snapshot(D))
    elif not os.path.exists(gf): sys.exit(f'no golden for {D}: python3 regress.py save {D}')
    sb = sandbox(D); now = dict(combine=combine_out(sb, D))
    if sizing: now['sizing'] = sizing_out(sb, D)
    if cmd == 'save':
        json.dump(now, open(gf, 'w'), indent=1); print(f'golden saved: {len(now["combine"])} names' + (', sizing' if sizing else '')); return
    gold = json.load(open(gf)); d = diff({k: gold.get(k) for k in now}, now)
    print(f'regress {D}: {len(d)} differences' + ('' if d else ' (outputs identical)'))
    for x in d[:80]: print('  ' + x)
    sys.exit(1 if d else 0)


if __name__ == '__main__':
    main()
