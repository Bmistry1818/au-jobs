"""Stage 4 — merge the pipeline outputs into ``site/data.json``.

Inputs are the occupations (extract), the scores (score), and optionally the demand
signal (demand), the OSCA crosswalk and the snapshot manifest. The output is what the
site fetches:

``occupations``
    One row per ANZSCO occupation, with its major group, score, demand and OSCA code.
``meta``
    Totals, the employment-weighted average exposure, the data date, the source
    snapshots, and a ``sample`` flag so the site can label the checked-in demo dataset.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

from aujobs import crosswalk, demand, manifest, validate
from aujobs.config import (
    DEMAND_JSON,
    MANIFEST_JSON,
    OCCUPATIONS_JSON,
    OSCA_CROSSWALK_CSV,
    SCORES_JSON,
    SITE_DATA_JSON,
    major_group,
    read_json,
    write_json_atomic,
)
from aujobs.score import RUBRIC_VERSION

log = logging.getLogger(__name__)


class SnapshotInfo(TypedDict):
    name: str
    retrieved_at: str
    source_url: str
    sha256: str


class Meta(TypedDict):
    total_occupations: int
    scored_occupations: int
    total_employment: int
    avg_ai_exposure_weighted: float | None
    sample: bool
    data_as_of: str | None
    demand_source_url: str | None
    demand_synthetic: bool
    demand_matched: int
    osca_mapped: int
    rubric_version: str
    snapshots: list[SnapshotInfo]


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
    demand_snapshot: demand.DemandSnapshot | None = None,
    osca: dict[str, str] | None = None,
    snapshots: Sequence[manifest.Snapshot] = (),
) -> SiteData:
    """Join scores, demand and OSCA codes onto occupations and compute the summary."""
    osca = osca or {}
    codes = [str(occupation.get("anzsco_code") or "").strip() for occupation in occupations]
    matches = demand.match_to_occupations(codes, demand_snapshot.rows) if demand_snapshot else {}

    rows: list[dict[str, Any]] = []
    scored_employment = 0.0
    weighted_exposure = 0.0
    total_employment = 0.0
    scored_count = 0

    for occupation, code in zip(occupations, codes, strict=True):
        if not code:
            continue
        entry = scores.get(code, {})
        employment = _as_number(occupation.get("employment")) or 0.0
        exposure = entry.get("score")
        exposure = exposure if isinstance(exposure, int) else None

        demand_block: dict[str, Any] | None = None
        match = matches.get(code)
        if match is not None:
            matched_code, row = match
            demand_block = {
                "matched_code": matched_code,
                "ads_latest": row.ads_latest,
                "ads_12m_avg": row.ads_12m_avg,
                "change_12m_pct": row.change_12m_pct,
            }

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
                "osca_code": osca.get(code),
                "demand": demand_block,
            }
        )

        total_employment += employment
        if exposure is not None:
            scored_count += 1
            if employment:
                scored_employment += employment
                weighted_exposure += exposure * employment

    # Weighted over scored occupations only; including unscored ones in the
    # denominator would silently drag the average toward zero.
    average = round(weighted_exposure / scored_employment, 2) if scored_employment else None

    meta: Meta = {
        "total_occupations": len(rows),
        "scored_occupations": scored_count,
        "total_employment": int(total_employment),
        "avg_ai_exposure_weighted": average,
        "sample": sample,
        "data_as_of": demand_snapshot.as_of if demand_snapshot else None,
        "demand_source_url": demand_snapshot.source_url if demand_snapshot else None,
        "demand_synthetic": demand_snapshot.synthetic if demand_snapshot else False,
        "demand_matched": sum(1 for row in rows if row["demand"] is not None),
        "osca_mapped": sum(1 for row in rows if row["osca_code"]),
        "rubric_version": RUBRIC_VERSION,
        "snapshots": [
            {
                "name": snapshot.name,
                "retrieved_at": snapshot.retrieved_at,
                "source_url": snapshot.source_url,
                "sha256": snapshot.sha256[:12],
            }
            for snapshot in sorted(snapshots, key=lambda item: item.name)
        ],
    }
    return {"occupations": rows, "meta": meta}


def run(
    *,
    occupations_path: Path = OCCUPATIONS_JSON,
    scores_path: Path = SCORES_JSON,
    output_path: Path = SITE_DATA_JSON,
    demand_path: Path | None = DEMAND_JSON,
    crosswalk_path: Path | None = OSCA_CROSSWALK_CSV,
    manifest_path: Path | None = MANIFEST_JSON,
    sample: bool = False,
) -> SiteData:
    """Validate the inputs, build the site data and write it. Missing optional inputs are skipped."""
    if not occupations_path.exists():
        raise FileNotFoundError(f"{occupations_path} not found — run `aujobs extract` first.")
    if not scores_path.exists():
        raise FileNotFoundError(f"{scores_path} not found — run `aujobs score` first.")

    occupations = read_json(occupations_path)
    scores = read_json(scores_path)
    validate.raise_if_invalid(
        validate.occupation_problems(occupations) + validate.score_problems(scores),
        "Pipeline inputs",
    )

    demand_snapshot: demand.DemandSnapshot | None = None
    if demand_path is not None:
        if demand_path.exists():
            validate.raise_if_invalid(
                validate.demand_problems(read_json(demand_path)), "Demand data"
            )
            demand_snapshot = demand.load_demand(demand_path)
        else:
            log.warning("No demand data at %s. Run `aujobs demand` to add it.", demand_path)

    osca = crosswalk.load(crosswalk_path) if crosswalk_path is not None else {}
    snapshots = list(manifest.load(manifest_path).values()) if manifest_path is not None else []

    site_data = build(
        occupations,
        scores,
        sample=sample,
        demand_snapshot=demand_snapshot,
        osca=osca,
        snapshots=snapshots,
    )
    validate.raise_if_invalid(validate.site_problems(site_data), "Site data")
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
    log.info(
        "  Demand matched: %d (as of %s). OSCA mapped: %d.",
        meta["demand_matched"],
        meta["data_as_of"] or "not loaded",
        meta["osca_mapped"],
    )
    return site_data


__all__: Sequence[str] = ("Meta", "SiteData", "build", "run")
