"""Compare a Google Sheet export (xlsx) cell by cell with the payload values files that should have been written."""
import sys, json, base64, glob, re, os, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openpyxl
from gsheet_rounds import R
sym, dl = sys.argv[1], sys.argv[2]; D = R[sym]
gd = f'/mnt/project-files/valuation/runs/{D}/{sym}/gsheet'
raw = json.load(open(dl)); xb = base64.b64decode(raw['content'])
open(f"/tmp/{sym}_gsheet.xlsx", "wb").write(xb)
wb = openpyxl.load_workbook(io.BytesIO(xb))
tabs = {t['sheetId']: t['title'] for t in json.load(open(gd + '/tabs.json'))}
want = {}
for f in glob.glob(gd + '/values_*.json'):
    d = json.load(open(f)); m = re.match(r"'?(.*?)'?!([A-Z]+)(\d+)", d['range']); t, r0 = m.group(1), int(m.group(3))
    for i, row in enumerate(d['values']):
        for j, v in enumerate(row):
            if v != '': want[(t, r0 + i, j + 1)] = v
def _num(m):
    try: return '%.13g' % float(m.group(0))
    except ValueError: return m.group(0)
def norm(s):
    s = re.sub(r'\s+', '', str(s)).upper().replace("'", '').replace('$', '')
    s = re.sub(r'([A-Z]+\d+):\1(?![0-9])', r'\1', s)
    return re.sub(r'(?<![A-Z0-9_.])\d+\.?\d*(?:E[-+]?\d+)?', _num, s)
bad = []; n = 0
for (t, r, c), v in want.items():
    n += 1
    if t not in wb.sheetnames: bad.append((t, 'missing tab')); continue
    g = wb[t].cell(r, c).value
    g = getattr(g, 'text', g)
    if isinstance(v, str) and v.startswith('='):
        if isinstance(v, str) and v.upper().startswith('=DATE(') and g is not None and not isinstance(g, str): continue  # a date literal
        if isinstance(v, str) and v.upper().startswith('=HYPERLINK') and g is not None and not isinstance(g, str): continue
        if isinstance(g, str) and 'DUMMYFUNCTION' in g.upper() and 'GOOGLEFINANCE' in v.upper(): continue
        if not (isinstance(g, str) and norm(g) == norm(v)): bad.append((t, f'{openpyxl.utils.get_column_letter(c)}{r}', v[:80], str(g)[:80]))
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        if not (isinstance(g, (int, float)) and abs(g - v) <= 1e-9 * max(1, abs(v))): bad.append((t, f'{openpyxl.utils.get_column_letter(c)}{r}', v, g))
    else:
        vs = str(v)[1:] if str(v).startswith("'") else str(v)
        if g is None or (str(g).strip() != vs.strip() and norm(g) != norm(vs)):
            if isinstance(g, (int, float)) and re.fullmatch(r'[-\d.,%]+', vs):
                try:
                    if abs(float(vs.replace(',', '').rstrip('%')) / (100 if vs.endswith('%') else 1) - g) < 1e-9: continue
                except ValueError: pass
            if isinstance(v, bool) or str(v).upper() in ('TRUE', 'FALSE'):
                if str(g).upper() == str(v).upper(): continue
            bad.append((t, f'{openpyxl.utils.get_column_letter(c)}{r}', str(v)[:80], str(g)[:80]))
# cells in the Sheet the payload leaves blank
extra = []
wt = {(t, r, c) for (t, r, c) in want}
for ws in wb.worksheets:
    for row in ws.iter_rows():
        for cell in row:
            if cell.value not in (None, '') and (ws.title, cell.row, cell.column) not in wt: extra.append((ws.title, cell.coordinate, str(cell.value)[:60]))
print(f'{sym}: {n} cells checked, {len(bad)} differ, {len(extra)} extra non-blank')
for b in bad[:25]: print('  DIFF', b)
for e in extra[:10]: print('  EXTRA', e)
