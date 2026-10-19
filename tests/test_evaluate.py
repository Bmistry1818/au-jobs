from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aujobs import evaluate
from aujobs.config import EXAMPLES_DIR
from aujobs.score import Occupation, Score


def test_shipped_gold_labels_load() -> None:
    labels = evaluate.load_gold(EXAMPLES_DIR / "gold_labels.csv")

    assert len(labels) == 6
    assert all(0 <= label.expected <= 10 for label in labels)


def test_agreement_counts_tolerance_misses_and_missing_codes() -> None:
    labels = [
        evaluate.GoldLabel("2611", 8, 1),
        evaluate.GoldLabel("2544", 4, 1),
        evaluate.GoldLabel("3222", 2, 1),
        evaluate.GoldLabel("8219", 1, 1),
    ]
    scores: dict[str, dict[str, Any]] = {
        "2611": {"score": 7},  # error 1, within tolerance
        "2544": {"score": 9},  # error 5, a miss
        "3222": {"score": 2},  # error 0
    }

    report = evaluate.agreement(scores, labels)

    assert report.evaluated == 3
    assert report.missing == ("8219",)
    assert report.within_tolerance == pytest.approx(2 / 3, abs=0.01)
    assert report.mean_absolute_error == pytest.approx(2.0)
    assert report.misses == (("2544", 4, 9),)


def test_agreement_with_nothing_scored_reports_no_figures() -> None:
    report = evaluate.agreement({}, [evaluate.GoldLabel("2611", 8, 1)])

    assert report.evaluated == 0
    assert report.within_tolerance is None
    assert report.missing == ("2611",)


def test_load_gold_rejects_out_of_range_labels(tmp_path: Path) -> None:
    path = tmp_path / "gold.csv"
    path.write_text("code,expected,tolerance\n2611,12,1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected must be 0-10"):
        evaluate.load_gold(path)


class _FakeScorer:
    def __init__(self, values: list[int]) -> None:
        self._values = values

    def score(self, occupation: Occupation) -> Score:
        return Score(title=occupation.title, score=self._values.pop(0), rationale="x")


def test_variance_reports_mean_and_spread() -> None:
    occupation = Occupation("2611", "Analysts")

    rows = evaluate.variance(_FakeScorer([6, 8, 7]), [occupation], repeats=3)

    assert rows[0].scores == (6, 8, 7)
    assert rows[0].mean == pytest.approx(7.0)
    assert rows[0].spread == 2


def test_sample_is_reproducible_and_bounded() -> None:
    occupations = [Occupation(f"26{index:02d}", f"Job {index}") for index in range(20)]

    first = evaluate.sample(occupations, 5, seed=1)
    second = evaluate.sample(occupations, 5, seed=1)

    assert first == second
    assert len(first) == 5
    assert evaluate.sample(occupations, 100) == occupations
