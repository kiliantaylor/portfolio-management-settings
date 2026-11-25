"""Owner decisions override the citation gate (Kilian 2026-11-16 12:34Z: "generally if i manually decide something on a
card and or state it myself the citation gate can be overridden").

When Kilian has decided an item on a card, or stated it himself in a thread, a gate failure on that item is closed as
`accepted_by_kilian` in runs/U/SYM/citation_audit.json, with his message id and quote logged (also in
data/rule_overrides.jsonl). Only his own words count: a card he tapped, or a message he wrote. Never a Claude's summary.

    python3 gate_accept.py list U SYM                                   open failures, numbered
    python3 gate_accept.py accept U SYM --msg cmsg_... --quote "..." [--items 0,2,5 | --all] [--card cmsg_...]

Rule 26 (Kilian 2026-11-18 22:21Z card "Skip under 0.5%, note it"): --msg may be the standing card
cmsg_01HdhANNPxz1f5xqDwR22cfL6x7Uwm1s8RxKyE9kDsHU8Z only for rows that a valid small_flag_notes entry covers (small_flag.py:
every alternative under 0.5%, feeds-forward check passed, search written). `small_flag.py accept U SYM` does this in one step.

`accept` moves the chosen open_failures to accepted_carried (status accepted_by_kilian) and sets clean true when none
remain open. It changes no figure in the set.
"""
import argparse, datetime as dt, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))


def _p(U, s): return os.path.join(os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs')), U, s, 'citation_audit.json')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['list', 'accept']); ap.add_argument('U'); ap.add_argument('SYM')
    ap.add_argument('--msg'); ap.add_argument('--quote'); ap.add_argument('--items'); ap.add_argument('--all', action='store_true'); ap.add_argument('--card')
    a = ap.parse_args(); s = a.SYM.upper(); p = _p(a.U, s); A = json.load(open(p)); op = A.get('open_failures') or []
    if a.cmd == 'list':
        for i, f in enumerate(op): print(i, f.get('result'), '|', f.get('origin'), '|', str(f.get('item'))[:120])
        print(f'{len(op)} open; clean={A.get("clean")}'); return
    if not (a.msg and a.msg.startswith('cmsg_') and a.quote and a.quote.strip()):
        sys.exit("REFUSED: --msg (Kilian's own message id, cmsg_...) and --quote (his words) are required")
    if not (a.all or a.items): sys.exit('REFUSED: name --items or --all')
    pick = set(range(len(op))) if a.all else {int(x) for x in a.items.split(',')}
    sys.path.insert(0, HERE); import small_flag as SF
    if a.msg == SF.STANDING_CARD or a.card == SF.STANDING_CARD:  # rule 26: the standing card covers only noted small flagged rows
        bad = [str(f.get('item'))[:100] for i, f in enumerate(op) if i in pick and not (f.get('result') in SF.FLAGGED and SF.covers(a.U, s, f.get('item')))]
        if bad: sys.exit('REFUSED: the standing rule-26 card covers only unsupported / trend_only rows with a valid small_flag_notes entry '
                         f'(python3 {HERE}/small_flag.py check {a.U} {s}); not covered: ' + '; '.join(bad))
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'); keep, moved = [], []
    for i, f in enumerate(op):
        if i in pick:
            f = dict(f, status='accepted_by_kilian', fix=(f.get('fix') or '') + f' | accepted by Kilian ({a.msg}{", card " + a.card if a.card else ""}): "{a.quote}"',
                     owner_override=dict(msg=a.msg, card=a.card, quote=a.quote, utc=now)); moved.append(f)
        else: keep.append(f)
    A.setdefault('accepted_carried', []).extend(moved); A['open_failures'] = keep
    if not keep: A['clean'] = True
    A['note'] = (A.get('note') or '') + f' | {now}: {len(moved)} open row(s) accepted by Kilian ({a.msg}).'
    json.dump(A, open(p, 'w'), indent=1)
    with open(os.environ.get('VAL_OVERRIDES_LOG', os.path.join(HERE, 'data', 'rule_overrides.jsonl')), 'a') as fh:
        fh.write(json.dumps(dict(rule='citation-gate-owner-decision', round=a.U, symbol=s, items=[m.get('item') for m in moved], msg=a.msg, card=a.card, quote=a.quote, utc=now)) + '\n')
    print(json.dumps(dict(symbol=s, round=a.U, accepted=len(moved), open=len(keep), clean=A.get('clean'))))


if __name__ == '__main__': main()
