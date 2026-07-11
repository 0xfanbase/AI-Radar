"""``auditor`` -- Phase 5's pure-code, no-LLM weekly checks (the real
implementation of CLAUDE.md's ``audit.yml`` "weekly" bullet).

Five independent, self-contained checker modules (``linkrot``,
``lexicon_audit``, ``duplicates``, ``trend``, ``missed_story``), each
individually unit-tested against fixtures/a real, currently-empty repo
state; ``report.py`` assembles all five checkers' own output dicts into
one ``schemas/audit.schema.json``-shaped ``data/audit/latest.json``
artifact; ``cli.py`` wires the whole pipeline together behind
``python -m auditor.cli run``, including promoting actionable findings
into ``IMPROVEMENT_BACKLOG.md`` via ``scripts/append_backlog_findings.py``.

**This package ships with an explicit ``__init__.py``, reversing an
earlier logged decision.** ``IMPROVEMENT_BACKLOG.md``'s "Phase 5:
`auditor/lexicon_audit.py`" entry deliberately left this file out,
matching ``scripts/``'s own ``__init__.py``-free convention (Python's
implicit namespace-package handling already made — and still makes —
every ``from auditor.<module> import ...`` resolve correctly with no
``__init__.py`` present, given ``python -m pytest``/``python -m
auditor.cli`` are run from the repo root). That reasoning wasn't wrong for
what existed at the time — there was no package-level CLI entrypoint yet.
This turn adds one (``python -m auditor.cli run``), which is exactly the
shape ``watcher/__init__.py`` already backs for ``watcher/cli.py``'s own
``python -m watcher.cli run`` — so an explicit package marker now makes
``auditor/`` symmetric with ``watcher/`` (a package with a real
``cli.py`` entrypoint) rather than ``scripts/`` (a flat collection of
standalone scripts, each invoked directly, never imported as a package by
one another). Added per this turn's own explicit instruction; logged in
full, including the reversal, in ``IMPROVEMENT_BACKLOG.md``. Nothing
functional required this addition — ``python -m pytest`` stayed green
before and after adding it — so this is a consistency/explicitness choice,
not a bug fix.
"""
