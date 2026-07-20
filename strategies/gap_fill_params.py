"""Frozen gap-fill exit parameters — SINGLE SOURCE OF TRUTH.

Derived by analysis/gap_fill_premium_scale.py on the 2020-2024 TRAIN window and
frozen (never tuned on backtest P&L). Re-run the pre-study to change these, then
update this file; the pinning test asserts they match the recorded _out.txt.
All exit values are PREMIUM points.
"""
BOOK = 16.90      # from pre-study
TRAIL = 8.45      # from pre-study
STOP = 51.15      # from pre-study
FLOOR = 0.0005    # from pre-study
CAP = 0.005
SPREAD_ASSUMED = 3.0
TRAIN_START = "2020-01-01"
TRAIN_END = "2024-12-31"
