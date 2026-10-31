"""Write memo briefs (agents/memo.md) for each stock the book buys on market date D (default; or the SYMs given): runs/D/SYM/brief_memo.md -> the memo agent writes
runs/D/SYM/memo.json. Research comes from the newest finished research round (combine.research_date).
Usage: python3 prep_memos.py D [SYM ...]"""
import glob, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from combine import research_date
import json
D = sys.argv[1]
# default: only the stocks the book buys (Kilian 2026-09-29: memos for the names in the portfolio, not the whole list)
syms = [s.upper() for s in sys.argv[2:]] or sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(f'{HERE}/runs/{D}/*/allocation.json')
                                                   if json.load(open(p)).get('shares') and os.path.exists(os.path.join(os.path.dirname(p), 'result.json')))
if len(sys.argv) <= 2:  # Kilian 2026-10-06 "autosyncs to the live book": a held name with no memo yet gets one too
    import book
    syms += [p['symbol'] for p in book.load()['positions'] if p['symbol'] not in syms and not glob.glob(f"{HERE}/runs/*/{p['symbol']}/memo.json")]
t = open(f'{HERE}/agents/memo.md').read().split('\n---\n', 1)[1]
for s in syms:
    rd = f'runs/{D}/{s}'; kv = dict(SYMBOL=s, AS_OF=D, RESEARCH_DIR=f'runs/{research_date(D, s)}/{s}', RESULT_PATH=f'{rd}/result.json',
                                    ALLOC_PATH=f'{rd}/allocation.json' if os.path.exists(f'{HERE}/{rd}/allocation.json') else f'{rd}/allocation_at_buy.json', OUT_PATH=f'/mnt/project-files/valuation/{rd}/memo.json')
    b = t
    for k, v in kv.items(): b = b.replace('{{' + k + '}}', v)
    open(f'{HERE}/{rd}/brief_memo.md', 'w').write(b); print(s, 'brief written, research', kv['RESEARCH_DIR'])
