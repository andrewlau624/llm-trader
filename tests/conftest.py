import pytest


@pytest.fixture
def cfg():
    from llmtrader.config import Config

    c = Config()
    c.symbols = ["TEST"]
    c.slippage_bps = 0.0
    c.commission_per_share = 0.0
    c.extra_context_symbols = []
    return c
