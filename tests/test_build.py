from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aujobs import build
from aujobs.config import read_json, write_json_atomic


def test_build_joins_scores_and_tags_major_groups(
    sample_occupations: list[dict[str, Any]], sample_scores: dict[str, dict[str, Any]]
) -> None:
    result = build.build(sample_occupations, sample_scores)

    by_code = {row["anzsco_code"]: row for row in result["occupations"]}
    assert by_code["2611"]["category"] == "Professionals"
    assert by_code["2611"]["ai_exposure"] == 8
    assert by_code["3222"]["rationale"].startswith("Physical installation")
    assert result["meta"]["total_occupations"] == len(sample_occupations)
    assert result["meta"]["scored_occupations"] == len(sample_scores)


def test_weighted_average_ignores_unscored_employment() -> None:
    occupations = [
        {"anzsco_code": "2611", "title": "A", "employment": 100},
        {"anzsco_code": "3222", "title": "B", "employment": 100},
        # Unscored, and large enough to dominate if it were counted.
        {"anzsco_code": "8219", "title": "C", "employment": 100_000},
    ]
    scores = {
        "2611": {"score": 10, "rationale": "x"},
        "3222": {"score": 0, "rationale": "y"},
    }

    meta = build.build(occupations, scores)["meta"]

    assert meta["avg_ai_exposure_weighted"] == 5.0
    assert meta["total_employment"] == 100_200
    assert meta["scored_occupations"] == 2


def test_build_handles_no_scores_at_all() -> None:
    occupations = [{"anzsco_code": "2611", "title": "A", "employment": 100}]

    result = build.build(occupations, {})

    assert result["meta"]["avg_ai_exposure_weighted"] is None
    assert result["occupations"][0]["ai_exposure"] is None
    assert result["occupations"][0]["rationale"] is None


def test_build_coerces_missing_and_unparseable_numbers() -> None:
    occupations = [
        {
            "anzsco_code": "2611",
            "title": "A",
            "employment": None,
            "median_earnings": "n/a",
            "growth_5yr_pct": "3.5",
        }
    ]

    row = build.build(occupations, {})["occupations"][0]

    assert row["employment"] == 0.0
    assert row["median_earnings"] is None
    assert row["growth_5yr_pct"] == 3.5


def test_build_skips_rows_without_a_code() -> None:
    result = build.build([{"anzsco_code": "", "title": "Nameless", "employment": 1}], {})

    assert result["occupations"] == []
    assert result["meta"]["total_occupations"] == 0


def test_unknown_codes_fall_into_other() -> None:
    result = build.build([{"anzsco_code": "9999", "title": "Mystery", "employment": 1}], {})

    assert result["occupations"][0]["category"] == "Other"


def test_run_writes_site_data(
    tmp_path: Path,
    sample_occupations: list[dict[str, Any]],
    sample_scores: dict[str, dict[str, Any]],
) -> None:
    occupations_path = tmp_path / "occupations.json"
    scores_path = tmp_path / "scores.json"
    output_path = tmp_path / "site" / "data.json"
    write_json_atomic(occupations_path, sample_occupations)
    write_json_atomic(scores_path, sample_scores)

    build.run(
        occupations_path=occupations_path,
        scores_path=scores_path,
        output_path=output_path,
        sample=True,
    )

    written = read_json(output_path)
    assert written["meta"]["sample"] is True
    assert len(written["occupations"]) == len(sample_occupations)


def test_run_requires_the_earlier_stages(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="aujobs extract"):
        build.run(
            occupations_path=tmp_path / "missing.json",
            scores_path=tmp_path / "scores.json",
            output_path=tmp_path / "data.json",
        )

    write_json_atomic(tmp_path / "occupations.json", [])
    with pytest.raises(FileNotFoundError, match="aujobs score"):
        build.run(
            occupations_path=tmp_path / "occupations.json",
            scores_path=tmp_path / "missing-scores.json",
            output_path=tmp_path / "data.json",
        )
