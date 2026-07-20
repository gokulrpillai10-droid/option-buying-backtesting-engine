from analysis.gap_fill_excursion import atm_strike, excursion_with_stop


def test_atm_strike_picks_nearest():
    assert atm_strike([24000, 24050, 24100], 24074) == 24050
    assert atm_strike([24000, 24050, 24100], 24076) == 24100


def test_excursion_no_stop_records_full_peak():
    # entry 100; rises to 130 (mfe 30), dips to 90 (mae 10); stop far away
    path = [100, 110, 130, 120, 90, 95]
    mfe, mae, stopped = excursion_with_stop(path, entry=100, stop_pts=50)
    assert (mfe, mae, stopped) == (30.0, 10.0, False)


def test_excursion_stop_truncates_favorable():
    # entry 100; dips to 75 first (adverse 25 -> stop@20 fires), THEN would rally to 140.
    # favorable AFTER the stop must NOT count: mfe stays 0, stopped True, mae full.
    path = [100, 90, 75, 140]
    mfe, mae, stopped = excursion_with_stop(path, entry=100, stop_pts=20)
    assert stopped is True
    assert mfe == 0.0        # never went favorable before the stop
    assert mae == 25.0


def test_excursion_favorable_then_stop():
    # entry 100; up to 118 (mfe 18) BEFORE dropping to 78 (stop@20 fires at -22).
    path = [100, 118, 108, 78, 130]
    mfe, mae, stopped = excursion_with_stop(path, entry=100, stop_pts=20)
    assert mfe == 18.0       # credited: reached before the stop
    assert stopped is True
    assert mae == 22.0
