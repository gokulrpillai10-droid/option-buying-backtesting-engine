#!/usr/bin/env bash
# Daily driver for the regime detector's live forward-validation test.
#
# Chains the two non-interactive steps into one command:
#   1. refresh NIFTY 50 + India VIX daily bars from Kite
#   2. log today's regime label + grade any predictions whose horizon matured
#
# Prereq (interactive, expires daily — run FIRST):
#   cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py
#
# Usage (from anywhere): ./analysis/forward_test_daily.sh
set -euo pipefail

# repo root = parent of this script's dir, so it runs from anywhere
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== [1/2] refreshing NIFTY 50 + India VIX daily bars =="
python3 -m analysis.refresh_nifty_vix_daily

echo ""
echo "== [2/2] logging today's regime label + grading matured predictions =="
python3 -m analysis.regime_forward_journal
