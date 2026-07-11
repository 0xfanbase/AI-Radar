"""Tests for watcher/cli.py.

No live network anywhere: the three fetch functions watcher.cli imports
(fetch_hn_items/fetch_arxiv_items/fetch_all_lab_items) are monkeypatched to
return plain fixture Items directly, so cli.run() exercises its own real
orchestration logic (clustering -> ranking -> ledger diff -> queue_writer
-> whats_moving -> ledger save) without ever reaching watcher.http.fetch or
requests.Session.send (which tests/conftest.py's autouse fixture would
block anyway, as a second safety net).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from watcher.cli import RunResult, main, run
from watcher.ledger import empty_ledger
from watcher.models import Item

FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _fake_items():
    hn_items = [
        Item(
            source_type="hn",
            source_name="hn",
            title="OpenAI Ships GPT-5 With Major New Features",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T11:00:00Z",
            points=200,
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="Someone Built A Small Weekend AI Side Project",
            url="https://news.ycombinator.com/item?id=101",
            published_at="2026-07-08T07:00:00Z",
            points=5,
        ),
    ]
    arxiv_items = [
        Item(
            source_type="arxiv",
            source_name="arxiv",
            title="A Completely Unrelated Paper About Reinforcement Learning",
            url="https://arxiv.org/abs/2607.00001",
            published_at="2026-07-08T09:00:00Z",
        )
    ]
    lab_items = [
        Item(
            source_type="lab",
            source_name="openai",
            title="OpenAI Releases GPT-5 With Major Upgrades",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
        )
    ]
    return hn_items, arxiv_items, lab_items


def _patch_fetchers(monkeypatch, hn_items, arxiv_items, lab_items):
    monkeypatch.setattr("watcher.cli.fetch_hn_items", lambda session, **kwargs: hn_items)
    monkeypatch.setattr("watcher.cli.fetch_arxiv_items", lambda session, **kwargs: arxiv_items)
    monkeypatch.setattr("watcher.cli.fetch_all_lab_items", lambda session, **kwargs: lab_items)
    # build_session() itself does no I/O, but stub it too so no real
    # requests.Session with mounted retry adapters needs to be constructed.
    monkeypatch.setattr("watcher.cli.build_session", lambda: object())


# --------------------------------------------------------------------------
# run() -- full orchestration against fixture fetchers
# --------------------------------------------------------------------------


def test_run_writes_queue_ledger_and_whats_moving(tmp_path, monkeypatch):
    hn_items, arxiv_items, lab_items = _fake_items()
    _patch_fetchers(monkeypatch, hn_items, arxiv_items, lab_items)

    ledger_path = tmp_path / "ledger.json"
    queue_path = tmp_path / "queue.json"
    whats_moving_path = tmp_path / "whats_moving.json"

    result = run(
        now=FIXED_NOW,
        ledger_path=ledger_path,
        queue_path=queue_path,
        whats_moving_path=whats_moving_path,
    )

    assert isinstance(result, RunResult)
    assert result.hn_items == 2
    assert result.arxiv_items == 1
    assert result.lab_items == 1
    # OpenAI lab post + HN discussion of it share a URL -> merge into one
    # cluster; the weekend side-project and the arXiv paper stay separate.
    assert result.clusters == 3

    assert ledger_path.is_file()
    assert queue_path.is_file()
    assert whats_moving_path.is_file()

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(ledger["entries"]) == 3
    assert result.ledger_size_before == 0
    assert result.ledger_size_after == 3
    assert result.new_ledger_keys == 3

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert len(queue) == result.queue_size == 3  # nothing already carded

    whats_moving = json.loads(whats_moving_path.read_text(encoding="utf-8"))
    assert len(whats_moving["topics"]) == 9

    assert result.hn_urls == frozenset(item.url for item in hn_items)
    assert result.arxiv_urls == frozenset(item.url for item in arxiv_items)
    assert result.lab_urls == frozenset(item.url for item in lab_items)


def test_run_second_identical_run_adds_zero_new_ledger_keys(tmp_path, monkeypatch):
    hn_items, arxiv_items, lab_items = _fake_items()
    _patch_fetchers(monkeypatch, hn_items, arxiv_items, lab_items)

    ledger_path = tmp_path / "ledger.json"
    queue_path = tmp_path / "queue.json"
    whats_moving_path = tmp_path / "whats_moving.json"

    first = run(
        now=FIXED_NOW,
        ledger_path=ledger_path,
        queue_path=queue_path,
        whats_moving_path=whats_moving_path,
    )
    later = FIXED_NOW.replace(day=FIXED_NOW.day + 1)
    second = run(
        now=later,
        ledger_path=ledger_path,
        queue_path=queue_path,
        whats_moving_path=whats_moving_path,
    )

    assert second.ledger_size_before == first.ledger_size_after
    assert second.new_ledger_keys == 0
    assert second.ledger_size_after == first.ledger_size_after

    # Nothing has a card_id yet (no analyst in Phase 1), so the same
    # clusters are still unpublished and still all fit in the <=8 queue.
    assert second.queue_size == first.queue_size


def test_run_excludes_already_carded_cluster_from_queue(tmp_path, monkeypatch):
    hn_items, arxiv_items, lab_items = _fake_items()
    _patch_fetchers(monkeypatch, hn_items, arxiv_items, lab_items)

    ledger_path = tmp_path / "ledger.json"
    queue_path = tmp_path / "queue.json"
    whats_moving_path = tmp_path / "whats_moving.json"

    # Pre-seed a ledger where the arXiv paper's cluster is already carded.
    from watcher.clustering import cluster_items
    from watcher.ranking import rank_clusters

    all_items = hn_items + arxiv_items + lab_items
    clusters = cluster_items(all_items)
    ranked = rank_clusters(clusters, now=FIXED_NOW, limit=len(clusters))
    arxiv_hash = next(
        r.cluster_hash for r in ranked if r.cluster.items[0].source_type == "arxiv"
    )

    seeded_ledger = empty_ledger()
    seeded_ledger["entries"][arxiv_hash] = {
        "card_id": "2026-07-08-arxiv-paper",
        "status": "published",
        "first_seen": "2026-07-01",
        "last_seen": "2026-07-08",
        "member_urls": [arxiv_items[0].url],
    }
    ledger_path.write_text(json.dumps(seeded_ledger), encoding="utf-8")

    result = run(
        now=FIXED_NOW,
        ledger_path=ledger_path,
        queue_path=queue_path,
        whats_moving_path=whats_moving_path,
    )

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queued_urls = {source["url"] for entry in queue for source in entry["sources"]}
    assert arxiv_items[0].url not in queued_urls
    assert result.queue_size == 2  # 3 clusters total, 1 already carded

    # But the ledger itself still tracks all 3 clusters (already-carded one
    # untouched, the other two upserted).
    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(ledger_after["entries"]) == 3
    assert ledger_after["entries"][arxiv_hash]["card_id"] == "2026-07-08-arxiv-paper"


def test_run_handles_empty_fetch_pool_without_raising(tmp_path, monkeypatch):
    _patch_fetchers(monkeypatch, [], [], [])

    ledger_path = tmp_path / "ledger.json"
    queue_path = tmp_path / "queue.json"
    whats_moving_path = tmp_path / "whats_moving.json"

    result = run(
        now=FIXED_NOW,
        ledger_path=ledger_path,
        queue_path=queue_path,
        whats_moving_path=whats_moving_path,
    )

    assert result.clusters == 0
    assert result.queue_size == 0
    assert json.loads(queue_path.read_text(encoding="utf-8")) == []
    whats_moving = json.loads(whats_moving_path.read_text(encoding="utf-8"))
    assert all(t["daily_counts"] == [0] * 7 for t in whats_moving["topics"])


# --------------------------------------------------------------------------
# main() -- argparse wiring
# --------------------------------------------------------------------------


def test_main_run_invokes_run_and_prints_summary(monkeypatch, capsys):
    fake_result = RunResult(
        hn_items=2,
        arxiv_items=1,
        lab_items=1,
        clusters=3,
        queue_size=3,
        ledger_size_before=0,
        ledger_size_after=3,
        new_ledger_keys=3,
    )
    monkeypatch.setattr("watcher.cli.run", lambda: fake_result)

    exit_code = main(["run"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "hn=2" in captured.out
    assert "arxiv=1" in captured.out
    assert "lab=1" in captured.out
    assert "clusters=3" in captured.out
    assert "queue=3" in captured.out
    assert "ledger 0->3" in captured.out
    assert "(+3 new)" in captured.out


def test_main_requires_a_subcommand(capsys):
    with pytest.raises(SystemExit):
        main([])


def test_main_rejects_unknown_subcommand():
    with pytest.raises(SystemExit):
        main(["bogus-command"])
