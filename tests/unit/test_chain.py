from datetime import datetime

from engine.data.chain import Chain


def test_snapshot_at_returns_all_strikes_for_that_minute(simple_chain_df):
    chain = Chain(simple_chain_df)
    snap = chain.snapshot_at(datetime(2024, 1, 1, 9, 15))
    assert snap.strikes() == [19900, 20000, 20100]


def test_snapshot_call_quote_reads_minute_values(simple_chain_df):
    chain = Chain(simple_chain_df)
    snap = chain.snapshot_at(datetime(2024, 1, 1, 9, 16))
    # at 09:16 the 20000 strike call_open base is 110.0
    quote = snap.call(20000)
    assert quote.open == 110.0
    assert quote.volume == 500
    assert quote.oi == 30000
    assert quote.is_traded is True
