"""Slim copy of a name's Excel model for Google Sheets (Kilian 2026-09-30: Sheets for held names only).

Usage: python3 sheets_slim.py DATE SYM... -> runs/DATE/SYM/sheet_SYM.xlsx and prints its size.
Keeps Summary, Assumptions, Distribution, Methods and Scenarios (every formula and every assumption's source), drops the long
Reasoning / Supervisor / Sources tabs (they stay in the Excel download), strips cell styles and hyperlink objects so the file is
small enough to upload through the Drive connector (which takes the file inline), and adds a live price row
(GOOGLEFINANCE) under the model's last close. Upload with Drive create_file (base64, xlsx content type) into the 'Pitch memos'
folder's sibling 'Models' folder; record the link in data/model_gsheets.json.
"""
import os, sys, openpyxl
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP = ['Summary', 'Assumptions', 'Distribution', 'Methods', 'Scenarios']
date, syms = sys.argv[1], sys.argv[2:]
for s in syms:
    src = os.path.join(HERE, 'runs', date, s, f'model_{s}.xlsx')
    wb = openpyxl.load_workbook(src)
    for n in list(wb.sheetnames):
        if n not in KEEP: del wb[n]
    for ws in wb.worksheets:
        ws._hyperlinks = []
        for row in ws.iter_rows():
            for c in row: c.style = 'Normal'; c.hyperlink = None
    sm = wb['Summary']
    sm['C4'] = 'Live'; sm['C5'] = f'=GOOGLEFINANCE("{s}","price")'
    for r in range(38, 46):  # the old tab index lists tabs that are not in this copy
        for col in 'AD': sm[f'{col}{r}'] = None
    sm['A38'] = 'Sections below: Scenarios, Methods, Distribution, Assumptions. Blue inputs recalculate everything. Reasoning, supervisor notes and source links are in the Excel model on the Price Targets page.'
    out = os.path.join(HERE, 'runs', date, s, f'sheet_{s}.xlsx'); wb.save(out)
    print(s, out, os.path.getsize(out))
