"""Morgan Stanley report digests (Kilian 2026-10-03; see broker/README.md).

Digests are valuation/broker/digests/<drive_file_id>.json, written by agents/broker_digest.md from PDFs Kilian saves to
the Drive folder 'Morgan Stanley Research' (id below). Only digests are kept here, never the report text.

  python3 broker_digests.py pending ID [ID ...]   ids from a Drive listing that have no digest yet
  python3 broker_digests.py brief ID TITLE MODIFIED   prints the filled digest brief for one report
  python3 broker_digests.py show SYM [DATE]       the brief section the industry lane and supervisor get for SYM
  python3 broker_digests.py check                 schema check of every digest
prep_briefs.py calls section(sym, as_of, role) with role 'industry' or 'supervisor'; other lanes get nothing, so the
five forecasters stay independent and the sell-side view enters through one lane only."""
import datetime as dt, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, 'broker', 'digests')
DRIVE_FOLDER = '1nsoYkKWybXadyo57yCx6Jtdv7TXzK6k2'  # 'Morgan Stanley Research' in Kilian's My Drive
MAX_AGE_DAYS = 180  # older reports are dropped; the analyst-consensus freshness half-life is 120 days
REQUIRED = ('file_id', 'report_date', 'symbols', 'summary', 'estimates', 'proprietary_data', 'peer_comps',
            'company_facts_to_verify')


def load_all():
    out = []
    for p in sorted(glob.glob(os.path.join(DIR, '*.json'))):
        try: out.append(json.load(open(p)))
        except (OSError, ValueError) as e: print(f'skip {p}: {e}', file=sys.stderr)
    return out


def for_symbol(sym, as_of=None, days=MAX_AGE_DAYS):
    """Digests that are mainly about sym or give figures for it, newest first, within `days` of as_of."""
    sym = sym.upper(); as_of = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
    hits = []
    for d in load_all():
        try: rd = dt.date.fromisoformat(d.get('report_date', ''))
        except ValueError: continue
        if not (0 <= (as_of - rd).days <= days): continue  # never hand agents a report dated after the run
        main = sym in [s.upper() for s in d.get('symbols', [])]
        if main or sym in [s.upper() for s in d.get('mentioned', [])]: hits.append((rd, main, d))
    return [(main, d) for rd, main, d in sorted(hits, key=lambda x: x[0], reverse=True)]


def _rows(items, keys, sym=None, limit=25):
    if sym: items = [x for x in items if not x.get('symbol') or x['symbol'].upper() == sym]
    return ['  - ' + ', '.join(f'{k}: {x[k]}' for k in keys if x.get(k) not in (None, '')) for x in items[:limit]]


def section(sym, as_of=None, role='industry'):
    """Markdown block for a brief, or '' when there is nothing for sym."""
    if role not in ('industry', 'supervisor'): return ''
    hits = for_symbol(sym, as_of)
    if not hits: return ''
    sym = sym.upper()
    L = ['', '## Morgan Stanley research (sell-side, from Kilian\'s subscription)',
         'Digests of recent Morgan Stanley reports that cover this name. Treat them as one secondary source of type '
         '`sell-side`, cited as "MS <report date>, p.<page>":',
         '- Use the proprietary data (surveys, channel checks, trackers) and the scenario drivers as evidence, and say how '
         'much weight you gave them.',
         '- Company figures MS quotes are not facts until you check them against the filing or release on EDGAR.',
         '- MS\'s rating and price target are already in the analyst consensus. Do not anchor on them or count them again.',
         '- Peer comp tables are a starting list, not the answer: the peer-comps rule (METHOD.md) still applies, and '
         'the supervisor spot-checks at least three multiples against an independent source.']
    if role == 'supervisor':
        L.append('- Only the industry-lane forecaster saw these digests. Check whether it leaned on them more than the '
                 'evidence supports, and use the comp tables when you check the peer sets.')
    for main, d in hits[:6]:
        L += ['', f"### {d.get('report_title') or d.get('drive_title')} ({d.get('report_date')}, {d.get('report_type', '?')}, "
                  f"{'about ' + sym if main else 'mentions ' + sym})",
              f"File: valuation/broker/digests/{d['file_id']}.json" + ('' if d.get('text_complete', True) else
                                                                       ' (text was incomplete; see notes)'),
              d.get('summary', '')]
        for title, items, keys in (
                ('Estimates', d.get('estimates', []), ('metric', 'period', 'ms', 'consensus', 'unit', 'page')),
                ('Scenarios', d.get('scenarios', []), ('case', 'value', 'probability', 'drivers', 'page')),
                ('Proprietary data', d.get('proprietary_data', []), ('what', 'finding', 'sample_or_period', 'date', 'page')),
                ('Company figures to verify', d.get('company_facts_to_verify', []), ('claim', 'value', 'period', 'verify_in', 'page')),
                ('Catalysts', d.get('catalysts', []), ('date', 'event', 'page'))):
            r = _rows(items, keys, sym if title in ('Estimates', 'Scenarios', 'Company figures to verify') else None)
            if r: L += [f'- {title}:'] + r
        pc = d.get('peer_comps') or {}
        if pc.get('rows'):
            L += [f"- Peer comps (priced {pc.get('priced_as_of') or '?'}):"] + _rows(pc['rows'], ('symbol', 'metric', 'period', 'value', 'growth', 'page'), limit=20)
    return '\n'.join(L) + '\n'


def fill_brief(file_id, title, modified):
    t = open(os.path.join(HERE, 'agents', 'broker_digest.md')).read().split('\n---\n', 1)[1].strip() + '\n'
    for k, v in dict(FILE_ID=file_id, TITLE=title, MODIFIED=modified,
                     OUT_PATH=os.path.join(DIR, f'{file_id}.json')).items():
        t = t.replace('{{' + k + '}}', v)
    return t


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'pending':
        done = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(DIR, '*.json'))}
        print('\n'.join(i for i in a[1:] if i not in done))
    elif a[0] == 'brief':
        print(fill_brief(*a[1:4]))
    elif a[0] == 'show':
        print(section(a[1], a[2] if len(a) > 2 else None, 'supervisor') or f'no digests for {a[1].upper()}')
    elif a[0] == 'check':
        bad = 0
        for d in load_all():
            miss = [k for k in REQUIRED if k not in d]
            if miss: bad += 1; print(d.get('file_id', '?'), 'missing', miss)
        print(f'{len(load_all())} digests, {bad} with missing fields')
