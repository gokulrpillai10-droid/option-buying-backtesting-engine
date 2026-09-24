"""The shared GO/NO-GO edge battery (analysis/evaluate.py).

Duck-typed on trades: each trade needs `.net` (float) and `.date` (datetime.date).
A Result is anything with `.trades`. Tests build synthetic results so the verdict
logic is pinned independently of the engine.
"""
from datetime import date
from types import SimpleNamespace

from analysis.evaluate import (
    evaluate, Verdict, _sortino, _var_cvar, _max_drawdown_info, _quantile,
)


def _tr(net, d, reason="eod"):
    return SimpleNamespace(net=float(net), date=d, entry=100.0, qty=50, reason=reason)


def _result(trades):
    return SimpleNamespace(trades=list(trades))


def _spread_days(vals, year=2022):
    """One trade per distinct day across a year (months cycle), for split-half/year."""
    out = []
    for i, v in enumerate(vals):
        out.append(_tr(v, date(year + i % 4, 1 + (i % 12), 1 + (i % 27))))
    return out


def test_robust_winner_is_GO():
    # consistent positive edge in EVERY year, many trades, no single tail carry
    trades = []
    for yr in (2020, 2021, 2022, 2023):
        for j, v in enumerate([300, -100, 250, -80, 400, -120, 350, -90] * 2):
            trades.append(_tr(v, date(yr, 1 + j % 12, 1 + j % 27)))
    v = evaluate(_result(trades), name="robust")
    assert isinstance(v, Verdict)
    assert v.verdict == "GO", v.reasons
    assert v.metrics["net"] > 0 and v.metrics["profit_factor"] >= 1.2


def test_net_negative_is_NO_GO():
    trades = _spread_days([-300, 100, -250, 80, -400, 120] * 8)
    v = evaluate(_result(trades), name="loser")
    assert v.verdict == "NO-GO"
    assert any("net" in r.lower() or "profit" in r.lower() for r in v.reasons)


def test_tail_dependent_positive_is_MARGINAL():
    # net positive ONLY because of one giant winner; drop top-5 -> negative
    trades = _spread_days([-50] * 40 + [5000, 4000, 3000, 2500, 2000])
    v = evaluate(_result(trades), name="tail")
    assert v.metrics["net"] > 0
    assert v.verdict == "MARGINAL"
    assert any("tail" in r.lower() for r in v.reasons)


def test_split_half_divergent_is_not_GO():
    # early years strongly positive, late years negative -> fails hold-out
    early = [_tr(300, date(2020, 1 + i % 12, 1 + i % 27)) for i in range(30)]
    late = [_tr(-280, date(2024, 1 + i % 12, 1 + i % 27)) for i in range(30)]
    v = evaluate(_result(early + late), name="regime")
    assert v.verdict in ("MARGINAL", "NO-GO")
    assert any("split" in r.lower() or "half" in r.lower() for r in v.reasons)


def test_report_is_printable_and_names_verdict():
    v = evaluate(_result(_spread_days([200, -80] * 20)), name="demo")
    report = v.report()
    assert "demo" in report and v.verdict in report
    assert "t-stat" in report.lower() or "t=" in report.lower()


def test_empty_result_is_NO_GO_not_crash():
    v = evaluate(_result([]), name="empty")
    assert v.verdict == "NO-GO"


# ── Track 4: new metric helpers ──────────────────────────────────────────────

def test_sortino_positive_for_positive_edge():
    # consistent winners, few losers -> Sortino > Sharpe
    daily = [200.0, -50.0, 300.0, -80.0, 250.0] * 20
    s = _sortino(daily, years=2.0)
    assert s > 0


def test_sortino_zero_when_no_losers():
    # no negative days -> denominator is 0 -> returns 0 (no blow-up)
    daily = [100.0, 200.0, 150.0]
    assert _sortino(daily, years=1.0) == 0.0


def test_quantile_bounds():
    sd = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _quantile(sd, 0.0) == 1.0
    assert _quantile(sd, 1.0) == 5.0
    assert _quantile(sd, 0.5) == 3.0


def test_var_cvar_95_is_worse_than_mean():
    # VaR 95% day should be ≤ mean (a loss day, not the average)
    daily = [100.0, -200.0, 50.0, -150.0, 300.0, -80.0, 200.0, -60.0,
             150.0, 120.0, -90.0, 80.0, -40.0, 250.0, 110.0, -30.0,
             130.0, -20.0, 70.0, 90.0]
    var95, cvar95 = _var_cvar(daily, 0.95)
    mean = sum(daily) / len(daily)
    assert var95 < mean                    # VaR is a bad day
    assert cvar95 <= var95                 # CVaR is at least as bad as VaR


def test_var_cvar_99_worse_than_95():
    daily = [100.0, -200.0, 50.0, -150.0, 300.0, -80.0, 200.0, -60.0,
             150.0, 120.0, -90.0, 80.0, -40.0, 250.0, 110.0, -30.0,
             130.0, -20.0, 70.0, 90.0, -250.0, 160.0, -180.0, 210.0,
             -300.0, 140.0, -10.0, 180.0, -5.0, -120.0, 220.0, -15.0,
             170.0, -25.0, 190.0, -35.0, 230.0, -45.0, 240.0, -55.0,
             260.0, -65.0, 270.0, -70.0, 280.0, -75.0, 290.0, -85.0,
             310.0, -95.0, 320.0, -105.0, 330.0, -115.0, 340.0, -125.0,
             350.0, -135.0, 360.0, -145.0, 370.0, -155.0, 380.0, -165.0,
             390.0, -175.0, 400.0, -185.0, 410.0, -195.0, 420.0, -205.0,
             430.0, -215.0, 440.0, -225.0, 450.0, -235.0, 460.0, -245.0,
             470.0, -255.0, 480.0, -265.0, 490.0, -275.0, 500.0, -285.0,
             510.0, -295.0, 520.0, -305.0, 530.0, -315.0, 540.0, -325.0,
             550.0, -335.0, 560.0, -345.0]
    var95, _ = _var_cvar(daily, 0.95)
    var99, _ = _var_cvar(daily, 0.99)
    assert var99 <= var95                  # 99% VaR is at least as bad as 95%


def test_max_drawdown_info_peak_trough_recovery():
    # equity curve: rises, then falls below peak, then recovers
    # cumulative: 100, 200, 300, 100, 50, 200, 350
    # daily:       100, 100, 100, -200, -50, 150, 150
    daily_dated = [
        (date(2023, 1, 1), 100.0),
        (date(2023, 1, 2), 100.0),
        (date(2023, 1, 3), 100.0),   # peak at 300
        (date(2023, 1, 4), -200.0),
        (date(2023, 1, 5), -50.0),   # trough at 50
        (date(2023, 1, 6), 150.0),
        (date(2023, 1, 7), 150.0),   # recovery: cum=350 >= peak=300
    ]
    mdd, peak_d, trough_d, rec_d = _max_drawdown_info(daily_dated)
    assert mdd == -250.0                              # 50 - 300
    assert peak_d == date(2023, 1, 3)
    assert trough_d == date(2023, 1, 5)
    assert rec_d == date(2023, 1, 7)


def test_max_drawdown_info_no_recovery():
    # never recovers to the peak
    daily_dated = [
        (date(2023, 1, 1), 200.0),   # peak at 200
        (date(2023, 1, 2), -150.0),  # trough at 50
        (date(2023, 1, 3), 20.0),    # 70, not back to 200
    ]
    mdd, _, _, rec_d = _max_drawdown_info(daily_dated)
    assert mdd == -150.0
    assert rec_d is None


def test_max_drawdown_info_no_drawdown():
    # monotonically rising -> no drawdown
    daily_dated = [
        (date(2023, 1, 1), 100.0),
        (date(2023, 1, 2), 200.0),
        (date(2023, 1, 3), 300.0),
    ]
    mdd, pk, tr, rc = _max_drawdown_info(daily_dated)
    assert mdd == 0.0 and pk is None and tr is None and rc is None


# ── Track 4: evaluate() integration ─────────────────────────────────────────

def test_evaluate_includes_sortino_calmar():
    trades = _spread_days([300, -100, 250, -80, 400, -120, 350, -90] * 8)
    v = evaluate(_result(trades))
    assert "sortino" in v.metrics
    assert v.metrics["sortino"] > 0
    assert v.metrics["calmar"] is not None
    assert v.metrics["calmar"] > 0


def test_evaluate_var_cvar_present():
    trades = _spread_days([300, -100, 250, -80, 400, -120, 350, -90] * 8)
    v = evaluate(_result(trades))
    for k in ("var_95", "cvar_95", "var_99", "cvar_99"):
        assert k in v.metrics
    assert v.metrics["cvar_95"] <= v.metrics["var_95"]
    assert v.metrics["cvar_99"] <= v.metrics["var_99"]


def test_evaluate_cagr_none_without_capital():
    trades = _spread_days([200, -80] * 20)
    v = evaluate(_result(trades))
    assert v.metrics["cagr"] is None
    assert v.metrics["total_return_pct"] is None


def test_evaluate_cagr_with_capital():
    trades = []
    for yr in (2020, 2021, 2022, 2023):
        for j, val in enumerate([300, -100, 250, -80] * 4):
            trades.append(_tr(val, date(yr, 1 + j % 12, 1 + j % 27)))
    v = evaluate(_result(trades), capital=500_000)
    assert v.metrics["cagr"] is not None
    assert v.metrics["total_return_pct"] is not None
    net = v.metrics["net"]
    assert abs(v.metrics["total_return_pct"] - net / 500_000) < 1e-9


def test_evaluate_drawdown_dates_present():
    trades = _spread_days([300, -100, 250, -80, 400, -120, 350, -90] * 8)
    v = evaluate(_result(trades))
    # there will be a drawdown in a mixed-return series
    assert "peak_date" in v.metrics
    assert "trough_date" in v.metrics
    assert "recovery_date" in v.metrics


def test_evaluate_exit_distribution():
    # mix of eod and stop exits
    trades = (
        [_tr(200, date(2022, 1, d), reason="eod") for d in range(1, 15)] +
        [_tr(-100, date(2022, 2, d), reason="stop") for d in range(1, 8)]
    )
    v = evaluate(_result(trades))
    ed = v.metrics["exit_dist"]
    assert "eod" in ed and "stop" in ed
    assert abs(ed["eod"] + ed["stop"] - 1.0) < 1e-9   # fractions sum to 1


def test_evaluate_avg_daily_weekly():
    trades = _spread_days([200, -80] * 20)
    v = evaluate(_result(trades))
    assert "avg_daily" in v.metrics
    assert "avg_weekly" in v.metrics
    assert abs(v.metrics["avg_weekly"] - v.metrics["avg_daily"] * 5) < 1e-9


def test_report_includes_new_metrics():
    trades = _spread_days([300, -100, 250, -80, 400, -120, 350, -90] * 8)
    report = evaluate(_result(trades)).report()
    assert "Sortino" in report
    assert "Calmar" in report
    assert "VaR" in report
    assert "CVaR" in report
    assert "avg daily" in report


def test_report_includes_cagr_when_capital_given():
    trades = []
    for yr in (2020, 2021, 2022, 2023):
        for j, val in enumerate([300, -100, 250, -80] * 4):
            trades.append(_tr(val, date(yr, 1 + j % 12, 1 + j % 27)))
    report = evaluate(_result(trades), capital=500_000).report()
    assert "CAGR" in report
    assert "capital" in report.lower()


def test_report_includes_exit_dist():
    trades = (
        [_tr(200, date(2022, 1, d), reason="eod") for d in range(1, 15)] +
        [_tr(-100, date(2022, 2, d), reason="stop") for d in range(1, 8)]
    )
    report = evaluate(_result(trades)).report()
    assert "exits:" in report
    assert "eod" in report
    assert "stop" in report
