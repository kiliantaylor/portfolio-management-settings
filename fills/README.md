One JSON file per fill entered on the Price Targets page (the fills tab of the "Price Targets live" Google Sheet),
named by fill id and written by the live workflow:
{"symbol": "T", "side": "buy", "shares": 1, "price": 20.94, "date": "2026-10-27", "note": "", "entered_at": "2026-10-27T17:10:10Z"}
The live workflow applies them on top of the nightly trades.json; `live/nightly.sh fold` folds them into the project's
trades.json and `live/nightly.sh push` removes the folded files.
