"""Sharpened volatility axis for the NIFTY regime detector.

The base labeler (``analysis/regime_finder.py``) calls a day calm/stormy from ONE
signal: price wobble (realized-vol z-score). This module blends in India VIX (the
market's own fear gauge) so the mood call needs two independent measures to agree,
and adds a "fear rising vs falling" dimension the base labeler lacks.

Rule: STORMY if EITHER price-wobble OR VIX flags danger (react fast); CALM only if
BOTH agree it is quiet (conservative). Persistence is applied by the caller via
``regime_finder.persistence`` (DRY).

All series are trailing / look-ahead safe.
Input: analysis/india_vix.csv (close) + analysis/nifty_daily.csv (close).
"""
from __future__ import annotations

import pandas as pd


def expanding_percentile(series: pd.Series, min_periods: int = 252) -> pd.Series:
    """Rank of each value within its own trailing history (expanding window).

    Value at ``i`` = share of observations ``<= series[i]`` among ``series[:i+1]``.
    Look-ahead safe (never sees the future); NaN until ``min_periods`` observations.
    """
    return series.expanding(min_periods=min_periods).apply(
        lambda s: float((s <= s.iloc[-1]).mean()), raw=False
    )


def vix_state(pct: float, hi: float = 0.66, lo: float = 0.33):
    """VIX level bucket from its expanding percentile: ``'high'`` (top tercile ->
    fear), ``'low'`` (bottom tercile -> calm), or ``None`` (middle / warm-up)."""
    if pd.isna(pct):
        return None
    if pct >= hi:
        return "high"
    if pct <= lo:
        return "low"
    return None


def blend_vol(price_high: bool, price_low: bool,
              vix_high: bool, vix_low: bool):
    """Blend price-wobble and VIX into one mood label.

    STORMY if EITHER measure flags danger (react fast); CALM only if BOTH agree it
    is quiet (conservative); otherwise ``None`` (transitional).
    """
    if price_high or vix_high:
        return "stormy"
    if price_low and vix_low:
        return "calm"
    return None


def fear_rising(vix: pd.Series, lookback: int = 10) -> pd.Series:
    """True where VIX is above its own value ``lookback`` days ago -- fear building
    (vs cooling). Trailing/look-ahead safe."""
    return vix > vix.shift(lookback)


# ------------------------------------------------------------------ #
# Driver: build the sharpened labels + report (pure helpers above are tested)
# ------------------------------------------------------------------ #
NIFTY_CSV = "analysis/nifty_daily.csv"
VIX_CSV = "analysis/india_vix.csv"
VIX_PCT_WARMUP = 252     # 1y before VIX level is judged high/low


def sharpen_labels(closes, vix):
    """Blend the price-wobble vol axis (regime_finder) with India VIX into a
    sharpened calm/stormy label, plus a fear-rising flag and the direction axis.

    Returns a DataFrame aligned to the labelled trading days.
    """
    import pandas as pd
    from analysis.regime_finder import label_regimes, persistence

    lab = label_regimes(closes)
    vix = vix.reindex(lab.index).astype(float)         # align to labelled days
    vix_pct = expanding_percentile(vix, min_periods=VIX_PCT_WARMUP)
    vix_hi = vix_pct.apply(lambda p: vix_state(p) == "high")
    vix_lo = vix_pct.apply(lambda p: vix_state(p) == "low")

    mood = [blend_vol(bool(ph), bool(pl), bool(vh), bool(vl))
            for ph, pl, vh, vl in zip(lab["high_vol"], lab["low_vol"], vix_hi, vix_lo)]
    mood = pd.Series(mood, index=lab.index)

    out = pd.DataFrame(index=lab.index)
    out["bull"], out["bear"] = lab["bull"], lab["bear"]
    out["price_high_vol"] = lab["high_vol"]            # old (price-only) storm
    out["stormy"] = mood == "stormy"
    out["calm"] = mood == "calm"
    out["stable_calm"] = persistence(out["calm"])
    out["stable_stormy"] = persistence(out["stormy"])
    out["fear_rising"] = fear_rising(vix).reindex(lab.index).fillna(False)
    return out


def sharp_state(row) -> str:
    """One combined label from a sharpened-labels row: mood + direction."""
    mood = "Stormy" if row["stormy"] else ("Calm" if row["calm"] else "Mixed")
    if row["bull"]:
        d = "Bull"
    elif row["bear"]:
        d = "Bear"
    else:
        d = "Neutral"
    return f"{mood}_{d}"


def main():
    import sys
    import pandas as pd

    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    vix = pd.read_csv(VIX_CSV, parse_dates=["date"], index_col="date")["close"]
    lab = sharpen_labels(closes, vix)

    print("SHARPENED REGIME DETECTOR — price-wobble + India VIX blend")
    print(f"labelled {len(lab)} days {lab.index[0].date()}..{lab.index[-1].date()}")
    print("(calm needs BOTH price AND VIX quiet; stormy needs EITHER; "
          f"VIX level warms up after {VIX_PCT_WARMUP}d)\n")

    # how much does VIX change the storm call vs price-only?
    price_storm = int(lab["price_high_vol"].sum())
    blend_storm = int(lab["stormy"].sum())
    added = int((lab["stormy"] & ~lab["price_high_vol"]).sum())
    dropped = int((~lab["stormy"] & lab["price_high_vol"]).sum())
    print(f"STORM DAYS: price-only={price_storm}  blended={blend_storm}  "
          f"(VIX ADDED {added}, removed {dropped})")

    state = lab.apply(sharp_state, axis=1)
    print(f"\nSHARPENED STATE CALENDAR counts: {dict(state.value_counts())}")

    # sanity: COVID must read stormy under the blend
    covid = lab.loc["2020-02-20":"2020-05-15"]
    if len(covid):
        share = covid["stormy"].mean()
        print(f"\nSANITY COVID 2020-02..05 stormy share {share:.0%} (need >=50%): "
              f"{'PASS' if share >= 0.5 else 'FAIL'}")

    print("\nLAST 8 sharpened episodes (>=5d):")
    rid = (state != state.shift()).cumsum()
    runs = [(seg.index[0].date(), seg.index[-1].date(), len(seg), seg.iloc[0])
            for _, seg in state.groupby(rid) if len(seg) >= 5]
    for a, b, n, s in runs[-8:]:
        fr = "fear^" if lab.loc[str(b), "fear_rising"] else "fear v"
        print(f"  {a}..{b}  {n:>3}d  {s:<16} ({fr} at end)")

    print(f"\nCURRENT ({lab.index[-1].date()}): {state.iloc[-1]}  "
          f"| fear_rising={bool(lab['fear_rising'].iloc[-1])}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
