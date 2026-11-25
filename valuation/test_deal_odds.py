"""Tests for deal_odds.py and the deal_events checks in model_engine.deal_mix (special situations playbook, 2026-11-16).
Run: python3 test_deal_odds.py"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deal_odds as DO


def test_mod_cash_odds_rule():  # playbook: MOD with B = U x 1.15 -> p 0.84, target 148.3 (card 2026-11-16)
    o = DO.build_event(dict(name='MOD test', type='T1', consideration='cash', price_per_share=145.9, undisturbed=107.1, spot_at_entry=142.30,
                            entry_date='2026-11-13', standalone=160.91))
    e = o['event']
    assert abs(e['break_price'] - 123.165) < 1e-9 and e['probability_source'] == 'market'
    assert abs(e['probability'] - 0.8417) < 1e-4
    assert abs(o['check']['target'] - 148.28) < 0.01


def test_stock_break_is_undisturbed():
    e = dict(name='s', type='T4', consideration='stock', ratio=4.5, acquirer='MRVL', acquirer_spot=164.45, undisturbed=600.0, spot_at_entry=624.65, entry_date='2026-11-12')
    assert DO.break_price(e) == 600.0 and abs(DO.deal_value_mkt(e) - 4.5 * 164.45) < 1e-9


def test_mixed_and_discount():
    e = dict(name='m', type='T4', consideration='mixed', cash_per_share=20.0, ratio=0.45, acquirer_spot=280.0, acquirer_target=300.0, undisturbed=107.1,
             spot_at_entry=135.0, entry_date='2026-11-13', expected_close='2028-03-31', discount_rate=0.04)
    dfn = 1.04 ** -((DO._date('2028-03-31') - DO._date('2026-11-13')).days / 365.25)
    assert abs(DO.deal_value_mkt(e) - (20 * dfn + 0.45 * 280)) < 1e-9
    cs = 20 * dfn / (20 * dfn + 126.0); assert abs(DO.break_price(e) - 107.1 * (1 + 0.15 * cs)) < 1e-9
    assert DO.df(e, '2028-04-30') == 1.0  # close before the horizon: no discount
    try: DO.df(dict(e, discount_rate=None), '2027-11-13'); assert False
    except ValueError: pass


def test_cap_exit():
    deal = dict(price_per_share=21.5, target=21.88, standalone=dict(median=22.99))
    assert DO.cap_exit(deal, 22.18, 0.04, 0.0) and not DO.cap_exit(deal, 20.0, 0.04, 0.0)
    assert not DO.cap_exit(dict(deal, standalone=dict(median=21.0)), 22.18, 0.04, 0.0)  # standalone below D: no cap case


def test_spread_test():
    ev = dict(type='T1', consideration='cash', price_per_share=25.0, undisturbed=19.0, break_price=21.85, spot_at_entry=22.18, entry_date='2026-11-13')
    deal = dict(ev, event=ev, probability=0.6, price_per_share=25.0, target=0.6 * 25 + 0.4 * 22.99, standalone=dict(median=22.99))
    t = DO.spread_test(deal, 22.18, 22.86, 0.5, 0.10, 1.11)
    assert t['block'] and abs(t['p_mkt'] - (22.18 - 21.85) / (25 - 21.85)) < 1e-4
    assert DO.spread_test(dict(deal, provisional=True), 22.18, 22.86, 0.5, 0.10)['block'].startswith('provisional')
    assert 'break' in DO.spread_test(deal, 21.0, 22.86, 0.5, 0.10)['block']
    assert DO.spread_test(deal, 22.18, 22.86, 0.5, 0.0, 1.11)['block'] is None  # clears a zero bar at market odds


def test_engine_checks():
    import model_engine as ME
    out = dict(base=dict(horizon='2027-11-11', horizon_value=160.91), median=160.91, mean=165.0, quantiles=dict(q10=100, q25=130, q75=190, q90=230),
               p10_p90=dict(p10=100, p90=230), P=lambda K: 1 - ME.N_(math.log(K / 160.91) / 0.3))
    e = DO.build_event(dict(name='t', type='T1', consideration='cash', price_per_share=145.9, undisturbed=107.1, spot_at_entry=142.30, entry_date='2026-11-13'))['event']
    d = ME.deal_mix([e], dict(out)); assert abs(d['target'] - (e['probability'] * 145.9 + (1 - e['probability']) * 160.91)) < 1e-9 and d['standalone_above_deal']
    for bad in (dict(probability=0.5), dict(break_price=110.0), dict(type='T7'), dict(consideration='swap')):
        try: ME.deal_mix([dict(e, **bad)], dict(out)); assert False, bad
        except ME.ModelError: pass
    legacy = dict(name='old', probability=0.907, price_per_share=145.9, deal_sd_log=0.03, probability_basis='x')  # 2026-11-13 form still runs
    assert abs(ME.deal_mix([legacy], dict(out))['target'] - (0.907 * 145.9 + 0.093 * 160.91)) < 1e-9


if __name__ == '__main__':
    n = 0
    for k, f in sorted(globals().items()):
        if k.startswith('test_') and callable(f): f(); n += 1
    print(f'{n} tests passed')
