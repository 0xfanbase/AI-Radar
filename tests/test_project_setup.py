"""Trivial checks that the project scaffolding is wired up correctly:
the watcher package imports and pytest itself runs in this environment.
"""
import watcher


def test_watcher_package_imports():
    assert watcher is not None


def test_pytest_runs():
    # Reaching this assertion at all proves pytest discovery, the
    # autouse conftest fixture, and test execution all work end-to-end.
    assert True
