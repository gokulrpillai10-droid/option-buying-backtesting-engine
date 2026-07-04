"""Contract tests for the Strategy base class."""
import pytest

from engine.strategy.base import Strategy


def test_on_bar_must_be_implemented():
    with pytest.raises(NotImplementedError):
        Strategy().on_bar(ctx=None)


def test_on_eod_defaults_to_no_orders():
    assert Strategy().on_eod(ctx=None) == []


def test_subclass_on_bar_is_used():
    class Buy(Strategy):
        def on_bar(self, ctx):
            return ["order"]

    assert Buy().on_bar(ctx=None) == ["order"]
