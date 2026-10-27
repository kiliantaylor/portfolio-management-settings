"""The "Price Targets live" Google Sheet, through a service account (key in the GOOGLE_SA_JSON secret).

Tab fills: one row per fill, written by the page (columns A-I) and given a status by the live run (J-K):
  A entered_at  B fill_id  C symbol  D side  E shares  F price  G date  H note  I fill JSON  J status  K status time
Tab live: the page patch (live/patch.py) as JSON text, cut into column A cells of at most CHUNK characters."""
import json, os
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

CHUNK, ROWS = 45000, 200  # a cell holds 50,000 characters; the live tab has 200 rows
DONE = ('in trades.json', 'removed')

class Sheet:
    def __init__(self, sheet_id, key_json):
        creds = service_account.Credentials.from_service_account_info(
            json.loads(key_json), scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self.s = AuthorizedSession(creds)
        self.url = f'https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values'

    def _get(self, rng):
        r = self.s.get(f'{self.url}/{rng}', params={'valueRenderOption': 'FORMATTED_VALUE'}); r.raise_for_status()
        return r.json().get('values', [])

    def _put(self, data):
        r = self.s.post(f'{self.url}:batchUpdate', json={'valueInputOption': 'RAW', 'data': data}); r.raise_for_status()

    def fills(self):
        """[(row number, fill id, fill doc, status)] for every row with an id and a parseable fill JSON."""
        out = []
        for i, row in enumerate(self._get('fills!A2:K'), start=2):
            row = row + [''] * (11 - len(row))
            try: doc = json.loads(row[8].lstrip("'"))
            except ValueError: continue
            if row[1]: out.append((i, row[1], doc, row[9]))
        return out

    def set_status(self, updates, now):
        """updates: {row number: status}."""
        if updates: self._put([{'range': f'fills!J{r}:K{r}', 'values': [[s, now]]} for r, s in sorted(updates.items())])

    def write_patch(self, patch):
        text = json.dumps(patch, separators=(',', ':'))
        parts = ['~' + text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]  # '~' keeps every cell plain text
        if len(parts) > ROWS: raise ValueError(f'patch of {len(text)} characters needs more than {ROWS} cells')
        self._put([{'range': f'live!A1:A{ROWS}', 'values': [[p] for p in parts] + [['']] * (ROWS - len(parts))}])
