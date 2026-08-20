"""Data-integrity invariants for option-chain frames.

These guard the class of bug that triggered the whole effort: a multi-day /
multi-expiry frame filtered on an INCOMPLETE key (time but not date) silently
returns wrong-context rows and .iloc[0] picks an arbitrary one. The invariants
turn that silent wrong-answer into a loud, immediate failure.
"""
from datetime import datetime, time

import pandas as pd
import pytest

from engine.data.integrity import (
    IntegrityError, one_row, at_most_one_row, check_single_day, check_session_bounds,
    check_snapshot, check_call_monotone, scoped_day, snapshot_at,
)


def _snap(dt, strikes, forward, call_from=50.0):
    """One clean single-timestamp snapshot: distinct strikes, one forward,
    call premiums decreasing with strike (a real chain)."""
    rows = []
    for i, k in enumerate(sorted(strikes)):
        rows.append(dict(DateTime=dt, StrikePrice=k, Forward=forward,
                         CallClose=max(call_from - i, 0.1), CallIsTradedStrike=True))
    return pd.DataFrame(rows)


# ---- the exact bug: same strike/time across two days, time-only filter -------- #

def test_time_only_filter_across_days_is_caught_as_duplicate_strikes():
    d1 = _snap(datetime(2024, 1, 4, 14, 0), [21600, 21700, 21800], 21650)
    d2 = _snap(datetime(2024, 1, 3, 14, 0), [21600, 21700, 21800], 20000)   # different day, same clock time
    mixed = pd.concat([d1, d2], ignore_index=True)                          # what a time-only filter yields
    with pytest.raises(IntegrityError, match="duplicate strike|distinct Forward"):
        check_snapshot(mixed)                                               # 21700 appears twice + 2 forwards


def test_clean_snapshot_passes():
    check_snapshot(_snap(datetime(2024, 1, 4, 14, 0), [21600, 21700, 21800], 21650))  # no raise


# ---- single-day / session / cardinality -------------------------------------- #

def test_multi_day_frame_fails_single_day():
    df = pd.concat([_snap(datetime(2024, 1, 4, 14, 0), [21700], 21650),
                    _snap(datetime(2024, 1, 3, 14, 0), [21700], 20000)], ignore_index=True)
    with pytest.raises(IntegrityError, match="one day"):
        check_single_day(df)


def test_out_of_session_row_fails():
    df = _snap(datetime(2024, 1, 4, 8, 0), [21700], 21650)   # 08:00 IST, before 09:15 open
    with pytest.raises(IntegrityError, match="session"):
        check_session_bounds(df)


def test_one_row_requires_exactly_one():
    df = _snap(datetime(2024, 1, 4, 14, 0), [21600, 21700, 21800], 21650)
    assert one_row(df, StrikePrice=21700)["CallClose"] == df.set_index("StrikePrice").loc[21700, "CallClose"]
    with pytest.raises(IntegrityError, match="exactly 1"):
        one_row(df, StrikePrice=99999)                       # zero rows
    dup = pd.concat([df, df], ignore_index=True)
    with pytest.raises(IntegrityError, match="exactly 1"):
        one_row(dup, StrikePrice=21700)                      # two rows


def test_at_most_one_row_allows_zero_fails_on_many():
    df = _snap(datetime(2024, 1, 4, 14, 0), [21600, 21700], 21650)
    assert at_most_one_row(df, StrikePrice=21700)["CallClose"] == df.set_index("StrikePrice").loc[21700, "CallClose"]
    assert at_most_one_row(df, StrikePrice=99999) is None    # missing is legitimate -> None
    with pytest.raises(IntegrityError, match="<=1"):
        at_most_one_row(pd.concat([df, df], ignore_index=True), StrikePrice=21700)


# ---- monotonicity (gross non-monotone = mixed context) ----------------------- #

def test_monotone_clean_chain_passes_mixed_fails():
    clean = _snap(datetime(2024, 1, 4, 14, 0), range(21000, 22000, 50), 21500)
    check_call_monotone(clean)                               # decreasing -> passes
    mixed = clean.sort_values("StrikePrice").copy()
    mixed["CallClose"] = range(1, len(mixed) + 1)            # premium RISES with strike -> corr +1 (impossible for a real call curve)
    with pytest.raises(IntegrityError, match="monoton"):
        check_call_monotone(mixed)


# ---- strict=False downgrades to a warning ------------------------------------ #

def test_strict_false_warns_not_raises():
    bad = _snap(datetime(2024, 1, 4, 8, 0), [21700], 21650)
    with pytest.warns(UserWarning):
        check_session_bounds(bad, strict=False)              # no raise


# ---- guarded readers end-to-end ---------------------------------------------- #

def test_scoped_day_and_snapshot_at_return_single_context():
    df = pd.concat([_snap(datetime(2024, 1, 4, 14, 0), [21600, 21700, 21800], 21650),
                    _snap(datetime(2024, 1, 4, 14, 5), [21600, 21700, 21800], 21655),
                    _snap(datetime(2024, 1, 3, 14, 0), [21600, 21700, 21800], 20000)], ignore_index=True)
    day = scoped_day(df, "2024-01-04")                       # drops the 2024-01-03 rows, checks invariants
    assert set(pd.to_datetime(day["DateTime"]).dt.date.unique()) == {pd.Timestamp("2024-01-04").date()}
    snap = snapshot_at(day, time(14, 0))                     # exactly the 14:00 snapshot, invariants pass
    assert snap["Forward"].nunique() == 1
    assert not snap["StrikePrice"].duplicated().any()
