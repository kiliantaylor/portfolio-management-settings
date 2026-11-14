"""Reg T / good-faith lock (Kilian 2026-11-04 and 2026-11-05: "i cannot sell avgo today because of reg t"). Cash account,
T+1: a lot bought on session B is paid with cash that settles B+1, and the broker blocks selling the name on session B and
on B+1. So a held name with a lot bought on session B is sellable from the second session after B. review.py (trims) and
swap_test.py (swap-outs) both use this, in the live and the nightly run (the nightly's levels are for the next session)."""
from staged_dates import next_session


def sellable_from(lots, session):
    """The first session the name can be sold, if a lot still locks it on `session`; else None."""
    ds = [l['date'] for l in lots or [] if l.get('date') and l.get('shares', 0) > 0]
    until = max((next_session(next_session(d)) for d in ds), default=None)
    return until if until and session < until else None


def reason(until):
    return f'Reg T: bought with cash that has not settled; sellable from {until}'
