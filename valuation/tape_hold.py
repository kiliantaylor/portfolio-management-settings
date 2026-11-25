"""Tape-triggered news hold (Kilian 2026-11-05 card 'Tape-triggered news hold', macro tape thread: "it's not wise to just buy
when our algorithm says to given the macro tape").

On a session where QQQ falls 4% or more, or SMH 7% or more, from the prior close (at the day's low or at the last price,
so a fall that touched the line and recovered still counts), adds and new buys in the names exposed to the shocked
sector (data/tape_hold.json 'exposed': the AI/data-center names and the watchlist semis) wait until that day's news is
traced to their drivers. Trims, exits and stops are unchanged. It is a news hold, not a price pause: the 11-05 test
(notes/tape/bigdown_fwd.py) found no timing edge in waiting after such days, so the hold lasts exactly until the
read-through is done, then the page decides as usual.

Who checks: the nightly (step 3b0t, `tape_hold.py check D`, before allocate.py) and every live run (live/remark.py,
on the live session). Who reads: allocate.py (new buys, binding 'tape_hold'), review.py (held adds and the next-share
table), swap_test.py (no swap-in, planned adds blocked), rules_check.py (no due buy in a held name), build_report.py
(page line).

Clearing is per name and per event, and needs the read-through file routines/readthrough_check.py passes, with a
verdict for that name. A 'moves' verdict also needs the driver change booked: a research round for the name dated on or
after the event.

    python3 tape_hold.py check D           record D's event if QQQ/SMH crossed the line (Yahoo daily bars)
    python3 tape_hold.py status [D]        names on hold as of D
    python3 tape_hold.py clear EVENT SYM [SYM ...] --readthrough FILE.json
                                           clear SYM's hold for the event on EVENT; then `live/nightly.sh tape` so the
                                           live job sees it before the next nightly slice
"""
import datetime as dt, glob, json, os, subprocess, sys, urllib.request
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, 'data', 'tape_hold.json')
ET = ZoneInfo('America/New_York')
DEFAULT = dict(triggers={'QQQ': -0.04, 'SMH': -0.07}, exposed=[], events={})


def load(path=None):
    try: c = json.load(open(path or PATH))
    except (OSError, ValueError): c = {}
    for k, v in DEFAULT.items(): c.setdefault(k, json.loads(json.dumps(v)))
    return c


def save(c, path=None):
    p = path or PATH; tmp = p + '.tmp'
    json.dump(c, open(tmp, 'w'), indent=1); os.replace(tmp, p)


def day_move(sym, session=None):
    """{date, prev_close, low, last, move_low, move_last} for the session (ET date; default the latest bar), from Yahoo
    daily bars. During the session the latest bar is the day so far."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=10d&interval=1d'
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20))['chart']['result'][0]
    q = r['indicators']['quote'][0]
    bars = [(dt.datetime.fromtimestamp(t, ET).date().isoformat(), lo, c) for t, lo, c in zip(r['timestamp'], q['low'], q['close']) if c and lo]
    i = len(bars) - 1 if session is None else next((k for k, b in enumerate(bars) if b[0] == session), None)
    if i is None or i == 0: return None
    d, lo, last = bars[i]; prev = bars[i - 1][2]
    if i == len(bars) - 1 and r['meta'].get('regularMarketPrice') and dt.datetime.fromtimestamp(r['meta'].get('regularMarketTime') or 0, ET).date().isoformat() == d:
        last = r['meta']['regularMarketPrice']; lo = min(lo, r['meta'].get('regularMarketDayLow') or lo, last)
    return dict(date=d, prev_close=round(prev, 2), low=round(lo, 2), last=round(last, 2),
                move_low=round(lo / prev - 1, 4), move_last=round(last / prev - 1, 4))


def check(D, path=None, record=True, fetch=day_move):
    """Record D's event when a trigger crossed its line. Returns (event or None, {sym: move or error})."""
    c = load(path); seen = {}; hit = {}
    for sym, line in c['triggers'].items():
        try: m = fetch(sym, D)
        except Exception as e: seen[sym] = dict(error=str(e)[:120]); continue
        if not m: seen[sym] = dict(error=f'no {D} bar'); continue
        seen[sym] = m
        if min(m['move_low'], m['move_last']) <= line: hit[sym] = dict(m, line=line)
    if not hit: return None, seen
    ev = c['events'].get(D) or dict(names=sorted(c['exposed']), cleared={})
    ev['trigger'] = {**(ev.get('trigger') or {}), **hit}  # widen, never narrow: a later, smaller move keeps the earlier hit
    ev.setdefault('recorded_utc', dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    c['events'][D] = ev
    if record: save(c, path)
    return ev, seen


def active(D, path=None):
    """{SYM: {event, why}} for every exposed name still on hold as of session D (events on or before D, not cleared)."""
    c = load(path); out = {}
    for E in sorted(c['events']):
        if E > D: continue
        ev = c['events'][E]
        for s in ev.get('names') or []:
            if s not in (ev.get('cleared') or {}) and s not in out:
                out[s] = dict(event=E, why=why(E, ev))
    return out


def why(E, ev):
    t = ev.get('trigger') or {}
    moves = ', '.join(f"{k} {min(v['move_low'], v['move_last']):+.1%}" for k, v in sorted(t.items()))
    md = dt.date.fromisoformat(E).strftime('%b %-d')
    return f"tape hold since {md} ({moves}): adds and new buys wait until that day's news is traced to drivers"


def summary(D, path=None):
    """What build_report.py puts on the page (DATA.tape_hold): open events and their names on hold."""
    c = load(path); act = active(D, path); ev = {}
    for s, a in act.items(): ev.setdefault(a['event'], []).append(s)
    return [dict(event=E, names=sorted(ns), why=why(E, c['events'][E]), trigger=c['events'][E].get('trigger'),
                 cleared=sorted((c['events'][E].get('cleared') or {}))) for E, ns in sorted(ev.items())] or None


def clear(E, syms, rt_file, path=None):
    c = load(path); ev = c['events'].get(E)
    if not ev: sys.exit(f'no tape event on {E}')
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'routines', 'readthrough_check.py'), rt_file], capture_output=True, text=True)
    if r.returncode: sys.exit('REFUSED: the read-through does not pass routines/readthrough_check.py\n' + r.stdout[-2000:])
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(rt_file), ET).date().isoformat()
    if mtime < E: sys.exit(f'REFUSED: {rt_file} was last written {mtime}, before the {E} event')
    R = json.load(open(rt_file)); bad = []
    for s in syms:
        n = (R.get('names') or {}).get(s)
        if s not in (ev.get('names') or []): bad.append(f'{s} is not on the {E} hold'); continue
        if not n or n.get('verdict') not in ('no_move', 'moves'): bad.append(f'{s}: no verdict in {rt_file}'); continue
        if n['verdict'] == 'moves':
            rounds = sorted(p.split('/')[-3] for p in glob.glob(os.path.join(HERE, 'runs', '*', s, 'supervisor.json')))
            if not rounds or rounds[-1] < E: bad.append(f"{s}: verdict 'moves' ({n.get('estimate')}) but no research round on or after {E}; book the driver change first (name_update.py)")
    if bad: sys.exit('REFUSED:\n- ' + '\n- '.join(bad))
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for s in syms:
        n = R['names'][s]
        ev.setdefault('cleared', {})[s] = dict(at_utc=now, readthrough=os.path.abspath(rt_file), verdict=n['verdict'],
                                              rows=n.get('rows'), estimate=n.get('estimate'))
    save(c, path)
    print(json.dumps(dict(event=E, cleared=syms, still_held=[s for s in ev['names'] if s not in ev['cleared']],
                          next='live/nightly.sh tape (so the live job sees the clear before tonight)')))


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] not in ('check', 'status', 'clear'): print(__doc__); sys.exit(1)
    if a[0] == 'check':
        ev, seen = check(a[1]); print(json.dumps(dict(date=a[1], event=ev, seen=seen)))
    elif a[0] == 'status':
        D = a[1] if len(a) > 1 else dt.datetime.now(ET).date().isoformat()
        print(json.dumps(dict(as_of=D, on_hold=active(D)), indent=1))
    else:
        if '--readthrough' not in a or len(a) < 5: sys.exit('usage: tape_hold.py clear EVENT SYM [SYM ...] --readthrough FILE.json')
        i = a.index('--readthrough'); clear(a[1], [s.upper() for s in a[2:i]], a[i + 1])
