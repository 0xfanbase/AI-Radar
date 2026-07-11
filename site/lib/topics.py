"""Shared raw-enum -> display-label mapping for topic tags.

`card.schema.json`'s `topics` enum and `whats_moving.schema.json`'s fixed
nine-topic tag set share the exact same nine raw machine values (e.g.
`"chips/compute"`, `"open-source"`), and both surfaces need the same
friendlier, human-readable label for a couple of the raw values that read
awkwardly as page copy verbatim. This module is the single place that
mapping lives, so a Wire card's topic chips and the `/moving/` page's own
topic rows can never drift apart on what a given raw topic value displays
as -- see `site/builders/moving.py` (topic rows) and
`site/builders/wire.py::prepare_card_view` (card topic chips), both of
which load this module by path and delegate to :func:`display_name`.
"""
from __future__ import annotations

TOPIC_DISPLAY_NAMES: dict[str, str] = {
    "models": "Models",
    "research": "Research",
    "chips/compute": "Chips / Compute",
    "policy": "Policy",
    "products": "Products",
    "safety": "Safety",
    "open-source": "Open Source",
    "China": "China",
    "funding": "Funding",
}


def display_name(topic: str) -> str:
    """Friendly display label for a raw topic enum value, falling back to
    the raw value itself for anything not in the map (e.g. a future topic
    added to the schema before this mapping is updated)."""
    return TOPIC_DISPLAY_NAMES.get(topic, topic)
