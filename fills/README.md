One JSON file per fill entered on the Price Targets page, named by fill id:
{"symbol": "T", "side": "buy", "shares": 1, "price": 20.94, "date": "2026-10-27", "note": "", "entered_at": "2026-10-27T17:10:10Z"}
The live workflow applies them on top of the nightly trades.json; the nightly run folds them into the project's trades.json.
