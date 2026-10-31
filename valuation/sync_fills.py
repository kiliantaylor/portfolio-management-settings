"""Bring the Price Targets page and the holdings dashboard in line with data/trades.json after a fill
(Kilian 2026-10-08: "price targets doesn't have the correct amount of shares for tjx, make sure this auto-updates going forward").

python3 sync_fills.py           -> re-runs review.py at the newest run date (trim/add levels from the shares actually held),
                                   build_report.py, and the dashboard (ensure_history, prep_v6, build_v6); then writes the stamp.
python3 sync_fills.py --check   -> prints {"stale": true|false}: whether trades.json changed since the last sync or nightly build.
Publishing stays with the session that owns the pages: after a sync, republish report.html (Price Targets) and
../portfolio/dashboard.html (Kilian's Holdings). The 17:52 re-mark also writes the stamp (it rebuilds everything)."""
import glob, hashlib, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); PORT = os.path.join(os.path.dirname(HERE), 'portfolio')
STAMP = os.path.join(HERE, 'data', 'page_sync_stamp.json')
digest = lambda: hashlib.sha256(open(os.path.join(HERE, 'data', 'trades.json'), 'rb').read()).hexdigest()

def stale():
    try: return json.load(open(STAMP)).get('trades_sha256') != digest()
    except FileNotFoundError: return True

def stamp(date):
    json.dump(dict(trades_sha256=digest(), run_date=date), open(STAMP, 'w'), indent=1)

if __name__ == '__main__':
    if '--check' in sys.argv: print(json.dumps(dict(stale=stale()))); sys.exit(0)
    if '--stamp' in sys.argv: stamp(sys.argv[-1]); sys.exit(0)
    D = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(HERE, 'runs', '*', 'review_summary.json')))[-1]
    for cmd, cwd in ([['python3', 'review.py', D], HERE], [['python3', 'build_report.py', D], HERE],
                     [['python3', 'ensure_history.py'], PORT], [['python3', 'prep_v6.py'], PORT], [['python3', 'build_v6.py'], PORT]):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        print(' '.join(cmd), '->', 'ok' if r.returncode == 0 else 'FAILED\n' + r.stderr[-2000:]); 
        if r.returncode: sys.exit(1)
    stamp(D); print('synced', D)
