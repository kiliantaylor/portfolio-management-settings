"""Nightly re-mark, mechanical part (REFRESH.md "Every weekday" steps 0-4), in one command (2026-10-19).

Usage (from anywhere):  python3 nightly.py [D] [--all] [--dry-run] [--force]
                        python3 nightly.py D --after-targets SYM [SYM ...]
                        python3 nightly.py D --retry-stale
  D            market date of the close being re-marked. Omitted: the market date of the latest close (Yahoo SPY bar).
               Given and different from the latest close's date (a market holiday): stops quietly.
  --all        re-mark every researched name (the default on Fridays); otherwise the active names (active_names.py)
  --force      re-mark even when every name already has runs/D/<SYM>/result.json (default: treat that as done/holiday)
  --dry-run    print the plan and exit
  --retry-stale  late pass (Cboe refreshes some names' delayed chains hours after the close): re-fetch chains for names
               with no runs/D/<SYM>/result.json, then combine + joint steps + pages for those now quoted from D; writes
               runs/D/nightly_summary_retry.json (still_stale lists the rest)
  --after-targets SYM...  after the model has updated data/analyst_targets.json (step 2): re-run combine/explain/model
               for those names, then the joint steps (conviction, allocate, score, review) and the pages for D.

Runs, in REFRESH order, each with cwd set explicitly (steps after combine once lost their cwd):
  0   daily_<SYM>.csv rows for the re-marked names and open limit orders (valuation/data; Yahoo daily bars, the same feed
      divs.py/live_quotes.py use; names it cannot fill are listed under skipped for Twelve Data)
  0b  check_limits.py                  1  market_dist.py SYM (one process per name, 6 at a time; a failure skips that name)
  2b  analyst_weights: fetch_history.py SYMS, fetch_current.py, build_current.py (Fridays: fetch_history.py and score.py first)
  2f  ../revisions_sleeve/live_scores.py on the month's last weekday and after any active name's print (Kilian 2026-10-22)
  2g  short_interest.py (FINRA days to cover; a cheap probe unless a new settlement is out; non-fatal)
  2h  stake_marks.py D (listed stakes in held names' sets at D's close, data/listed_stakes.json; revalues the set)
  2i  momentum.py D (12-1 momentum vs the S&P 500, data/momentum.json; bottom fifth gets combine.py's -3% tilt; non-fatal)
  3   combine.py D SYMS   3a explain.py D   3b divs.py SYMS, conviction.py D, 3b0 earnings_miss.py D, allocate.py D   3b1 score.py D
  3a0 scoring/std_model_log.py D, scoring/score_live.py, then scoring/scorecard.py D   3b2 review.py D   (3b3 book_track.py retired 2026-10-19)
  3c  model_xlsx.py D SYMS and copy the workbooks to models/
  4   build_report.py D (active names -> report.html); Fridays / --all also build_report.py D --archive (archive.html);
      sync_fills.py --check: if trades.json changed, sync_fills.py (review, page, dashboard), else sync_fills.py --stamp D
      (2c0 insider_flags.py D runs just before the sync, non-fatal) news_page.py D; Fridays calendar/build_calendar.py
One run at a time (2026-10-21): the nightly run, a --retry-stale pass and the --after-targets runs that research threads
start all rewrite the same joint files (forecast_log.csv, portfolio.json, the pages), so each takes data/nightly.lock
first and waits (up to 90 min, checking every 30s) while another run holds it; a lock older than 3 hours is treated as
left over from a crash and removed.
It does NOT run the model steps (2 analyst targets via WebFetch, 2c news agents, 2d Morgan Stanley via Drive, 2e capex,
5b re-research, publishing, messages). It ends with one JSON summary (also runs/D/nightly_summary.json) that lists what
the model still has to do; the full step log is runs/D/nightly_log.txt."""
import concurrent.futures as cf, datetime as dt, glob, json, os, shutil, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
PY = sys.executable or 'python3'
T0 = time.time()
UA = {'User-Agent': 'Mozilla/5.0'}
RES_MT, PAGE_MT = {}, {}  # file times before this run, to tell what it rewrote (no clock comparison with the network mount)
_mt = lambda p: os.path.getmtime(p) if os.path.exists(p) else None


def jload(p, default=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return default


class Run:
    def __init__(self, D, dry):
        self.D, self.dry, self.errors, self.skipped, self.steps = D, dry, [], [], []
        self.log_path = os.path.join(HERE, 'runs', D, 'nightly_log.txt')

    def log(self, text):
        if self.dry: return
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, 'a') as f: f.write(text if text.endswith('\n') else text + '\n')

    def sh(self, step, cmd, cwd=HERE, timeout=3600, fatal=False):
        """Run one command in cwd; log its output; record a failure. Returns (ok, stdout)."""
        line = f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%SZ}] {step}: (cd {os.path.relpath(cwd, HERE) if cwd != HERE else '.'}) {' '.join(cmd)}"
        print(line, file=sys.stderr, flush=True)
        if self.dry: self.steps.append(dict(step=step, cmd=' '.join(cmd), cwd=cwd)); return True, ''
        t = time.time()
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            ok, out, err = r.returncode == 0, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            ok, out, err = False, (e.stdout or b'').decode() if isinstance(e.stdout, bytes) else (e.stdout or ''), f'timed out after {timeout}s'
        self.steps.append(dict(step=step, seconds=round(time.time() - t, 1), ok=ok))
        self.log(f"{line}  ({time.time() - t:.1f}s, {'ok' if ok else 'FAILED'})\n{out[-20000:]}{err[-5000:]}")
        if not ok:
            self.errors.append(dict(step=step, cmd=' '.join(cmd), error=(err.strip() or out.strip())[-600:]))
            if fatal: raise SystemExit(f'{step} failed')
        return ok, out


def take_lock(run, what):
    """Hold data/nightly.lock for this run (runlock.py); waits while another run or a page publish holds it."""
    import runlock
    tok = runlock.take(what, D=run.D, wait_s=5400, stale_s=10800, log=run.log)
    return lambda: runlock.release(tok)


def latest_close():
    """Date of the latest finished daily bar (Yahoo SPY, regular market time in New York)."""
    u = 'https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d'
    m = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))['chart']['result'][0]['meta']
    return dt.datetime.fromtimestamp(m['regularMarketTime'] + m.get('gmtoffset', -14400), dt.timezone.utc).date().isoformat()


def yahoo_bars(sym, since):
    u = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d'
    r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))['chart']['result'][0]
    off = r['meta'].get('gmtoffset', -14400); q = r['indicators']['quote'][0]
    out = []
    for i, ts in enumerate(r.get('timestamp') or []):
        d = dt.datetime.fromtimestamp(ts + off, dt.timezone.utc).date().isoformat()
        row = [q[k][i] for k in ('open', 'high', 'low', 'close', 'volume')]
        if d > since and None not in row: out.append((d, row))
    return out


def step0(run, syms):
    """Append missing daily rows (after the file's last row, up to D) to valuation/data/daily_<SYM>.csv."""
    D, done, todo = run.D, [], []
    for s in syms:
        f = os.path.join(HERE, 'data', f'daily_{s}.csv')
        if not os.path.exists(f): continue  # names without a valuation/data file use portfolio/data (dashboard routine)
        last = open(f).read().rstrip('\n').rsplit('\n', 1)[-1].split(',')[0]
        if last < D: todo.append((s, f, last))
    if run.dry: return dict(to_update=[s for s, _, _ in todo])

    def one(a):
        s, f, last = a
        try: bars = [b for b in yahoo_bars(s, last) if b[0] <= D]
        except Exception as e: return s, None, f'Yahoo failed: {e}'
        if not bars or bars[-1][0] != D: return s, None, f'no {D} bar (last {bars[-1][0] if bars else last})'
        with open(f, 'a') as fh:
            for d, (o, h, l, c, v) in bars: fh.write(f'{d},{o:.5f},{h:.5f},{l:.5f},{c:.5f},{int(v)}\n')
        return s, len(bars), None
    with cf.ThreadPoolExecutor(4) as ex:
        for s, n, why in ex.map(one, todo):
            if why: run.skipped.append(dict(sym=s, step='0', reason=why + '; take the row from Twelve Data'))
            else: done.append(s)
    run.log(f'step 0: appended rows for {done}')
    return dict(updated=done)


def step1(run, syms):
    """market_dist.py per name (the CLI exactly as REFRESH step 1 runs it). A name whose chain is unusable or whose quotes
    are not from D keeps no prior for D and is skipped from the re-mark (unless a prior for D already exists)."""
    D, t = run.D, time.time()
    if run.dry: run.sh('1', ['python3', 'market_dist.py', '<each SYM>']); return syms
    def one(s):
        p = os.path.join(HERE, 'priors', D, f'{s}.json')
        before = os.path.getmtime(p) if os.path.exists(p) else None
        try: r = subprocess.run([PY, 'market_dist.py', s], cwd=HERE, capture_output=True, text=True, timeout=600); rc, out = r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired: rc, out = 1, 'timed out'
        after = os.path.getmtime(p) if os.path.exists(p) else None
        return s, rc, out, rc == 0 and after is not None and after != before, after is not None
    ok = []
    with cf.ThreadPoolExecutor(6) as ex:
        for s, rc, out, fresh, have in ex.map(one, syms):
            run.log(f'market_dist {s}: rc={rc} fresh={fresh}\n{out[-1500:]}')
            if fresh: ok.append(s); continue
            reason = out.splitlines()[-1][:200] if rc else 'quotes not from ' + D + ' (stale)'
            if have:
                ok.append(s); run.skipped.append(dict(sym=s, step='1', reason=f'{reason}; reused the prior already saved for {D}'))
            else: run.skipped.append(dict(sym=s, step='1', reason=f'{reason}; not re-marked'))
    run.steps.append(dict(step='1 market_dist', seconds=round(time.time() - t, 1), ok=True, n=len(ok)))
    return sorted(ok)


def prev_run_date(D):
    ds = sorted(d for d in os.listdir(os.path.join(HERE, 'runs')) if len(d) == 10 and d < D and os.path.exists(os.path.join(HERE, 'runs', d, 'portfolio.json')))
    return ds[-1] if ds else None


def review_changes(D, prev):
    """REFRESH 3b2: suggested trades that changed since the previous day, and names ahead of schedule."""
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, 'runs', D, '*', 'review.json'))):
        s = f.split(os.sep)[-2]; a = jload(f, {}); b = jload(os.path.join(HERE, 'runs', prev or '', s, 'review.json'), {}) if prev else {}
        ka, kb = (a.get('action'), a.get('trade_shares')), (b.get('action'), b.get('trade_shares'))
        if b and ka != kb: out.append(f"{s}: {kb[0]} {kb[1] or ''} -> {ka[0]} {ka[1] or ''}".replace('  ', ' '))
        if a.get('ahead_of_schedule'): out.append(f'{s}: ahead of schedule')
    return out


def joint_and_pages(run, syms, all_names, friday, summary):
    D = run.D
    # 3a explain.py D (every result for D), 3b dividends, conviction, joint allocation
    run.sh('3a explain', ['python3', 'explain.py', D])
    run.sh('3b divs', ['python3', 'divs.py'] + syms)
    run.sh('3b conviction', ['python3', 'conviction.py', D])
    run.sh('3b0 earnings_miss', ['python3', 'earnings_miss.py', D])  # big-miss add block; a failed fetch keeps the stored history
    run.sh('3b0x expected_miss', ['python3', 'expected_miss.py', D])  # Kilian 2026-10-30: adds wait before a print our model expects to miss by 5%+
    run.sh('3b allocate', ['python3', 'allocate.py', D], timeout=10800)  # 2026-10-21: rubric gate off, ~70 more names in the joint solve (48 min on 10-20)
    run.sh('3b1 score', ['python3', 'score.py', D])
    run.sh('3a0 std_model_log', ['python3', 'scoring/std_model_log.py', D])  # standard-model trial logger (refreshes rows until the next open)
    run.sh('3a0 score_live', ['python3', 'scoring/score_live.py'])
    sc = os.path.join(HERE, 'scoring', 'scorecard.py')
    summary['scorecard_cmd_ran'] = os.path.exists(sc) and run.sh('3a0 scorecard', ['python3', 'scoring/scorecard.py', D])[0]
    run.sh('3b2 review', ['python3', 'review.py', D])
    # 3b3 scoring/book_track.py retired 2026-10-19 (scorecard.py live_book uses the real fills)
    ok, _ = run.sh('3c model_xlsx', ['python3', 'model_xlsx.py', D] + syms)  # 4 workbooks at a time; unchanged inputs keep theirs (2026-10-21)
    if not run.dry:
        def cp(s):
            x = os.path.join(HERE, 'runs', D, s, f'model_{s}.xlsx')
            if not os.path.exists(x): return 0
            shutil.copy2(x, os.path.join(HERE, 'models')); return 1
        with cf.ThreadPoolExecutor(12) as ex: n = sum(ex.map(cp, syms))  # each copy is several round trips on the shared folder
        run.log(f'3c copied {n} workbooks to models/')
    # 4 pages
    run.sh('4 build_report', ['python3', 'build_report.py', D])
    if friday or all_names: run.sh('4 build_report --archive', ['python3', 'build_report.py', D, '--archive'])
    # 2c0 SEC Form 4 check on held names (display only; non-fatal). Before the dashboard build (sync_fills) and news_page.py,
    # which both read data/insider_flags.json; it exits cleanly without writing when no SEC contact is configured
    # 2c0 insider_flags retired 2026-10-19 15:21Z (Kilian: insider sales don't matter enough; insider buys showed no edge in
    # liquid names, insider_backtest/). insider_flags.py and data/insider_flags.json.retired-20261019 are kept.
    # run.sh('2c0 insider_flags', ['python3', 'insider_flags.py', D], timeout=900)
    ok, out = run.sh('4 sync_fills --check', ['python3', 'sync_fills.py', '--check'])
    stale = ok and '"stale": true' in out
    if stale: run.sh('4 sync_fills', ['python3', 'sync_fills.py'], timeout=3600)  # review + page + dashboard, writes the stamp
    else: run.sh('4 sync_fills --stamp', ['python3', 'sync_fills.py', '--stamp', D])
    summary['trades_changed_dashboard_synced'] = bool(stale)


def main():
    a = sys.argv[1:]
    dry, force, alln = '--dry-run' in a, '--force' in a, '--all' in a
    after = None
    if '--after-targets' in a:
        i = a.index('--after-targets'); after = [s.upper() for s in a[i + 1:] if not s.startswith('--')]; a = a[:i]
    retry = '--retry-stale' in a
    pos = [x for x in a if not x.startswith('--')]
    try: close = latest_close()
    except Exception as e: close = None; print('latest close unknown:', e, file=sys.stderr)
    D = pos[0] if pos else close
    if not D: raise SystemExit('no market date: pass D')
    friday = dt.date.fromisoformat(D).weekday() == 4
    summary = dict(D=D, mode=None, names_remarked=0, skipped=[], errors=[], held_news_needed=[], pages={}, scorecard_cmd_ran=False)
    if pos and close and close < D and not force:  # D given but no close for it yet: holiday (or run before the close)
        summary.update(mode='holiday', note=f'latest close is {close}, not {D}: nothing to re-mark'); print(json.dumps(summary, indent=1)); return
    run = Run(D, dry)
    release = (lambda: None) if dry else take_lock(run, ' '.join(sys.argv[1:])[:200])
    try: work(run, D, dry, force, alln, after, retry, friday, summary)
    finally: release()


def work(run, D, dry, force, alln, after, retry, friday, summary):
    run.log(f'\n===== nightly.py {" ".join(sys.argv[1:])} started {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%SZ}')
    sys.path.insert(0, HERE); import active_names as an, book
    cfg = jload(os.path.join(HERE, 'data', 'review_settings.json'), {})
    held = sorted(p['symbol'] for p in book.load().get('positions') or [] if p.get('shares'))
    prev = prev_run_date(D)
    for p in ('report.html', 'archive.html', 'news.html', 'calendar/calendar.html'): PAGE_MT[p] = _mt(os.path.join(HERE, p))

    if retry and after is None:  # Cboe refreshes each symbol's delayed chain at its own time, some hours after the close:
        # re-fetch the names that have no result for D yet and re-mark the ones whose quotes are now from D (2026-10-20)
        summary['mode'] = 'retry-stale'
        stale = [s for s in an.universe(D) if not os.path.exists(os.path.join(HERE, 'runs', D, s, 'result.json'))]
        summary['retried'] = stale
        fresh = step1(run, stale) if stale else []
        summary['still_stale'] = sorted(set(stale) - set(fresh))
        if not fresh:
            summary['steps'] = run.steps; summary['skipped'] = run.skipped
            if not dry: json.dump(summary, open(os.path.join(HERE, 'runs', D, 'nightly_summary_retry.json'), 'w'), indent=1)
            print(json.dumps(summary, indent=1)); return
        after = fresh
    if after is not None:  # step 2 updated some analyst targets: re-mark those names, then the joint steps and pages
        summary['mode'] = summary['mode'] or 'after-targets'
        RES_MT.update({s: _mt(os.path.join(HERE, 'runs', D, s, 'result.json')) for s in after})
        run.sh('3 combine', ['python3', 'combine.py', D] + after)
        joint_and_pages(run, after, alln, friday and False, summary)
        syms = after
    else:
        all_names = alln or friday
        univ = an.universe(D)
        act = an.active_names(D)
        syms = univ if all_names else act
        summary.update(mode='all' if all_names else 'active', universe=len(univ), active=len(act), archive=len(univ) - len(act))
        if not force and not dry and syms and all(os.path.exists(os.path.join(HERE, 'runs', D, s, 'result.json')) for s in syms):
            summary.update(mode='done', note=f'every name already has runs/{D}/<SYM>/result.json (holiday or already run); pass --force to re-mark')
            print(json.dumps(summary, indent=1)); return
        limit_syms = sorted((cfg.get('limit_orders') or {}).keys())
        summary['step0'] = step0(run, sorted(set(syms) | set(limit_syms)))
        ok, out = run.sh('0b check_limits', ['python3', 'check_limits.py'])
        summary['limit_orders'] = [l for l in out.splitlines() if l.strip()][-10:] if limit_syms else []
        syms = step1(run, syms)
        # 2 (model): analyst targets via WebFetch. 2b weighted consensus (mechanical):
        aw = os.path.join(HERE, 'analyst_weights')
        try: import yfinance  # noqa: F401
        except ImportError: run.sh('2b pip yfinance', ['python3', '-m', 'pip', 'install', '-q', 'yfinance'])
        if friday:
            run.sh('2b fetch_history (Friday, all)', ['python3', 'fetch_history.py'], cwd=aw)
            run.sh('2b score (Friday)', ['python3', 'score.py'], cwd=aw)
        run.sh('2b fetch_history', ['python3', 'fetch_history.py'] + syms, cwd=aw)
        run.sh('2b fetch_current', ['python3', 'fetch_current.py'], cwd=aw)
        run.sh('2b build_current', ['python3', 'build_current.py'], cwd=aw)
        nxt = dt.date.fromisoformat(D) + dt.timedelta(days=3 if friday else 1)
        # 2f month's last weekday, and (Kilian 2026-10-22 18:53Z "obviously refresh revision tilts after earnings") any night
        # an active name has reported on or after the newest snapshot's date (earnings_miss.json last_report_date)
        _snaps = sorted(glob.glob(os.path.join(HERE, '..', 'revisions_sleeve', 'snapshots', '*.csv')))
        _last = os.path.basename(_snaps[-1])[:10] if _snaps else ''
        try: _em = json.load(open(os.path.join(HERE, 'data', 'earnings_miss.json')))
        except Exception: _em = {}
        _printed = sorted(x for x in syms if isinstance(_em.get(x), dict) and (_em[x].get('last_report_date') or '') >= _last)
        if nxt.month != dt.date.fromisoformat(D).month or _printed:
            run.sh('2f live_scores' + (f" (prints since {_last}: {' '.join(_printed)})" if _printed else ''), ['python3', '../revisions_sleeve/live_scores.py'])
        run.sh('2g short_interest', ['python3', 'short_interest.py'], timeout=600)  # FINRA days to cover; refetches only on a new settlement
        run.sh('2h stake_marks', ['python3', 'stake_marks.py', D], timeout=900)  # listed stakes at D's close (Kilian 2026-10-30)
        run.sh('2i momentum', ['python3', 'momentum.py', D], timeout=900)  # 12-1 momentum vs the S&P 500 (Kilian 2026-10-30); non-fatal, a failure keeps the old file
        # 3 combine (one process: it rewrites forecast_log.csv for each name)
        RES_MT.update({s: _mt(os.path.join(HERE, 'runs', D, s, 'result.json')) for s in syms})
        ok, out = run.sh('3 combine', ['python3', 'combine.py', D] + syms, timeout=7200)
        for l in out.splitlines():
            if l.startswith('BLOCKED'): run.skipped.append(dict(sym=None, step='3', reason=l[:300]))
        joint_and_pages(run, syms, alln, friday, summary)
    # news page (2c's agents write news.json later; re-run news_page.py after them), Friday calendar
    run.sh('2c news_page', ['python3', 'news_page.py', D])
    if friday and after is None: run.sh('calendar', ['python3', 'calendar/build_calendar.py'])
    remarked = [s for s in syms if not dry and _mt(os.path.join(HERE, 'runs', D, s, 'result.json')) not in (None, RES_MT.get(s))]
    summary['names_remarked'] = len(remarked)
    missing_held = [s for s in held if not os.path.exists(os.path.join(HERE, 'runs', D, s, 'result.json'))]
    if missing_held: summary['held_missing_from_page'] = missing_held
    summary['held_news_needed'] = held
    summary['news_args'] = {s: dict(FROM=prev, RESEARCH_DATE=(jload(os.path.join(HERE, 'runs', D, s, 'result.json'), {}) or {}).get('research_as_of')) for s in held}
    for p in ('report.html', 'archive.html', 'news.html', 'calendar/calendar.html'):
        f = os.path.join(HERE, p)
        if _mt(f) not in (None, PAGE_MT.get(p)): summary['pages'][f] = os.path.getsize(f)
    summary['review_changes'] = review_changes(D, prev) if not dry else []
    cap = jload(os.path.join(HERE, 'data', 'ai_capex.json'), {}) or {}
    todo = [f"2 analyst targets: WebFetch stockanalysis.com forecast pages for the re-marked names (GPRO has none), update data/analyst_targets.json; if any changed: python3 nightly.py {D} --after-targets SYM...",
            f"2c news agents for {', '.join(held)} (agents/news.md, FROM={prev}; RESEARCH_DATE per news_args), verify material items, then python3 news_page.py {D} and publish Held-Name News",
            "2d Morgan Stanley: Drive search of the broker folder, broker_digests.py pending/brief/check",
            "4 publish report.html to Price Targets" + (" and archive.html to the archive page (set data/pages.json archive_url)" if (friday or alln) and after is None else "")
            + (", and the dashboard (trades.json changed)" if summary.get('trades_changed_dashboard_synced') else '')]
    if cap.get('refresh_after') and cap['refresh_after'] <= D: todo.append(f"2e AI capex refresh due (data/ai_capex.json refresh_after {cap['refresh_after']})")
    if summary['review_changes']: todo.append('3b2 one line in the thread: ' + '; '.join(summary['review_changes']))
    if any('assumed fill' in l.lower() or 'filled' in l.lower() for l in summary.get('limit_orders') or []): todo.append('0b a limit order filled: one line in the thread, ask Kilian to confirm the price')
    if after is None: todo.append('4h rebuild the Catalyst Calendar (python3 calendar/build_calendar.py D) and publish calendar/calendar.html to it')
    todo.append('5b if a held name\'s news is material: re-research it tonight (price-target-agents), then re-run combine..build and republish')
    summary['model_todo'] = todo
    summary['skipped'], summary['errors'] = run.skipped, run.errors
    summary['runtime_s'] = round(time.time() - T0, 1)
    summary['steps'] = run.steps
    if not dry: json.dump(summary, open(os.path.join(HERE, 'runs', D, 'nightly_summary_retry.json' if retry else 'nightly_summary.json'), 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
