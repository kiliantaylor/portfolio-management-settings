"""Should the hourly watch republish Price Targets? Skips the multi-MB publish when nothing a reader acts on has moved.

python3 publish_check.py D           -> prints {"publish": true|false, "why": [...]} for the build at run date D
python3 publish_check.py D --stamp   -> records what was just published (run right after every Price Targets publish)

Publish when any of: no stamp yet or the stamp is for an earlier run date; trades.json changed; a name's action,
trade_shares, trim_above / add_below or next trim / add (price, shares) in runs/D/*/review.json changed; a name's spot
moved more than MOVE since the last publish; the last publish is older than MAX_AGE_H hours.
State: data/hourly_publish_state.json.
"""
import glob, hashlib, json, math, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'data', 'hourly_publish_state.json')
MOVE = 0.02       # 2% move in a name's price since the last publish
MAX_AGE_H = 3.0   # republish at least this often while the market is open

def trades_sha():
    return hashlib.sha256(open(os.path.join(HERE, 'data', 'trades.json'), 'rb').read()).hexdigest()

def snapshot(D):
    names = {}
    for p in glob.glob(os.path.join(HERE, 'runs', D, '*', 'review.json')):
        try: r = json.load(open(p))
        except (OSError, ValueError): continue
        dec = r.get('decider') or {}
        lv = lambda k: [(dec.get(k) or {}).get('price'), (dec.get(k) or {}).get('shares')]
        names[r.get('symbol') or os.path.basename(os.path.dirname(p))] = dict(
            act=[r.get('action'), r.get('trade_shares'), r.get('trim_above'), r.get('add_below'), lv('next_trim'), lv('next_add')],
            spot=r.get('spot'))
    return dict(run_date=D, trades_sha256=trades_sha(), names=names, at=time.time())

def check(D):
    import subprocess  # Kilian 2026-11-05: never publish a build that breaks a rule rules_check.py can see
    rc = subprocess.run([sys.executable, os.path.join(HERE, 'rules_check.py'), D], capture_output=True, text=True, cwd=HERE)
    if rc.returncode:
        try: v = json.loads(rc.stdout)['violations']
        except ValueError: v = [dict(rule='rules_check', why=(rc.stderr or rc.stdout).strip()[-300:])]
        return dict(publish=False, blocked=True, why=['BLOCKED %s %s: %s' % (x['rule'], x.get('symbol', ''), x['why']) for x in v])
    now = snapshot(D)
    try: old = json.load(open(STATE))
    except (OSError, ValueError): return dict(publish=True, why=['no stamp yet'])
    why = []
    if old.get('run_date') != D: why.append('new run date %s' % D)
    if old.get('trades_sha256') != now['trades_sha256']: why.append('trades.json changed')
    age = (now['at'] - old.get('at', 0)) / 3600
    if age > MAX_AGE_H: why.append('last publish %.1f h ago' % age)
    on = old.get('names', {})
    for s, v in sorted(now['names'].items()):
        o = on.get(s)
        if o is None: why.append(s + ' new on the page'); continue
        if o.get('act') != v['act']: why.append(s + ' trade or level changed')
        if o.get('spot') and v['spot'] and abs(math.log(v['spot'] / o['spot'])) > MOVE:
            why.append('%s moved %+.1f%%' % (s, 100 * (v['spot'] / o['spot'] - 1)))
    return dict(publish=bool(why), why=why)

def page_key(path):  # the live-patch base key the page computes from its embedded data (report_template.html LIVE)
    import re
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', open(path).read(), re.S)
    d = json.loads(m.group(1).replace('<\\/', '</'))
    return f"{d.get('as_of')}|{d.get('generated')}|{(d.get('live') or {}).get('time_utc')}"

def push_live():
    """2026-11-04: publish and live push are one step. Every build has its own live-patch key, so a publish without
    the push leaves the page on the close until someone pushes. --stamp now runs live/nightly.sh push itself (up to 3
    tries) and checks that live-data's slice.json carries this page's key; it exits 1 loudly if not."""
    import subprocess, tempfile, time as _t
    want = page_key(os.path.join(HERE, 'report.html'))
    repo = 'https://github.com/kiliantaylor/portfolio-management-settings'
    for i in range(3):
        w = tempfile.mkdtemp()
        r = subprocess.run(f'git clone -q --depth 1 {repo} {w}/m && bash {w}/m/live/nightly.sh push', shell=True, capture_output=True, text=True, cwd=w)
        got = subprocess.run(f'git clone -q --depth 1 -b live-data {repo} {w}/s && cat {w}/s/slice.json', shell=True, capture_output=True, text=True, cwd=w)
        subprocess.run(['rm', '-rf', w], cwd='/')
        try: base = json.loads(got.stdout).get('base')
        except ValueError: base = None
        if base == want:
            print(json.dumps(dict(live_push='ok', base=base))); return
        print(json.dumps(dict(live_push='retry', attempt=i + 1, want=want, live_data=base, err=((r.stderr or '') + (got.stderr or ''))[-300:])), file=sys.stderr)
        _t.sleep(5 * (i + 1))
    print(json.dumps(dict(live_push='FAILED', want=want, live_data=base)) + '  <- live prices stay off the page until live/nightly.sh push succeeds', file=sys.stderr)
    sys.exit(1)

if __name__ == '__main__':
    D = sys.argv[1]
    if '--stamp' in sys.argv:
        json.dump(snapshot(D), open(STATE, 'w')); print(json.dumps(dict(stamped=D)))
        if '--no-push' not in sys.argv: push_live()
    else:
        print(json.dumps(check(D)))
