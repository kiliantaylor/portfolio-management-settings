"""Hard stops for project rules (Kilian 2026-11-05: "how do i make you follow the rules strictly" -> rules in scripts, not text).

A guarded step refuses to run unless the caller gives a reason with --why; every override is logged to
data/rule_overrides.jsonl (kept 90 days, pruned on each write) so Kilian can see when and why a rule was bypassed.

    python3 guards.py log [N]     the last N overrides (default 20)
"""
import datetime as dt, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'data', 'rule_overrides.jsonl')
KEEP_DAYS = 90


def _now(): return dt.datetime.now(dt.timezone.utc)


def need_why(rule, why, msg, **ctx):
    """Stop with `msg` unless `why` is given; a given reason is logged with the rule and context."""
    if not (why or '').strip():
        sys.exit(f'REFUSED ({rule}): {msg}\nTo go ahead anyway, re-run with --why "<reason>" (logged to data/rule_overrides.jsonl).')
    log(rule, why, **ctx)


def log(rule, why, **ctx):
    cut = (_now() - dt.timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%dT%H:%MZ')
    try: rows = [l for l in open(LOG) if l.strip() and json.loads(l).get('utc', '') >= cut]
    except OSError: rows = []
    rows.append(json.dumps(dict(utc=_now().strftime('%Y-%m-%dT%H:%MZ'), rule=rule, why=why, **ctx)) + '\n')
    open(LOG, 'w').writelines(rows)


def ny_now():
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo('America/New_York'))


def session_open(now=None):
    """True between 9:30am and 4:15pm ET on a trading day (4:15 = when nightly.latest_close() counts today's bar)."""
    from staged_dates import HOLIDAYS
    n = now or ny_now()
    return n.weekday() < 5 and n.date().isoformat() not in HOLIDAYS and (9, 30) <= (n.hour, n.minute) < (16, 15)


if __name__ == '__main__':
    if sys.argv[1:2] == ['log']:
        try: rows = open(LOG).readlines()
        except OSError: rows = []
        for l in rows[-int(sys.argv[2] if len(sys.argv) > 2 else 20):]: print(l.rstrip())
    else: print(__doc__)
