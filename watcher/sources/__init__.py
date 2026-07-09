"""Per-source fetchers for the Phase 1 watcher (arXiv, HN, labs).

Each module here is responsible for exactly one upstream source and
returns a list of :class:`watcher.models.Item`. Clustering/ranking never
need to know source-specific fetch details beyond that shared shape.
"""
