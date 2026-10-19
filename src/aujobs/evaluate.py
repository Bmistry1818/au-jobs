"""Evaluation: how well the scores agree with a gold set, and how stable they are.

Two measurements (Phase 2):

* **Gold-set agreement** compares scores with labels in ``examples/gold_labels.csv``.
  It makes no API calls.
* **Variance** scores a sample of occupations several times and reports the spread.
  It makes API calls.

The shipped gold labels are provisional. They are not an independent expert panel,
so agreement figures are a sanity check until the labels have been reviewed.
"""

from __future__ import annotations

import csv
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aujobs.config import EXAMPLES_DIR
from aujobs.score import Occupation, Score

GOLD_CSV: Path = EXAMPLES_DIR / "gold_labels.csv"
DEFAULT_TOLERANCE = 1


class Scorer(Protocol):
    def score(self, occupation: Occupation) -> Score: ...


@dataclass(frozen=True, slots=True)
class GoldLabel:
    code: str
    expected: int
    tolerance: int


@dataclass(frozen=True, slots=True)
class AgreementReport:
    evaluated: int
    missing: tuple[str, ...]
    mean_absolute_error: float | None
    within_tolerance: float | None
    misses: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True, slots=True)
class VarianceRow:
    code: str
    title: str
    scores: tuple[int, ...]
    mean: float
    spread: int


def load_gold(path: Path = GOLD_CSV) -> list[GoldLabel]:
    if not path.exists():
        return []
    labels: list[GoldLabel] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            code = (row.get("code") or "").strip()
            try:
                expected = int(row["expected"])
                tolerance = int(row.get("tolerance") or DEFAULT_TOLERANCE)
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_number}: expected and tolerance must be integers"
                ) from exc
            if not 0 <= expected <= 10 or tolerance < 0:
                raise ValueError(f"{path}:{line_number}: expected must be 0-10 and tolerance >= 0")
            labels.append(GoldLabel(code=code, expected=expected, tolerance=tolerance))
    return labels


def agreement(
    scores: Mapping[str, Mapping[str, Any]], labels: Sequence[GoldLabel]
) -> AgreementReport:
    matched: list[tuple[GoldLabel, int]] = []
    missing: list[str] = []
    for label in labels:
        entry = scores.get(label.code)
        score = entry.get("score") if entry else None
        if isinstance(score, int):
            matched.append((label, score))
        else:
            missing.append(label.code)

    if not matched:
        return AgreementReport(0, tuple(missing), None, None, ())

    errors = [abs(score - label.expected) for label, score in matched]
    within = [error <= label.tolerance for (label, _), error in zip(matched, errors, strict=True)]
    misses = tuple(
        (label.code, label.expected, score)
        for (label, score), ok in zip(matched, within, strict=True)
        if not ok
    )
    return AgreementReport(
        evaluated=len(matched),
        missing=tuple(missing),
        mean_absolute_error=round(statistics.fmean(errors), 2),
        within_tolerance=round(sum(within) / len(within), 2),
        misses=misses,
    )


def sample(occupations: Sequence[Occupation], size: int, *, seed: int = 0) -> list[Occupation]:
    """A reproducible random sample, so repeated variance runs cover the same occupations."""
    if size >= len(occupations):
        return list(occupations)
    return random.Random(seed).sample(list(occupations), size)


def variance(scorer: Scorer, occupations: Sequence[Occupation], repeats: int) -> list[VarianceRow]:
    """Score each occupation ``repeats`` times and summarise the spread."""
    rows: list[VarianceRow] = []
    for occupation in occupations:
        values = tuple(scorer.score(occupation).score for _ in range(repeats))
        rows.append(
            VarianceRow(
                code=occupation.anzsco_code,
                title=occupation.title,
                scores=values,
                mean=round(statistics.fmean(values), 2),
                spread=max(values) - min(values),
            )
        )
    return rows
