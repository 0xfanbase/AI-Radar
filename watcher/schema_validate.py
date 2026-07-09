"""Thin jsonschema wrapper used by every writer in the pipeline.

Every persisted JSON artifact under ``content/`` and ``data/`` has a
corresponding schema under ``schemas/<artifact>.schema.json`` (see
CLAUDE.md's "Schema & test conventions"). This module gives the rest of the
codebase exactly one entry point -- :func:`validate` -- so a malformed
artifact never reaches disk or a commit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _schema_path(schema_name: str) -> Path:
    return SCHEMAS_DIR / f"{schema_name}.schema.json"


def load_schema(schema_name: str) -> dict:
    """Load and return schemas/<schema_name>.schema.json as a dict.

    Raises FileNotFoundError if no such schema file exists.
    """
    path = _schema_path(schema_name)
    if not path.is_file():
        raise FileNotFoundError(
            f"No schema named {schema_name!r} at {path} "
            f"(expected schemas/{schema_name}.schema.json)."
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate(instance: Any, schema_name: str) -> None:
    """Validate `instance` against schemas/<schema_name>.schema.json.

    Raises jsonschema.ValidationError -- with the artifact name prefixed
    onto the message so failures are traceable to a specific artifact --
    on the first validation failure. Raises FileNotFoundError if no such
    schema exists.
    """
    schema = load_schema(schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)

    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise jsonschema.ValidationError(f"[{schema_name}] {first.message}")
