from analysis.gap_fill_premium_scale import _bucket


def test_bucket_boundaries_and_cap_edge():
    assert _bucket(0.0005) == "0.05-0.15"
    assert _bucket(0.001) == "0.05-0.15"
    assert _bucket(0.0015) == "0.15-0.30"
    assert _bucket(0.003) == "0.30-0.50"
    assert _bucket(0.005) == "0.30-0.50"   # CAP exactly -> top bucket, no IndexError
