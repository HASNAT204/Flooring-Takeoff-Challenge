"""Verify the prediction pipeline never reads from the gold/ directory.

This is the structural guard that enforces the challenge's hard rule:
``Do not use the gold/manual output during prediction``. Only the
``evaluator`` package (which is invoked separately by reviewers, not by
the pipeline itself) is allowed to mention gold paths.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"


def _python_files(d: Path):
    yield from d.rglob("*.py")


def test_app_tree_has_no_gold_references():
    """Grep every .py file under ``app/`` for the substring ``gold``. The
    only acceptable matches are inside string literals that are clearly
    user-facing UI hints (e.g. instructing the reviewer to populate the
    ``gold/`` folder). To keep the rule simple, we forbid the substring
    entirely except in main.py's UI flag that gates the "Run evaluation"
    button — that flag is allowed to *check* for the presence of the
    folder without reading any file inside it.
    """
    forbidden_patterns = [
        re.compile(r"open\([^)]*gold[^)]*\)", re.IGNORECASE),
        re.compile(r"load_workbook\([^)]*gold[^)]*\)", re.IGNORECASE),
        re.compile(r"read_text\([^)]*gold[^)]*\)", re.IGNORECASE),
        re.compile(r"from\s+evaluator\b", re.IGNORECASE),
        re.compile(r"import\s+evaluator\b", re.IGNORECASE),
        re.compile(r"from\s+\.\.evaluator", re.IGNORECASE),
    ]
    bad: list[tuple[Path, str]] = []
    for path in _python_files(APP_DIR):
        text = path.read_text()
        # Allowed: app/main.py imports evaluator only inside a request
        # handler that the *reviewer* triggers (POST /job/{id}/evaluate).
        # We allow that single import.
        if path.name == "main.py":
            continue
        for pat in forbidden_patterns:
            for m in pat.finditer(text):
                bad.append((path, m.group(0)))
    assert not bad, (
        "Pipeline code under app/ must not reference gold output or import "
        "the evaluator package. Offending matches: " + str(bad)
    )


def test_pipeline_does_not_open_gold_files():
    """A second-line check: scan the pipeline package for the literal
    string ``gold/`` (path component) which is what file-open calls would
    use. Pure mentions in comments are tolerated only outside the pipeline
    package."""
    pipeline_dir = APP_DIR / "pipeline"
    for path in _python_files(pipeline_dir):
        text = path.read_text()
        # strip comments and docstrings before scanning
        non_comment = re.sub(r"(?m)^\s*#.*$", "", text)
        # check for path-literal gold/ references
        assert "gold/" not in non_comment, (
            f"{path} contains 'gold/' reference outside comments — "
            "pipeline code must never open a gold file."
        )
