from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def sample_occupations() -> list[dict[str, Any]]:
    raw = (EXAMPLES / "sample_occupations.json").read_text(encoding="utf-8")
    return cast("list[dict[str, Any]]", json.loads(raw))


@pytest.fixture
def sample_scores() -> dict[str, dict[str, Any]]:
    raw = (EXAMPLES / "sample_scores.json").read_text(encoding="utf-8")
    return cast("dict[str, dict[str, Any]]", json.loads(raw))
