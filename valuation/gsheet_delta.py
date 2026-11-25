"""Model Sheet by copy + delta (Kilian 2026-11-20, after the VST Sheet upload took one worker ~156k tokens and ~100 calls:
"implement all three for all work going forward and make sure no future claudes can bypass this without telling me what
they are doing").

Route. `name_update.py sheet U SYM` exports the payloads as before (model_gsheet.py export -> runs/U/SYM/gsheet/), then
`plan` picks the route:
  delta  the name's newest linked Sheet (data/model_gsheets.json) has a snapshot of the payloads it was built from
         (runs/R/SYM/gsheet_linked/, written by `name_update.py link`) and the same tabs. The worker copies that Sheet with
         Google Drive copy_file (formatting carried) and pushes only what differs: grid growth, changed value ranges and
         changed format cells, as a few update_spreadsheet batches (runs/U/SYM/gsheet_delta/batch_N.json: grid growth,
         updateCells with typed values, repeatCell formats) plus update_values for any string Sheets would parse itself. Before writing the plan the script applies the delta to the
         snapshot and checks the result equals the new export cell for cell (values and formats); a mismatch falls back to full.
  full   no snapshot (Sheets linked before 2026-11-20), the tabs changed (layout), or the self-check failed: the worker
         builds a new Sheet from runs/U/SYM/gsheet/ as before.
Taking the full route while a delta is ready is a bypass: `name_update.py sheet U SYM --full --why "<reason>"` (logged,
rule "sheet-full-rebuild", listed to Kilian at finish). The settings hook (.claude/hooks/update_speed_check.py) refuses a
Drive create_file of "SYM driver model (U)" while runs/U/SYM/gsheet_route.json says delta.
The Sheet = engine check is unchanged: model_gsheet.py verify (LibreOffice vs model_engine.json) runs on every route, and
the worker reads back Summary B6 / B8 against the engine.

  python3 gsheet_delta.py plan U SYM          write gsheet_route.json (+ gsheet_delta/ on the delta route); print the route
  python3 gsheet_delta.py snapshot U SYM URL  copy runs/U/SYM/gsheet/ to gsheet_linked/ (name_update.py link calls this)
"""
import copy, datetime as dt, glob, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT') or os.path.join(HERE, 'runs')
LINKS = os.environ.get('VAL_GSHEET_LINKS') or os.path.join(HERE, 'data', 'model_gsheets.json')
FOLDER = '1GcnbYftwn7L8qTtGTYzOK-UHuApOubPI'
BLOCK_ROWS = 400  # rows per value block, as model_gsheet.export
GAP = 2           # unchanged rows a changed block may span (rewritten unchanged: fewer calls)


def J(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _now(): return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def col(n):  # 0-based -> A1 letters
    s = ''; n += 1
    while n: n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s


def sheet_id(url):
    m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', url or ''); return m.group(1) if m else None


# ---------------------------------------------------------------------------------------------------------------- payloads
def load(gd):
    """Payload folder -> dict(tabs {sid: title}, setup [...], cells {(title, r, c): v}, fmt {(sid, r, c): json}, other set)."""
    tabs = {t['sheetId']: t['title'] for t in J(os.path.join(gd, 'tabs.json'), [])}
    cells = {}
    for f in glob.glob(os.path.join(gd, 'values_*.json')):
        d = J(f); m = re.match(r"'?(.*?)'?!([A-Z]+)(\d+)$", d['range']); t, c0, r0 = m.group(1), m.group(2), int(m.group(3)) - 1
        c0 = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(c0))) - 1
        for i, row in enumerate(d['values']):
            for j, v in enumerate(row):
                if v != '' and v is not None: cells[(t, r0 + i, c0 + j)] = v
    fmt, other = {}, set()
    for f in sorted(glob.glob(os.path.join(gd, 'format_*.json')), key=lambda x: int(re.sub(r'\D', '', os.path.basename(x)) or 0)):
        for q in J(f, []):
            rc = q.get('repeatCell')
            if rc:
                g = rc['range']; fm = json.dumps(rc['cell'].get('userEnteredFormat') or {}, sort_keys=True)
                for r in range(g['startRowIndex'], g['endRowIndex']):
                    for c in range(g['startColumnIndex'], g['endColumnIndex']): fmt[(g['sheetId'], r, c)] = fm
            else: other.add(json.dumps(q, sort_keys=True))
    return dict(tabs=tabs, setup=J(os.path.join(gd, 'setup.json'), []), cells=cells, fmt=fmt, other=other)


def _grid(setup):
    """{sid: (rows, cols)} from setup.json."""
    out = {}
    for q in setup:
        p = (q.get('addSheet') or q.get('updateSheetProperties') or {}).get('properties') or {}
        gp = p.get('gridProperties') or {}
        if 'rowCount' in gp: out[p.get('sheetId', 0)] = (gp['rowCount'], gp.get('columnCount', 26))
    return out


# ---------------------------------------------------------------------------------------------------------------- delta
def _blocks(rows):
    """Sorted row numbers -> [(first, last)] runs, joining gaps of up to GAP rows, at most BLOCK_ROWS long."""
    out = []
    for r in rows:
        if out and r - out[-1][1] <= GAP + 1 and r - out[-1][0] < BLOCK_ROWS: out[-1][1] = r
        else: out.append([r, r])
    return out


def value_delta(old, new):
    """[{range, values}] that turn old cells into new cells (cleared cells written as '')."""
    ch = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    out = []
    for t in sorted({k[0] for k in ch}):
        mine = [k for k in ch if k[0] == t]
        for a, b in _blocks(sorted({k[1] for k in mine})):
            cs = [k[2] for k in mine if a <= k[1] <= b]; c0, c1 = min(cs), max(cs)
            vals = [[new.get((t, r, c), '') for c in range(c0, c1 + 1)] for r in range(a, b + 1)]
            out.append(dict(range=f"'{t}'!{col(c0)}{a + 1}", values=vals))
    return out


def format_delta(old, new):
    """repeatCell requests (fields userEnteredFormat: replaces the whole format, {} clears it) for cells whose format differs."""
    ch = {}
    for k in set(old) | set(new):
        if old.get(k) != new.get(k): ch[k] = new.get(k, '{}')
    reqs = []
    by = {}
    for (sid, r, c), fm in ch.items(): by.setdefault((sid, fm), {}).setdefault(r, []).append(c)
    for (sid, fm), rows in sorted(by.items()):
        spans = {}
        for r, cs in rows.items():
            cs.sort(); a = b = cs[0]
            for c in cs[1:] + [None]:
                if c is not None and c == b + 1: b = c; continue
                spans.setdefault((a, b), []).append(r)
                if c is not None: a = b = c
        for (a, b), rs in spans.items():
            rs.sort(); s0 = p0 = rs[0]
            for r in rs[1:] + [None]:
                if r is not None and r == p0 + 1: p0 = r; continue
                reqs.append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': s0, 'endRowIndex': p0 + 1, 'startColumnIndex': a, 'endColumnIndex': b + 1},
                                            'cell': {'userEnteredFormat': json.loads(fm)}, 'fields': 'userEnteredFormat'}})
                if r is not None: s0 = p0 = r
    return reqs


def other_delta(old, new):
    """Widths / frozen panes that are new or changed (a width the new export no longer sets goes back to 100 px)."""
    out = [json.loads(x) for x in sorted(new - old)]
    newkeys = {_okey(json.loads(x)) for x in new}
    for x in sorted(old - new):
        q = json.loads(x); k = _okey(q)
        if k in newkeys: continue
        if 'updateDimensionProperties' in q:
            q['updateDimensionProperties']['properties'] = {'pixelSize': 100}; out.append(q)
        elif 'updateSheetProperties' in q:
            p = q['updateSheetProperties']['properties']; p['gridProperties'] = {'frozenRowCount': 0, 'frozenColumnCount': 0}; out.append(q)
    return out


def _okey(q):
    if 'updateDimensionProperties' in q: return ('w', json.dumps(q['updateDimensionProperties']['range'], sort_keys=True))
    if 'updateSheetProperties' in q: return ('f', q['updateSheetProperties']['properties'].get('sheetId'))
    return ('x', json.dumps(q, sort_keys=True))


AMBIG = re.compile(r'^\s*(?:true|false|[-+]?[\d$€£(][\d\s/:.,%$€£()+eE-]*)\s*$', re.I)


def cell_data(v):
    """A payload value as Sheets CellData, as USER_ENTERED would store it; None for a string USER_ENTERED might parse."""
    if v == '' or v is None: return {}
    if isinstance(v, bool): return {'userEnteredValue': {'boolValue': v}}
    if isinstance(v, (int, float)): return {'userEnteredValue': {'numberValue': v}}
    s = str(v)
    if s.startswith('='): return {'userEnteredValue': {'formulaValue': s}}
    if s.startswith("'"): return {'userEnteredValue': {'stringValue': s[1:]}}
    if AMBIG.match(s): return None
    return {'userEnteredValue': {'stringValue': s}}


def cells_requests(vd, tabs):
    """Value blocks -> (updateCells requests, [{range, values}] left for update_values: strings Sheets might parse)."""
    sid = {t: i for i, t in tabs.items()}; reqs, rest = [], []
    for b in vd:
        m = re.match(r"'(.*)'!([A-Z]+)(\d+)$", b['range']); t, r0 = m.group(1), int(m.group(3)) - 1
        c0 = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(m.group(2)))) - 1
        rows = []
        for i, row in enumerate(b['values']):
            out = []
            for j, v in enumerate(row):
                cd = cell_data(v)
                if cd is None: rest.append(dict(range=f"'{t}'!{col(c0 + j)}{r0 + i + 1}", values=[[v]])); cd = {}
                out.append(cd)
            rows.append({'values': out})
        i0, n = 0, 0  # one request per ~50KB of rows, so a batch file stays readable in one call
        for i, rw in enumerate(rows + [None]):
            k = len(json.dumps(rw)) if rw else 0
            if rw is None or (n + k > 50000 and i > i0):
                reqs.append({'updateCells': {'start': {'sheetId': sid[t], 'rowIndex': r0 + i0, 'columnIndex': c0}, 'rows': rows[i0:i], 'fields': 'userEnteredValue'}})
                i0, n = i, 0
            n += k
    return reqs, rest


def chunks(reqs, max_bytes=60000):
    out, cur, n = [], [], 0
    for q in reqs:
        k = len(json.dumps(q, separators=(',', ':')))
        if cur and n + k > max_bytes: out.append(cur); cur, n = [], 0
        cur.append(q); n += k
    return out + ([cur] if cur else [])


def grow(old_setup, new_setup):
    og, ng = _grid(old_setup), _grid(new_setup); out = []
    for sid, (r, c) in sorted(ng.items()):
        r0, c0 = og.get(sid, (0, 0))
        if r > r0 or c > c0:
            out.append({'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'rowCount': max(r, r0), 'columnCount': max(c, c0)}},
                                                  'fields': 'gridProperties.rowCount,gridProperties.columnCount'}})
    return out


def apply(base, vd, fd):
    """Self-check: the snapshot with the delta applied (as Sheets would) -> (cells, fmt)."""
    cells, fmt = dict(base['cells']), dict(base['fmt'])
    for b in vd:
        m = re.match(r"'(.*)'!([A-Z]+)(\d+)$", b['range']); t, r0 = m.group(1), int(m.group(3)) - 1
        c0 = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(m.group(2)))) - 1
        for i, row in enumerate(b['values']):
            for j, v in enumerate(row):
                k = (t, r0 + i, c0 + j)
                if v == '': cells.pop(k, None)
                else: cells[k] = v
    for q in fd:
        g = q['repeatCell']['range']; fm = json.dumps(q['repeatCell']['cell']['userEnteredFormat'], sort_keys=True)
        for r in range(g['startRowIndex'], g['endRowIndex']):
            for c in range(g['startColumnIndex'], g['endColumnIndex']):
                k = (g['sheetId'], r, c)
                if fm == '{}': fmt.pop(k, None)
                else: fmt[k] = fm
    return cells, fmt


# ---------------------------------------------------------------------------------------------------------------- route
def base_link(U, s):
    """Newest linked Sheet for s at or before round U whose payload snapshot exists -> (round, url) or (None, reason)."""
    g = J(LINKS, {}) or {}
    rs = sorted((r for r, v in g.items() if s in v and r <= U), reverse=True)
    if not rs: return None, 'no earlier linked Sheet for this name'
    for r in rs:
        if os.path.exists(os.path.join(RUNS, r, s, 'gsheet_linked', 'linked.json')): return (r, g[r][s]), ''
    return None, (f'the newest linked Sheet ({rs[0]}) has no payload snapshot (Sheets linked before 2026-11-20 were not snapshotted; '
                  'this full upload makes one at `link`, so the next update copies)')


def plan(U, s, force_full_why=''):
    rd = os.path.join(RUNS, U, s); gd = os.path.join(rd, 'gsheet'); dd = os.path.join(rd, 'gsheet_delta')
    title = f'{s} driver model ({U})'
    route = dict(symbol=s, round=U, title=title, utc=_now(), route='full', reason='', folder=FOLDER)
    b, why = base_link(U, s)
    if b:
        br, url = b; bd = os.path.join(RUNS, br, s, 'gsheet_linked')
        old, new = load(bd), load(gd)
        if old['tabs'] != new['tabs']: why = f'layout changed: tabs {list(old["tabs"].values())} -> {list(new["tabs"].values())}'
        else:
            vd = value_delta(old['cells'], new['cells']); fd = format_delta(old['fmt'], new['fmt'])
            od = other_delta(old['other'], new['other']); gr = grow(old['setup'], new['setup'])
            cells, fmt = apply(old, vd, fd)
            if cells != new['cells'] or fmt != new['fmt']: why = 'self-check failed: snapshot + delta != new export (full upload instead)'
            else:
                route.update(route='delta', base_round=br, base_url=url, base_id=sheet_id(url),
                             changed_value_blocks=len(vd), changed_cells=sum(1 for k in set(old['cells']) | set(new['cells']) if old['cells'].get(k) != new['cells'].get(k)),
                             format_requests=len(fd) + len(od), grid_requests=len(gr))
                cr, rest = cells_requests(vd, new['tabs'])
                batches = chunks(gr + cr + fd + od)
                route.update(batch_files=len(batches), value_files=len(rest))
                if os.path.isdir(dd): shutil.rmtree(dd)
                os.makedirs(dd)
                for i, x in enumerate(batches): json.dump(x, open(os.path.join(dd, f'batch_{i}.json'), 'w'), separators=(',', ':'), ensure_ascii=False)
                for i, x in enumerate(rest): json.dump(x, open(os.path.join(dd, f'values_{i}.json'), 'w'), separators=(',', ':'), ensure_ascii=False)
    if route['route'] == 'full':
        route['reason'] = why
        if os.path.isdir(dd): shutil.rmtree(dd)
    if force_full_why and route['route'] == 'delta':
        route.update(route='full', reason=f'BYPASS (delta was ready from {route["base_round"]}): {force_full_why}', bypass=True)
        if os.path.isdir(dd): shutil.rmtree(dd)
    json.dump(route, open(os.path.join(rd, 'gsheet_route.json'), 'w'), indent=1)
    return route


def snapshot(U, s, url):
    rd = os.path.join(RUNS, U, s); gd, sd = os.path.join(rd, 'gsheet'), os.path.join(rd, 'gsheet_linked')
    if not os.path.isdir(gd): return None
    if os.path.isdir(sd): shutil.rmtree(sd)
    os.makedirs(sd)
    for f in glob.glob(os.path.join(gd, '*.json')): shutil.copy(f, sd)
    rt = J(os.path.join(rd, 'gsheet_route.json'), {}) or {}
    json.dump(dict(url=url, id=sheet_id(url), utc=_now(), route=rt.get('route'), base_round=rt.get('base_round')), open(os.path.join(sd, 'linked.json'), 'w'), indent=1)
    return sd


def main(a):
    if len(a) >= 3 and a[0] == 'plan': print(json.dumps(plan(a[1], a[2].upper()), indent=1))
    elif len(a) >= 4 and a[0] == 'snapshot': print(snapshot(a[1], a[2].upper(), a[3]))
    else: print(__doc__); sys.exit(2)


if __name__ == '__main__': main(sys.argv[1:])
