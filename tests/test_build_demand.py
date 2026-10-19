from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aujobs import build, demand, manifest
from aujobs.score import RUBRIC_VERSION, Occupation, build_prompt


def _snapshot() -> demand.DemandSnapshot:
    return demand.DemandSnapshot(
        as_of="2026-06",
        rows=(demand.DemandRow("26", 500.0, 500.0, 0.0),),
        source_url="https://example.test/ivi.xlsx",
    )


OCCUPATIONS: list[dict[str, Any]] = [
    {"anzsco_code": "2611", "title": "Analysts", "employment": 100},
    {"anzsco_code": "2544", "title": "Nurses", "employment": 300},
]
SCORES: dict[str, dict[str, Any]] = {
    "2611": {"score": 8, "rationale": "desk work"},
    "2544": {"score": 4, "rationale": "patient care"},
}


def test_build_attaches_demand_by_prefix_and_reports_the_data_date() -> None:
    result = build.build(OCCUPATIONS, SCORES, demand_snapshot=_snapshot())

    # Analysts (2611) fall in group 26; nurses (2544) fall in group 25, which has no row.
    analysts, nurses = result["occupations"]
    assert analysts["demand"]["matched_code"] == "26"
    assert analysts["demand"]["change_12m_pct"] == 0.0
    assert nurses["demand"] is None
    assert result["meta"]["data_as_of"] == "2026-06"
    assert result["meta"]["demand_matched"] == 1
    assert result["meta"]["demand_source_url"] == "https://example.test/ivi.xlsx"


def test_build_without_demand_leaves_fields_empty() -> None:
    result = build.build(OCCUPATIONS, SCORES)

    assert all(row["demand"] is None for row in result["occupations"])
    assert result["meta"]["data_as_of"] is None
    assert result["meta"]["demand_matched"] == 0


def test_build_maps_osca_codes_and_counts_them() -> None:
    result = build.build(OCCUPATIONS, SCORES, osca={"2611": "OSCA-1"})

    by_code = {row["anzsco_code"]: row for row in result["occupations"]}
    assert by_code["2611"]["osca_code"] == "OSCA-1"
    assert by_code["2544"]["osca_code"] is None
    assert result["meta"]["osca_mapped"] == 1


def test_build_records_snapshots_and_the_rubric_version(tmp_path: Path) -> None:
    workbook = tmp_path / "profiles.xlsx"
    workbook.write_bytes(b"abc")
    snapshot = manifest.record(
        "occupation_profiles",
        workbook,
        "https://example.test/profiles.xlsx",
        manifest_path=tmp_path / "manifest.json",
        retrieved_at="2026-10-01T00:00:00+00:00",
    )

    result = build.build(OCCUPATIONS, SCORES, snapshots=[snapshot])

    recorded = result["meta"]["snapshots"][0]
    assert recorded["name"] == "occupation_profiles"
    assert recorded["retrieved_at"] == "2026-10-01T00:00:00+00:00"
    assert result["meta"]["rubric_version"] == RUBRIC_VERSION


def test_run_rejects_invalid_inputs_before_writing(tmp_path: Path) -> None:
    occupations = tmp_path / "occupations.json"
    scores = tmp_path / "scores.json"
    output = tmp_path / "site.json"
    occupations.write_text('[{"anzsco_code": "26", "title": "Bad code"}]', encoding="utf-8")
    scores.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="must be 4 digits"):
        build.run(
            occupations_path=occupations,
            scores_path=scores,
            output_path=output,
            demand_path=None,
            crosswalk_path=None,
            manifest_path=None,
        )
    assert not output.exists()


def test_build_prompt_includes_task_text_and_truncates_it() -> None:
    plain = build_prompt(Occupation("2611", "Analysts"))
    detailed = build_prompt(Occupation("2611", "Analysts", "Analyse data. " * 500))

    assert plain == "Occupation: Analysts (ANZSCO 2611)"
    assert "Tasks and description:" in detailed
    assert len(detailed) < 1400
