"""One run at a time on the shared folder (2026-10-21, the speedup list Kilian approved).

nightly.py holds the lock for a whole run (nightly, --after-targets, --retry-stale), so a research or earnings thread's
--after-targets waits for tonight's re-mark instead of rebuilding the same files at the same time. A thread that publishes
the Price Targets page holds it from copying report.html until the publish is done, so two threads never publish
different builds over each other:
    python3 runlock.py take "publish ABT"      # waits up to 9 min while a run or another publish holds it; prints a token
                                               # (still held: exits 2; rerun with --wait-min 90 under Bash run_in_background)
    cp report.html <scratchpad> ... publish ...
    python3 runlock.py release TOKEN
    python3 runlock.py show                    # the queue: who holds it, who waits, since when

How (v2, 2026-10-21 22:30Z): the shared folder is an rclone mount, and each session's container keeps its own view of it,
so "create only if absent" (O_EXCL) is not exclusive between sessions: at 22:23Z a run and a publish both "created"
data/nightly.lock and one overwrote the other. Now every claimant writes its own ticket, data/locks/<ms>-<token>.json
(never overwritten), waits SETTLE seconds so tickets written elsewhere reach this view, and holds the lock once its ticket
is first in (time, token) order on two looks CONFIRM seconds apart. Release deletes the ticket. A ticket older than its own
stale_s (3 h for a run, 20 min for a publish, judged from the time written inside it) is from a dead session and is removed.
A data/nightly.lock left by the first version still counts as the holder until it is released or stale.
"""
import datetime as dt, json, os, socket, sys, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, 'data', 'locks')
LEGACY = os.path.join(HERE, 'data', 'nightly.lock')
SETTLE, CONFIRM, POLL = 45, 15, 15


def _read(p):
    try:
        with open(p) as f: return json.load(f)
    except (OSError, ValueError): return None


def _age(rec, path):
    t = rec.get('t')
    if t is None:  # first-version lock: started is a UTC timestamp
        try: t = dt.datetime.strptime(rec['started'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).timestamp()
        except (KeyError, ValueError):
            try: t = os.path.getmtime(path)
            except OSError: return 0
    return time.time() - t


def queue(say=None):
    """Live tickets in lock order (the first one holds the lock); stale ones are removed on the way."""
    out = []
    try: names = os.listdir(DIR)
    except OSError: names = []
    paths = [os.path.join(DIR, n) for n in names if n.endswith('.json')]
    if os.path.exists(LEGACY): paths.append(LEGACY)
    for p in paths:
        rec = _read(p)
        if rec is None: continue  # half-written or just removed
        if _age(rec, p) > (rec.get('stale_s') or 10800):
            if say: say(f'removing a stale lock ticket ({_age(rec, p) / 60:.0f} min old): {rec}')
            try: os.remove(p)
            except OSError: pass
            continue
        rec['_path'] = p
        out.append(rec)
    out.sort(key=lambda r: (0 if r['_path'] == LEGACY else 1, r.get('t') or 0, r.get('token', '')))
    return out


def holder():
    q = queue()
    return {k: v for k, v in q[0].items() if k != '_path'} if q else None


def take(what, D=None, wait_s=5400, stale_s=10800, log=None):
    """Hold the lock (waits while someone else does). Returns the token that release() needs."""
    say = log or (lambda m: print(m, file=sys.stderr, flush=True))
    os.makedirs(DIR, exist_ok=True)
    tok, t0 = uuid.uuid4().hex, time.time()
    rec = dict(token=tok, pid=os.getpid(), host=socket.gethostname(), run=what, D=D, stale_s=stale_s, t=round(t0, 3),
               started=f'{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}')
    mine = os.path.join(DIR, f'{int(t0 * 1000):015d}-{tok}.json')
    with open(mine, 'w') as f: json.dump(rec, f)
    time.sleep(SETTLE)
    firsts, said, h = 0, None, None
    while True:
        q = queue(say)
        if not any(r.get('token') == tok for r in q):  # removed under us (should not happen): write it again
            with open(mine, 'w') as f: json.dump(rec, f)
            firsts = 0
        elif q[0].get('token') == tok:
            firsts += 1
            if firsts >= 2: break
            time.sleep(CONFIRM); continue
        else:
            firsts = 0
            h = {k: v for k, v in q[0].items() if k != '_path'}
            if said != h.get('token'): say(f'waiting for the lock: {h}'); said = h.get('token')
        if time.time() - t0 > wait_s:
            release(tok)
            raise SystemExit(f'runlock: still held after {wait_s / 60:.0f} min: {h if q else None}')
        time.sleep(POLL)
    waited = round(time.time() - t0 - SETTLE - CONFIRM)
    if waited > 1: say(f'waited {waited}s for the lock')
    return tok


def release(tok):
    done = False
    try:
        for n in os.listdir(DIR):
            if tok in n:
                os.remove(os.path.join(DIR, n)); done = True
    except OSError: pass
    try:
        if (_read(LEGACY) or {}).get('token') == tok: os.remove(LEGACY); done = True
    except OSError: pass
    return done


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['take'] and len(a) in (2, 4) and (len(a) == 2 or a[2] == '--wait-min'):
        try: print(take(a[1], wait_s=60 * float(a[3]) if len(a) == 4 else 540, stale_s=1200))
        except SystemExit as e: print(e, file=sys.stderr); sys.exit(2)
    elif a[:1] == ['release'] and len(a) == 2: print('released' if release(a[1]) else 'not held with that token (already released or stale-removed)')
    elif a[:1] == ['show']:
        q = queue()
        print('\n'.join(('HOLDS  ' if i == 0 else 'waits  ') + json.dumps({k: v for k, v in r.items() if k != '_path'}) for i, r in enumerate(q)) or 'free')
    else: raise SystemExit(__doc__)
