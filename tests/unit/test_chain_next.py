from datetime import datetime

from engine.data.chain import Chain


def test_next_open_returns_following_minute_open(simple_chain_df):
    chain = Chain(simple_chain_df)
    # signal at 09:15 -> fill at 09:16 open (anti-look-ahead). 20000 call = 110.0
    price = chain.next_open(datetime(2024, 1, 1, 9, 15), 20000, "CALL")
    assert price == 110.0


def test_next_open_is_none_when_no_future_bar(simple_chain_df):
    chain = Chain(simple_chain_df)
    # 09:16 is the last bar -> nothing to fill into
    price = chain.next_open(datetime(2024, 1, 1, 9, 16), 20000, "CALL")
    assert price is None


def test_next_open_put_side(simple_chain_df):
    chain = Chain(simple_chain_df)
    # 20000 put at 09:16 = base 110.0
    price = chain.next_open(datetime(2024, 1, 1, 9, 15), 20000, "PUT")
    assert price == 110.0
