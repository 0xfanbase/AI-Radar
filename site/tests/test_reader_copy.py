"""Copy-lint test: reader-facing empty-state copy must never leak internal
build/ops vocabulary (workflow file names, data file names, phase numbers,
or dev-facing phrasing like "the analyst has not run") to real readers.

This project's target reader is explicitly someone with a keen interest in
AI news who is NOT a technical expert (see CLAUDE.md). Every module-level
`..._MESSAGE` string constant in `site/builders/` is reader-facing copy
(rendered directly into a page, verbatim or via Jinja), so this test
iterates over every one of them, across every builder module, and asserts
none contains a banned token.

Loaded by explicit file path (matching every other `site/tests/*.py`
file's own convention), since `site/` is deliberately not an importable
package -- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILDERS_DIR = REPO_ROOT / "site" / "builders"

# Every builder module this test scans for reader-facing `..._MESSAGE`
# constants. Modules with no such constant (e.g. board.py, primer.py,
# about.py, as of this writing) are still loaded and scanned -- they just
# contribute zero constants to the check, which is fine; the point is this
# list, not each module's current contents.
BUILDER_MODULE_NAMES = [
    "wire",
    "moving",
    "method",
    "corrections",
    "board",
    "lexicon",
    "primer",
    "about",
    "map",
]

# Internal dev/ops vocabulary that must never reach a real reader: workflow
# file names, data/config file extensions, internal build-phase numbering,
# and the specific dev-facing phrase this task was filed to remove.
BANNED_TOKENS = [
    "environment",
    ".yml",
    ".json",
    "Phase ",
    "analyst has not run",
]


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: several builder modules use
    # `@dataclass` together with `from __future__ import annotations`,
    # which needs `sys.modules[cls.__module__]` populated during class
    # creation -- same requirement documented in every sibling
    # site/tests/test_*_builder.py file.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_builder(module_name: str):
    path = BUILDERS_DIR / f"{module_name}.py"
    return _load_module_by_path(f"frontier_wire_site_builders_{module_name}_copylint", path)


def _message_constants(module) -> dict[str, str]:
    """Every module-level constant whose name ends in `_MESSAGE` and whose
    value is a string -- the reader-facing empty-state/placeholder copy
    this test exists to lint."""
    return {
        name: value
        for name, value in vars(module).items()
        if name.isupper() and name.endswith("_MESSAGE") and isinstance(value, str)
    }


def test_every_builder_module_loads_and_is_scanned():
    # Sanity check on this test's own setup: every named builder module
    # actually exists and loads cleanly, so a typo'd module name here
    # can't silently make this test scan nothing.
    for module_name in BUILDER_MODULE_NAMES:
        module = _load_builder(module_name)
        assert module is not None


def test_at_least_one_message_constant_is_found_across_all_builders():
    # Guards against this test file quietly asserting nothing -- if every
    # builder stopped exporting any `_MESSAGE` constant, that would be a
    # sign this test needs updating, not a silent pass.
    total = 0
    for module_name in BUILDER_MODULE_NAMES:
        module = _load_builder(module_name)
        total += len(_message_constants(module))
    assert total > 0


def test_no_reader_facing_message_constant_contains_banned_dev_language():
    violations: list[str] = []
    for module_name in BUILDER_MODULE_NAMES:
        module = _load_builder(module_name)
        for const_name, value in _message_constants(module).items():
            for token in BANNED_TOKENS:
                if token in value:
                    violations.append(
                        f"{module_name}.{const_name} contains banned token "
                        f"{token!r}: {value!r}"
                    )
    assert violations == [], "\n".join(violations)
