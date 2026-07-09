"""Shared pytest fixtures for the whole test suite.

The project's hard rule is "no live network calls in the default pytest
run." This is enforced structurally here, not just by convention: every
test gets its HTTP escape hatch replaced with a stub that raises
immediately, unless the test is explicitly marked ``@pytest.mark.live``
(see ``pytest.ini``, which also excludes the ``live`` marker from the
default run via ``addopts = -m "not live"``).

All of the project's fetchers are designed to funnel through a single
shared ``requests.Session`` (built in the watcher's HTTP layer), so
patching ``requests.sessions.Session.request`` is the one choke point that
covers HN, arXiv, and every lab fetcher alike.
"""
import pytest
import requests


@pytest.fixture(autouse=True)
def block_live_network_calls(request, monkeypatch):
    """Raise on any HTTP call made via requests.Session, unless the
    current test node is marked ``live``.
    """
    if request.node.get_closest_marker("live") is not None:
        # Live tests keep the real requests.sessions.Session.request
        # implementation and are free to hit the network.
        yield
        return

    def _blocked_request(self, method, url, *args, **kwargs):
        raise RuntimeError(
            f"Live network call blocked in non-live test: {method} {url}. "
            "Mark the test with @pytest.mark.live to allow real network "
            "access (such tests are excluded from the default "
            "`python -m pytest` run by pytest.ini)."
        )

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked_request)
    yield
