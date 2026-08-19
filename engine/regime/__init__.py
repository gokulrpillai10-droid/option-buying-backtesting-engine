"""Daily NIFTY buy-vs-sell regime detector.

Turns prior-close market data into a `RegimeBias` of BUY (gamma / storm) /
SELL (theta / calm) / NEUTRAL. See `contract.classify` for the entry point.
Spec: docs/superpowers/specs/2026-08-13-regime-buysell-detector-design.md
"""
