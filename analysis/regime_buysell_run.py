"""Run the buy-vs-sell regime detector over history and report the calendar.

Detector: engine/regime/ (features -> classifier -> contract). BUY = gamma/storm
(favor option buying), SELL = theta/calm (favor selling), NEUTRAL = stand aside.

Fast historical walk: every feature module is trailing/expanding only, so its
value on day t computed over the full series equals the value `classify` would
compute from data < t+1. We therefore compute each module series ONCE and
`shift(1)` — the bias for day t uses prior-close (t-1) inputs, matching
`contract.classify`'s look-ahead cut exactly (the leak test pins that identity).
skew/breadth are None here (no chain snapshot in the bulk walk).

Run:  PYTHONPATH=. python3 analysis/regime_buysell_run.py | tee analysis/regime_buysell_run_out.txt
Spec: docs/superpowers/specs/2026-08-13-regime-buysell-detector-design.md
"""
import pandas as pd

from analysis.regime_data import load_closes_vix
from analysis.regime_finder import label_regimes, episodes
from engine.regime.features import vol_state, vol_dynamics, iv_rv_ratio, vix_percentile
from engine.regime.classifier import composite, persist, BASE_WEIGHTS


def module_frame(closes, vix):
    """Prior-close module scores per day (shift(1) = look-ahead-safe, matches classify)."""
    labels = label_regimes(closes)
    mods = pd.DataFrame({
        "vol_state": vol_state(labels),
        "vol_dynamics": vol_dynamics(vix),
        "iv_rv": iv_rv_ratio(vix, closes),
        "vix_level": vix_percentile(vix),
    })
    return mods.shift(1)


def calendar(mods, thr=0.15, min_agreement=0.6, weights=BASE_WEIGHTS):
    """Row-wise composite -> raw BUY/SELL/NEUTRAL verdicts (before persistence)."""
    verdict = {}
    for day, row in mods.iterrows():
        scores = {m: row.get(m) for m in ("vol_state", "vol_dynamics", "iv_rv", "vix_level")}
        scores["skew"] = None
        scores["breadth"] = None
        verdict[day] = composite(scores, weights=weights, thr=thr,
                                 min_agreement=min_agreement).label
    return pd.Series(verdict)


def _counts(cal):
    c = cal.value_counts()
    return {k: int(c.get(k, 0)) for k in ("BUY", "SELL", "NEUTRAL")}


def _episode_stats(cal, label):
    eps = episodes(cal == label)
    longest = max((d for _, _, d in eps), default=0)
    return len(eps), longest


def main():
    closes, vix = load_closes_vix()
    mods = module_frame(closes, vix)
    valid = mods.dropna(how="all")
    print("NIFTY BUY-vs-SELL REGIME DETECTOR — historical calendar")
    print(f"window {valid.index[0].date()}..{valid.index[-1].date()}  "
          f"({len(valid)} scored days; BUY=gamma/storm, SELL=theta/calm)")
    print(f"module coverage (non-null days): "
          f"{ {m: int(mods[m].notna().sum()) for m in mods.columns} }")

    raw = calendar(mods)
    stable = persist(raw)
    print("\nCALENDAR COUNTS (default thr=0.15, min_agree=0.60)")
    print(f"  raw       : {_counts(raw)}")
    print(f"  persisted : {_counts(stable)}   (4-of-5 flip-flop guard)")

    print("\nEPISODES (persisted; power-check needs >=3 episodes & >=30 days/side)")
    for side in ("BUY", "SELL"):
        n_ep, longest = _episode_stats(stable, side)
        days = _counts(stable)[side]
        gate = "PASS" if (n_ep >= 3 and days >= 30) else "thin"
        print(f"  {side:<4}: {days:>4} days / {n_ep:>2} episodes  (longest {longest}d)  [{gate}]")

    print("\nTHRESHOLD SENSITIVITY (persisted counts)")
    print(f"  {'thr':>5} {'min_agr':>8}   {'BUY':>5} {'SELL':>5} {'NEUTRAL':>8}")
    for thr in (0.10, 0.15, 0.20, 0.25):
        for ma in (0.6,):
            c = _counts(persist(calendar(mods, thr=thr, min_agreement=ma)))
            print(f"  {thr:>5.2f} {ma:>8.2f}   {c['BUY']:>5} {c['SELL']:>5} {c['NEUTRAL']:>8}")

    print("\nLAST 10 DAYS (persisted bias)")
    for day, lab in stable.tail(10).items():
        sc = composite({**{m: mods.loc[day].get(m) for m in
                           ('vol_state', 'vol_dynamics', 'iv_rv', 'vix_level')},
                        'skew': None, 'breadth': None})
        print(f"  {day.date()}  {lab:<8} (raw {sc.label:<8} score {sc.score:+.3f} conf {sc.confidence:.2f})")

    print("\nREAD")
    print("  Detector is look-ahead safe (prior-close in); skew/breadth deferred (None).")
    print("  This calendar is the detector OUTPUT; the GO/NO-GO verdict comes from the")
    print("  straddle thermometer (analysis/regime_straddle_pnl.py), not from here.")


if __name__ == "__main__":
    main()
