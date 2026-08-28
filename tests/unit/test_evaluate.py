"""The shared GO/NO-GO edge battery (analysis/evaluate.py).

Duck-typed on trades: each trade needs `.net` (float) and `.date` (datetime.date).
A Result is anything with `.trades`. Tests build synthetic results so the verdict
logic is pinned independently of the engine.
"""
from datetime import date
from types import SimpleNamespace

from analysis.evaluate import evaluate, Verdict


def _tr(net, d):
    return SimpleNamespace(net=float(net), date=d, entry=100.0, qty=50)


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
