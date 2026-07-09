"""Shared pytest fixtures for the whole test suite.

The project's hard rule is "no live network calls in the default pytest
run." This is enforced structurally here, not just by convention: every
test gets its HTTP escape hatch replaced with a stub that raises
immediately, unless the test is explicitly marked ``@pytest.mark.live``
(see ``pytest.ini``, which also excludes the ``live`` marker from the
default run via ``addopts = -m "not live"``).

All of the project's fetchers are designed to funnel through a single
shared ``requests.Session`` (built in the watcher's HTTP layer), so
patching ``requests.sessions.Session.send`` is the one choke point that
covers HN, arXiv, and every lab fetcher alike.

Note this patches ``Session.send`` rather than the higher-level
``Session.request``: ``requests-mock`` (used throughout this project's
fixture-based fetcher tests) works by patching ``Session.send`` /
``Session.get_adapter`` itself so it can hand back a canned response
without ever touching the network. Patching ``request`` here would shadow
that -- ``request()`` would raise before it ever reached ``send()``, so a
``requests-mock``-backed test would fail even though it never makes a real
call. Patching at the ``send`` seam means: when a test wires up
``requests-mock``, its patch of ``send`` (applied after this fixture's,
since it's requested by the test function rather than autouse) wins for
the test's duration and the mocked response flows through normally; when
no mock is wired up, this stub is still the one that runs, so a genuine
outbound call still raises immediately unless the test is marked ``live``.
"""
import pytest
import requests


@pytest.fixture(autouse=True)
def block_live_network_calls(request, monkeypatch):
    """Raise on any real HTTP send via requests.Session, unless the
    current test node is marked ``live``.
    """
    if request.node.get_closest_marker("live") is not None:
        # Live tests keep the real requests.sessions.Session.send
        # implementation and are free to hit the network.
        yield
        return

    def _blocked_send(self, prepared_request, **kwargs):
        raise RuntimeError(
            f"Live network call blocked in non-live test: "
            f"{prepared_request.method} {prepared_request.url}. "
            "Mark the test with @pytest.mark.live to allow real network "
            "access (such tests are excluded from the default "
            "`python -m pytest` run by pytest.ini), or use the "
            "`requests_mock` fixture for deterministic fixture-based "
            "coverage instead."
        )

    monkeypatch.setattr(requests.sessions.Session, "send", _blocked_send)
    yield
