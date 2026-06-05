import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """DRF's AnonRateThrottle keeps its per-IP counters in the Django cache,
    which otherwise persists across tests in the same process — so a few
    register/login POSTs spread over several tests can trip the 5/min register
    throttle and make a later, unrelated test get 429 instead of its expected
    response. Clear the cache around every test so each starts with fresh
    throttle counters."""
    cache.clear()
    yield
    cache.clear()
