"""Cheap first job of the live workflow: prints run=true when the full refresh is worth its minutes, i.e. the market is
open or the Sheet has fills the live run has not finished with (new, or applied live but not yet in trades.json)."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import market_open

def pending():
    if not (os.environ.get('SHEET_ID') and os.environ.get('GOOGLE_SA_JSON')): return False
    from sheet import Sheet, DONE
    rows = Sheet(os.environ['SHEET_ID'], os.environ['GOOGLE_SA_JSON']).fills()
    return any(s not in DONE and not s.startswith('rejected') for _, _, _, s in rows)

if __name__ == '__main__':
    go = os.environ.get('FORCE') == 'true' or market_open(dt.datetime.now(dt.timezone.utc)) or pending()
    print(f'run={str(go).lower()}')
