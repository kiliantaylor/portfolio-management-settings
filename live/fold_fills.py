"""Append the fills entered on the page (repo folder fills/) to a trades.json for good.

    python3 live/fold_fills.py FILLS_DIR VALUATION_DIR

Uses VALUATION_DIR/fill_intake.py, so the checks and the trades.json row format are the same as the live run's.
Prints fill_intake's {"logged", "already_in_trades", "rejected"}."""
import glob, json, os, subprocess, sys, tempfile

FILLS, V = (os.path.abspath(a) for a in sys.argv[1:3])
with tempfile.TemporaryDirectory() as tmp:
    docs = []
    for p in sorted(glob.glob(f'{FILLS}/*.json')):
        fid = os.path.splitext(os.path.basename(p))[0]
        doc = json.load(open(p)); doc['status'] = 'new'
        q = f'{tmp}/{fid}.json'; json.dump({'id': fid, 'data': doc}, open(q, 'w')); docs.append(q)
    if not docs: print(json.dumps({'logged': [], 'already_in_trades': [], 'rejected': []})); sys.exit(0)
    r = subprocess.run(['python3', 'fill_intake.py', *docs], cwd=V, capture_output=True, text=True)
    if r.returncode: sys.exit(r.stderr[-2000:])
    print(r.stdout.strip())
