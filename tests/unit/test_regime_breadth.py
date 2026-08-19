"""Breadth is deferred: its score is always None so the classifier renormalizes
the other five modules over themselves (see test_regime_classifier drop/renorm)."""
from engine.regime.breadth import breadth_score


def test_breadth_is_deferred_returns_none():
    assert breadth_score() is None
    assert breadth_score("anything", universe=[1, 2, 3]) is None
