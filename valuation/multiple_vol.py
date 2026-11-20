"""Each name's own multiple variability, for the downside/upside width floor (Kilian 2026-11-01 10:53pm ET: "floor both
sides with flags for cases like $t"). Writes data/multiple_vol.json, which model_engine.py reads.

Variability = sd of the name's own log NTM forward P/E over the last 60 months (>= 36), from the WRDS CRSP + I/B/E/S
panel (wrds/code/blend_weights.build_panel, top 1000 by cap, point in time). Names with 12-35 months take the larger of
their own shorter sd and their GICS industry median (flagged). Names missing from the panel are added by hand in
MANUAL below with their industry median (flagged); a name in neither gets the panel median in the engine (flagged).

Floor table (wrds/results/exit_multiple_shrink/vol_k/error_spread_by_decile.csv, 2005-24 formations): the 12-month
exit-multiple miss vs the k = 0.8 forecast, P10 and P90 by monthly decile of this variability. Floor side vol =
-P10 / 1.2816 (downside) and P90 / 1.2816 (upside), linear between decile medians, flat beyond the ends.

Flags (the floor still applies): EVENTS below are names whose window holds a change in the earnings base (spin, large
merger) that likely overstates the variability; any name whose biggest one-month P/E move is above the panel's 95th
percentile gets an automatic flag.

Usage: BW_CACHE=<dir with room> python3 multiple_vol.py   (rebuilds the panel the first time, ~5 min; needs pyarrow)."""
import sys, os, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = f'{HERE}/data/multiple_vol.json'
FLOOR_CSV = '/mnt/project-files/wrds/results/exit_multiple_shrink/vol_k/error_spread_by_decile.csv'
EVENTS = {  # checked by hand 2026-11-01
    'T': "5y window holds the WarnerMedia spin (Apr 2022) and DirecTV split: earnings base changed, variability likely overstated",
    'VST': "5y window holds Winter Storm Uri losses (2021) and the Energy Harbor deal (Mar 2024): earnings base changed, variability likely overstated",
    'MOD': "the Oct 1 2026 spin changes the earnings base; panel history is pre-spin",
}
MANUAL = {  # not matched in the panel: GICS industry median
    'TT': dict(sd=0.185, months=0, source='GICS 201020 building products median (not matched in the WRDS panel)'),
    'CRDO': dict(sd=0.262, months=0, source='GICS 453010 semiconductors median (not in the panel: under the top 1000 until 2025)'),
}

def main():
    sys.path.insert(0, '/mnt/project-files/wrds/code'); import blend_weights as bw
    bw.CACHE = os.environ.get('BW_CACHE', bw.CACHE); bw.K1 = 2025 * 12 + 11
    os.makedirs(bw.CACHE, exist_ok=True)
    if not os.path.exists(f'{bw.CACHE}/panel.pkl'):
        bw.RES = bw.CACHE; import shutil; shutil.copy(os.path.dirname(FLOOR_CSV) + '/ust10y_monthly.csv', bw.CACHE)
    P = bw.build_panel(); P = P[P.k <= bw.K1].copy()
    P['m'] = P.adjp / P.ntm; P = P[np.isfinite(P.m) & (P.m > 0)].copy(); P['lm'] = np.log(P.m)
    km = int(P.k.max()); W = P[P.k > km - 60].sort_values(['PERMNO', 'k']).copy()
    W['d'] = W.groupby('PERMNO').lm.diff().abs()
    g = W.groupby('PERMNO').agg(sd=('lm', 'std'), months=('lm', 'size'), jump=('d', 'max'), GIND=('GIND', 'last'))
    full = g[g.months >= 36]; ind = full.groupby('GIND').sd.median(); j95 = float(full.jump.quantile(0.95))
    c = pd.read_parquet(bw.W + '/crsp.parquet', columns=['PERMNO', 'ym', 'Ticker'])
    g['tic'] = c.sort_values('ym').groupby('PERMNO').Ticker.last().reindex(g.index).values
    g = g[(g.months >= 12) & g.tic.notna()].sort_values('months')
    names = {}
    for p, r in g.iterrows():  # longest history wins a shared ticker (sorted ascending, later rows overwrite)
        fl = []
        if r.months >= 36: sd, src = r.sd, f'own 5y ({int(r.months)} months)'
        else:
            im = float(ind.get(r.GIND, np.nan)); sd = max(r.sd, im) if np.isfinite(im) else r.sd
            src = f'max(own {int(r.months)} months {r.sd:.3f}, GICS {r.GIND} median {im:.3f})'; fl.append(f'short history ({int(r.months)} months)')
        if r.jump > j95: fl.append(f'one-month P/E move of {r.jump:.0%} log in the window (above the panel 95th pct {j95:.0%}): variability may be overstated')
        names[r.tic] = dict(sd=round(float(sd), 4), months=int(r.months), source=src, flags=fl)
    if 'GOOG' in names and 'GOOGL' not in names: names['GOOGL'] = names['GOOG']
    for t, v in MANUAL.items(): names[t] = dict(v, flags=['no own multiple history in the panel: industry median'])
    for t, why in EVENTS.items(): names.setdefault(t, dict(sd=None, months=0, source='none', flags=[]))['flags'].append(why)
    F = pd.read_csv(FLOOR_CSV)
    out = dict(as_of_panel=f'{km // 12}-{km % 12 + 1:02d}', panel_median_sd=round(float(full.sd.median()), 4),
               floor_table=[dict(sd=float(r.sd_lev_med), miss_p10=float(r.err_P10), miss_p90=float(r.err_P90)) for r in F.itertuples()],
               names=names)
    json.dump(out, open(OUT, 'w'), indent=1); print('wrote', OUT, len(names), 'names; panel to', out['as_of_panel'])

if __name__ == '__main__': main()
