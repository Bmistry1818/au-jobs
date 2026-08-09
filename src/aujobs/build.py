"""Stage 3 — merge occupation stats and AI exposure scores into ``site/data.json``.

The output is what the treemap fetches:

``occupations``
    One row per ANZSCO occupation, tagged with its major group.
``meta``
    Totals plus the employment-weighted average exposure, and a ``sample``
    flag so the site can label the checked-in demo dataset.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

from aujobs.config import (
    OCCUPATIONS_JSON,
    SCORES_JSON,
    SITE_DATA_JSON,
    major_group,
    read_json,
    write_json_atomic,
)

log = logging.getLogger(__name__)


class Meta(TypedDict):
    total_occupations: int
    scored_occupations: int
    total_employment: int
    avg_ai_exposure_weighted: float | None
    sample: bool


class SiteData(TypedDict):
    occupations: list[dict[str, Any]]
    meta: Meta


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # drop NaN


def build(
    occupations: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
    *,
    sample: bool = False,
) -> SiteData:
    """Join scores onto occupations and compute the summary metadata."""
    rows: list[dict[str, Any]] = []
    scored_employment = 0.0
    weighted_exposure = 0.0
    total_employment = 0.0
    scored_count = 0

    for occupation in occupations:
        code = str(occupation.get("anzsco_code") or "").strip()
        if not code:
            continue
        entry = scores.get(code, {})
        employment = _as_number(occupation.get("employment")) or 0.0
        exposure = entry.get("score")
        exposure = exposure if isinstance(exposure, int) else None

        rows.append(
            {
                "anzsco_code": code,
                "title": occupation.get("title"),
                "category": major_group(code),
                "employment": employment,
                "median_earnings": _as_number(occupation.get("median_earnings")),
                "education": occupation.get("education"),
                "growth_5yr_pct": _as_number(occupation.get("growth_5yr_pct")),
                "growth_10yr_pct": _as_number(occupation.get("growth_10yr_pct")),
                "ai_exposure": exposure,
                "rationale": entry.get("rationale"),
            }
        )

        total_employment += employment
        if exposure is not None:
            scored_count += 1
            if employment:
                scored_employment += employment
                weighted_exposure += exposure * employment

    # Weighted over scored occupations only — including unscored ones in the
    # denominator would silently drag the average toward zero.
    average = round(weighted_exposure / scored_employment, 2) if scored_employment else None

    return {
        "occupations": rows,
        "meta": {
            "total_occupations": len(rows),
            "scored_occupations": scored_count,
            "total_employment": int(total_employment),
            "avg_ai_exposure_weighted": average,
            "sample": sample,
        },
    }


def run(
    *,
    occupations_path: Path = OCCUPATIONS_JSON,
    scores_path: Path = SCORES_JSON,
    output_path: Path = SITE_DATA_JSON,
    sample: bool = False,
) -> SiteData:
    if not occupations_path.exists():
        raise FileNotFoundError(f"{occupations_path} not found — run `aujobs extract` first.")
    if not scores_path.exists():
        raise FileNotFoundError(f"{scores_path} not found — run `aujobs score` first.")

    occupations = read_json(occupations_path)
    scores = read_json(scores_path)
    site_data = build(occupations, scores, sample=sample)

    write_json_atomic(output_path, site_data)
    meta = site_data["meta"]
    log.info("Wrote %s", output_path)
    log.info(
        "  %d occupations (%d scored), %s total employment",
        meta["total_occupations"],
        meta["scored_occupations"],
        f"{meta['total_employment']:,}",
    )
    log.info("  Job-weighted average AI exposure: %s", meta["avg_ai_exposure_weighted"])
    return site_data


__all__: Sequence[str] = ("Meta", "SiteData", "build", "run")
