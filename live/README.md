# Live refresh for Price Targets

Fills, live quotes and the page's numbers update here, in GitHub Actions, with no Claude session.

- **Inputs.** Branch `live-data` holds the files the review and page build read (`live/slice.py`, pushed by
  `live/nightly.sh push` after each nightly publish). The `fills` tab of the "Price Targets live" Google Sheet holds
  fills entered on the page.
- **Run** (`.github/workflows/live.yml`), started every 30 minutes by an Apps Script time trigger in the Sheet
  (`live/apps_script.gs`, which holds a fine-grained token with Actions read and write on this repo). The gate job (`live/gate.py`) runs the full job only while the New York market
  is open or a fill on the Sheet is waiting. `live/run.py` turns waiting Sheet rows into `fills/<id>.json`, folds them
  into the slice's trades, refreshes quotes, re-marks every researched name (held, or in `runs/D` with a driver model; about 24 names) on a fresh
  Cboe option chain every run (`live/remark.py`, Kilian 2026-11-05: market leg, page target, take-profit and stop; the
  research target (12m) does not move). One pull per name feeds the 12-month and 1-month legs, pulls are paced with one
  retry on a 429, and a chain counts only when it is quoted in the live price's session, so pre-market runs and stale
  chains never re-mark on the prior close (the name keeps an earlier leg from this session, if any). The pulled legs stay
  in `remark/` on `live-out` for the rest of the session; the nightly still writes the official priors on the official
  close. A full run with ~23 chains took about 40 seconds in a test on 2026-11-05 (15-minute job limit), reruns `review.py`, scores swap candidates (`live/swaps.py`, which calls `valuation/swap_test.py`, the same test the nightly runs at the close; the best winner is the trade in the page's next-share table; unheld names with gate-clean research at or below their Entry below, tested with `market_color/beats_check.py`'s whole-book swap; the result is the page's `live_swaps`), and `build_report.py`, then writes the difference from
  the published page's data (`live/patch.py`) to the Sheet's `live` tab and branch `live-out`, and sets each row's status.
- **Page.** Price Targets reads the `live` tab through the Google Sheets connector and applies the patch before it
  renders. A patch whose `base` does not match the page's own data is ignored, so a new publish never shows stale numbers.
- **Research intraday.** `live/nightly.sh research SYM [SYM ...]`, run when a model update finishes during the day
  (`name_update.py finish` prints it), adds that round to the published slice (`live/slice.py --research`: finished,
  gate-clean, model valuing the current set, not pending) and lists it in `slice.json` `research`. The push starts a live run
  at once; `live/remark.py` re-marks the name with `combine.py` research = that round on every run, so the new target and the
  held-name sizing reach the page before the 4:15pm ET close bar. The slice date, `base.json` and the other inputs are
  untouched (the page's patch key holds); a same-day republish keeps the list, the next nightly slice drops it.
- **Nightly.** `live/nightly.sh fold` adds `fills/` to `valuation/data/trades.json` before the re-mark;
  `live/nightly.sh push` re-slices after the publish and removes fill files that trades.json now holds.

Sheet row status: `live, in trades.json tonight` (in the live numbers, waiting for the nightly fold), `in trades.json`
(folded), `removed` (taken off on the page; the file is deleted), `rejected: why`.
