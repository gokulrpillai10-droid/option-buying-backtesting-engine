"""Tests for engine/instruments.py — the real NIFTY lot-size schedule and
LotTable lookup and anomaly-correction behavior."""
from datetime import date

import pandas as pd
import pytest

from engine.instruments import LotTable, lot_size_for


@pytest.mark.parametrize("day,expected_lot", [
	(date(2020, 6, 1), 75),
	(date(2021, 6, 1), 50),
	(date(2024, 6, 1), 25),
	(date(2025, 6, 1), 75),
	(date(2026, 6, 1), 65),
])
def test_lot_size_for_mid_period(day, expected_lot):
	assert lot_size_for(day) == expected_lot


def test_lot_size_for_day_before_change_uses_old_lot():
	assert lot_size_for(date(2024, 5, 8)) == 50


def test_lot_size_for_exact_change_day_uses_new_lot():
	assert lot_size_for(date(2024, 5, 9)) == 25


def test_lot_size_for_before_schedule_start_uses_first_entry():
	assert lot_size_for(date(1999, 1, 1)) == 75


def test_isolated_spike_is_corrected_to_neighbor_value():
	raw = {
		date(2025, 1, 23): 75,
		date(2025, 1, 30): 25,
		date(2025, 2, 6): 75,
	}
	cleaned = LotTable._clean_anomalies(raw)
	assert cleaned[date(2025, 1, 30)] == 75
	assert cleaned[date(2025, 1, 23)] == 75
	assert cleaned[date(2025, 2, 6)] == 75


def test_clean_anomalies_emits_a_warning_when_correcting():
	raw = {date(2025, 1, 23): 75, date(2025, 1, 30): 25, date(2025, 2, 6): 75}
	with pytest.warns(UserWarning, match="isolated lot-size anomaly"):
		LotTable._clean_anomalies(raw)


def test_genuine_regime_change_is_not_flattened():
	raw = {
		date(2024, 5, 2): 50,
		date(2024, 5, 9): 25,
		date(2024, 5, 16): 25,
	}
	cleaned = LotTable._clean_anomalies(raw)
	assert cleaned[date(2024, 5, 9)] == 25


def test_small_difference_is_never_treated_as_anomaly():
	raw = {
		date(2024, 1, 1): 75,
		date(2024, 1, 8): 65,
		date(2024, 1, 15): 75,
	}
	cleaned = LotTable._clean_anomalies(raw)
	assert cleaned[date(2024, 1, 8)] == 65


def test_endpoints_cannot_be_corrected():
	raw = {
		date(2024, 1, 1): 25,
		date(2024, 1, 8): 75,
		date(2024, 1, 15): 75,
	}
	cleaned = LotTable._clean_anomalies(raw)
	assert cleaned[date(2024, 1, 1)] == 25


def test_for_expiry_exact_match():
	table = LotTable({date(2025, 1, 2): 75, date(2026, 1, 6): 65})
	assert table.for_expiry(date(2025, 1, 2)) == 75


def test_for_expiry_falls_back_to_nearest_earlier_known_expiry():
	table = LotTable({date(2025, 1, 2): 75, date(2026, 1, 6): 65})
	assert table.for_expiry(date(2025, 6, 15)) == 75


def test_for_expiry_before_any_known_expiry_returns_none():
	table = LotTable({date(2025, 1, 2): 75})
	assert table.for_expiry(date(2020, 1, 1)) is None


def test_for_day_uses_nearest_expiry_on_or_after():
	table = LotTable({date(2025, 1, 2): 75, date(2025, 1, 9): 65})
	assert table.for_day(date(2024, 12, 20)) == 75


def test_for_day_past_the_table_falls_back_to_last_known():
	table = LotTable({date(2025, 1, 2): 75, date(2025, 1, 9): 65})
	assert table.for_day(date(2030, 1, 1)) == 65


def test_for_expiry_and_for_day_diverge_on_the_same_table():
	table = LotTable({date(2025, 1, 2): 75, date(2025, 1, 9): 65})
	day = date(2025, 1, 5)
	assert table.for_expiry(day) == 75
	assert table.for_day(day) == 65


def test_from_frame_builds_and_cleans_in_one_step():
	df = pd.DataFrame({
		"Expiry": [date(2025, 1, 23), date(2025, 1, 30), date(2025, 2, 6)],
		"LotSize": [75, 25, 75],
	})
	table = LotTable.from_frame(df)
	assert table.for_expiry(date(2025, 1, 30)) == 75
