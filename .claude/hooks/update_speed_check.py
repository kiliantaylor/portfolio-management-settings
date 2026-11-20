#!/usr/bin/env python3
"""PreToolUse hook: RULES.md 28 (Kilian 2026-11-20, after the VST non-op update: "implement all three for all work going
forward and make sure no future claudes can bypass this without telling me what they are doing").

1. XBRL FIRST. A fetch of an SEC filing document (www.sec.gov/Archives/..., the ix viewer, cgi-bin/viewer) by WebFetch,
   a browser tool, or curl/wget/urllib in Bash is refused unless that filing was registered with
   `python3 /mnt/project-files/valuation/secfacts.py untagged SYM URL --item "..."`. Registration runs the XBRL lookup on the
   item first; a tagged match needs --why and is then a logged bypass listed to Kilian. data.sec.gov (the XBRL API) and
   EDGAR full-text search are not filing documents and pass.
2. SHEET COPY ROUTE. Drive create_file of "SYM driver model (D)" is refused while runs/D/SYM/gsheet_route.json says the
   delta route (copy the last Sheet, push the changes); `name_update.py sheet D SYM --full --why "..."` sets the full route,
   logged and listed to Kilian.
Prints nothing for any other call.
"""
import json, os, re, sys

VAL = os.environ.get('SPEED_CHECK_VAL_DIR', '/mnt/project-files/valuation')
REG = os.path.join(VAL, 'data', 'sec_text_fetches.jsonl')
DOC = re.compile(r'(?:https?://)?(?:www\.)?sec\.gov/(?:Archives/edgar/data/|ix\?doc=/?Archives/edgar/data/|cgi-bin/viewer\?)[^\s"\'<>]*', re.I)
FETCHY = re.compile(r'\b(curl|wget|urlopen|urllib|requests|httpx)\b')
SHEET_TITLE = re.compile(r'^\s*([A-Z][A-Z0-9.\-]{0,9}) driver model \((\d{4}-\d{2}-\d{2})\)\s*$')


def filing_key(url):
    m = re.search(r'Archives/edgar/data/0*(\d+)/(\d{10}-?\d{2}-?\d{6})', url)
    if m: return f"{m.group(1)}/{m.group(2).replace('-', '')}"
    a = re.search(r'accession_number=(\d{10}-?\d{2}-?\d{6})', url, re.I); c = re.search(r'cik=0*(\d+)', url, re.I)
    if a and c: return f"{c.group(1)}/{a.group(1).replace('-', '')}"
    return None


def registered():
    out = set()
    try:
        for l in open(REG):
            try: k = json.loads(l).get('filing')
            except ValueError: continue
            if k: out.add(k)
    except OSError: pass
    return out


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': reason}}))


def sec_check(tool, inp):
    if tool == 'Bash':
        cmd = str(inp.get('command') or '')
        if not FETCHY.search(cmd) or re.search(r'secfacts\.py\s+(untagged|lookup)', cmd): return None
        text = cmd
    else: text = json.dumps(inp)
    urls = DOC.findall(text)
    if not urls: return None
    reg = registered()
    bad = [u for u in urls if filing_key(u) not in reg]
    if not bad: return None
    u = bad[0]
    return ('FILING FETCH REFUSED (RULES.md 28c, Kilian 2026-11-20: XBRL first, no bypass without telling him). '
            f'{u[:160]} is a filing document. Take figures from the fact pack or '
            f'`python3 {VAL}/secfacts.py lookup SYM <words> [--period YYYY-MM-DD]`. If you need text the tags do not carry, '
            f'register it first: `python3 {VAL}/secfacts.py untagged SYM "{u[:200]}" --item "<what you need>" [--update U]`; '
            'if the lookup finds a tagged figure that registration needs --why and is listed to Kilian as a bypass.')


def sheet_check(inp):
    t = str(inp.get('title') or inp.get('name') or '')
    m = SHEET_TITLE.match(t)
    if not m: return None
    mt = json.dumps(inp).lower()
    if 'spreadsheet' not in mt: return None
    s, d = m.group(1), m.group(2)
    try: rt = json.load(open(os.path.join(VAL, 'runs', d, s, 'gsheet_route.json')))
    except (OSError, ValueError): return None
    if rt.get('route') != 'delta': return None
    return ('SHEET CREATE REFUSED (RULES.md 28b, Kilian 2026-11-20). The delta route is set for this Sheet: Google Drive '
            f"copy_file of {rt.get('base_url') or rt.get('base_id')} titled \"{t}\", then the batch files in "
            f'{VAL}/runs/{d}/{s}/gsheet_delta/ (brief: runs/{d}/{s}/gsheet_upload_brief.md). A full build while the delta is ready '
            f'needs `python3 {VAL}/name_update.py sheet {d} {s} --full --why "<reason>"` first (logged, listed to Kilian).')


def main():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    tool, inp = ev.get('tool_name') or '', ev.get('tool_input') or {}
    if tool == 'mcp__Google_Drive__create_file': r = sheet_check(inp)
    else: r = sec_check(tool, inp)
    if r: deny(r)


if __name__ == '__main__':
    try: main()
    except Exception: pass  # a broken check never blocks unrelated work
