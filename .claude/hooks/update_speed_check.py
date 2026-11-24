#!/usr/bin/env python3
"""PreToolUse hook: RULES.md 28 (Kilian 2026-11-20, after the VST non-op update: "implement all three for all work going
forward and make sure no future claudes can bypass this without telling me what they are doing").

1. XBRL FIRST. A fetch of an SEC filing document (www.sec.gov/Archives/..., the ix viewer, cgi-bin/viewer) by WebFetch,
   a browser tool, or curl/wget/urllib in Bash is refused unless that filing was registered with
   `python3 /mnt/project-files/valuation/secfacts.py untagged SYM URL --item "..."`. Registration runs the XBRL lookup on the
   item first; a tagged match needs --why and is then a logged bypass listed to Kilian. data.sec.gov (the XBRL API) and
   EDGAR full-text search are not filing documents and pass.
2. NO GOOGLE SHEETS OR EXCEL WRITES (Kilian 2026-11-24: "make sure ALL future sheet updates are pushed there [Claude Sheets]";
   then "i don't just want to block uploads i want to block you from writing to sheets or excel altogether"). Refused:
   every Google Sheets tool that writes (anything but get_values / get_spreadsheet), and Google Drive create_file /
   copy_file / update_file of a spreadsheet (Google Sheets or Excel/xlsx) or of anything titled "SYM driver model (D)".
   Reads still pass. Driver-model Sheets go to Claude Sheets: `name_update.py sheet D SYM` writes the brief
   (agents/claude_sheet_upload.md).
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


GS_READ = ('mcp__Google_Sheets__get_values', 'mcp__Google_Sheets__get_spreadsheet')
DRIVE_WRITE = ('mcp__Google_Drive__create_file', 'mcp__Google_Drive__copy_file', 'mcp__Google_Drive__update_file')
SHEETY = re.compile(r'spreadsheet|\.xlsx?\b|\.xlsm\b|ms-excel|driver model', re.I)


def sheet_check(tool, inp):
    gs_write = tool.startswith('mcp__Google_Sheets__') and tool not in GS_READ
    if gs_write or (tool in DRIVE_WRITE and SHEETY.search(json.dumps(inp))):
        return ('SHEET WRITE REFUSED (RULES.md 28b, Kilian 2026-11-24: no writing to Google Sheets or Excel at all; Sheets live '
                f'only in Claude Sheets). {tool} would write a spreadsheet outside Claude Sheets. For a driver model run '
                f'`python3 {VAL}/name_update.py sheet D SYM` and follow runs/D/SYM/gsheet_upload_brief.md (Claude Sheets import), '
                f'then `name_update.py link D SYM <claude.ai/artifact URL>`. Reading a Google Sheet (get_values) still works.')
    return None


def main():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    tool, inp = ev.get('tool_name') or '', ev.get('tool_input') or {}
    if tool in DRIVE_WRITE or tool.startswith('mcp__Google_Sheets__'): r = sheet_check(tool, inp)
    else: r = sec_check(tool, inp)
    if r: deny(r)


if __name__ == '__main__':
    try: main()
    except Exception: pass  # a broken check never blocks unrelated work
