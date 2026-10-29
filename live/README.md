# Live refresh for Price Targets

Fills, live quotes and the page's numbers update here, in GitHub Actions, with no Claude session.

- **Inputs.** Branch `live-data` holds the files the review and page build read (`live/slice.py`, pushed by
  `live/nightly.sh push` after each nightly publish). The `fills` tab of the "Price Targets live" Google Sheet holds
  fills entered on the page.
- **Run** (`.github/workflows/live.yml`), started every 30 minutes by an Apps Script time trigger in the Sheet
  (`live/apps_script.gs`, which holds a fine-grained token with Actions read and write on this repo). The gate job (`live/gate.py`) runs the full job only while the New York market
  is open or a fill on the Sheet is waiting. `live/run.py` turns waiting Sheet rows into `fills/<id>.json`, folds them
  into the slice's trades, refreshes quotes, re-marks held names that moved past their implied move on a fresh Cboe
  option chain (`live/remark.py`: one implied standard deviation, 30-day ATM vol x sqrt(sessions since the name's last
  market leg / 252); the pulled legs stay in `remark/` on `live-out` for the rest of the session), reruns `review.py` and `build_report.py`, then writes the difference from
  the published page's data (`live/patch.py`) to the Sheet's `live` tab and branch `live-out`, and sets each row's status.
- **Page.** Price Targets reads the `live` tab through the Google Sheets connector and applies the patch before it
  renders. A patch whose `base` does not match the page's own data is ignored, so a new publish never shows stale numbers.
- **Nightly.** `live/nightly.sh fold` adds `fills/` to `valuation/data/trades.json` before the re-mark;
  `live/nightly.sh push` re-slices after the publish and removes fill files that trades.json now holds.

Sheet row status: `live, in trades.json tonight` (in the live numbers, waiting for the nightly fold), `in trades.json`
(folded), `removed` (taken off on the page; the file is deleted), `rejected: why`.
