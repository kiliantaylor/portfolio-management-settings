"""Names out of bounds for research, updates and buys (moved from project memory into code, Kilian 2026-11-16: "put this and
anything else in memory in code if you can to save space"). The rules:
  - NO DEFENSE NAMES (Kilian 09-30, 10-07, 11-06): defense primes and contractors/suppliers whose main customer is the
    military. Overrides any defense idea (e.g. distinct-bets-1106). Hand list = ideas/screen.py NOT_MANDATE_SYMS + DEFENSE.
  - FINANCIALS OUT (Kilian 11-09): GICS Financials (data/sectors.json) plus credit scores/bureaus/ratings/payments by hand
    (FICO, SPGI, MCO, EFX, TRU, V, MA, ...), whatever their GICS sector.
  - REMOVED NAMES (review_settings.json removed_names): cut for good, never revisited (e.g. KRKNF, the 11-10 one-off
    defense exception that failed the 4% bar; the 10-22 sector cut; defense removals 10-07).
Used by name_update.py start (refuses without --why, logged) and rules_check.py (no due buy, ever). Held names are never
forced out by this file.

    python3 universe.py SYM [SYM ...]     prints the reason each is out of bounds, or 'ok'"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
DEFENSE = {'RTX', 'GD', 'HII', 'LHX', 'NOC', 'LMT', 'LDOS', 'CW', 'BWXT', 'MRCY', 'AVAV', 'KTOS', 'BAH', 'CACI',
           'SAIC', 'KRMN', 'KRKNF', 'ESLT', 'BAESY', 'RNMBY', 'DRS'}
FIN_HAND = {'FICO', 'SPGI', 'MCO', 'EFX', 'TRU', 'EXPGY', 'V', 'MA', 'AXP', 'PYPL', 'FI', 'FIS', 'GPN', 'MSCI', 'ICE',
            'CME', 'NDAQ', 'COIN', 'HOOD', 'XYZ', 'SQ', 'AFRM', 'TOST', 'FOUR', 'WEX', 'BR', 'FDS', 'MKTX', 'CBOE'}


def _j(p, d):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def reason(s):
    s = s.upper()
    try:
        sys.path.insert(0, os.path.join(HERE, 'ideas')); from screen import NOT_MANDATE_SYMS as NM
    except Exception: NM = set()
    if s in DEFENSE or s in NM: return f'{s}: defense name (Kilian: NO DEFENSE NAMES)'
    if s in FIN_HAND or _j(os.path.join(HERE, 'data', 'sectors.json'), {}).get(s) == 'Financials':
        return f'{s}: financials are out of scope (Kilian 11-09: credit scores/bureaus/ratings/payments too)'
    if s in set(_j(os.path.join(HERE, 'data', 'review_settings.json'), {}).get('removed_names') or []):
        return f'{s}: in removed_names (cut for good; never revisit)'


if __name__ == '__main__':
    for s in sys.argv[1:] or [__doc__]: print(reason(s) or f'{s}: ok') if sys.argv[1:] else print(s)
