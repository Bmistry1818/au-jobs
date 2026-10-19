from __future__ import annotations

import pytest

from aujobs import validate


def test_occupation_problems_flags_codes_duplicates_titles_and_employment() -> None:
    rows = [
        {"anzsco_code": "2611", "title": "Analysts", "employment": 100},
        {"anzsco_code": "2611", "title": "Analysts again", "employment": 100},
        {"anzsco_code": "26", "title": "Group", "employment": 1},
        {"anzsco_code": "3222", "title": "", "employment": -5},
    ]

    problems = validate.occupation_problems(rows)

    assert any("duplicate" in problem for problem in problems)
    assert any("anzsco_code must be 4 digits" in problem for problem in problems)
    assert any("missing title" in problem for problem in problems)
    assert any("non-negative" in problem for problem in problems)


def test_clean_occupations_have_no_problems() -> None:
    rows = [{"anzsco_code": "2611", "title": "Analysts", "employment": None}]

    assert validate.occupation_problems(rows) == []


def test_score_problems_flags_out_of_range_and_wrong_types() -> None:
    scores = {
        "2611": {"score": 11, "rationale": "x"},
        "2544": {"score": True, "rationale": "x"},
        "3222": {"score": 2, "rationale": None},
        "8219": "not an object",
    }

    assert len(validate.score_problems(scores)) == 4


def test_demand_problems_checks_the_month_and_counts() -> None:
    payload = {
        "as_of": "June 2026",
        "rows": [{"code": "2611", "ads_latest": -1}, {"code": "12345", "ads_latest": 3}],
    }

    problems = validate.demand_problems(payload)

    assert any("as_of must look like YYYY-MM" in problem for problem in problems)
    assert any("ads_latest" in problem for problem in problems)
    assert any("1 to 4 digits" in problem for problem in problems)


def test_site_problems_flags_exposure_outside_the_scale() -> None:
    data = {
        "meta": {"total_occupations": 1, "scored_occupations": 1, "sample": True},
        "occupations": [{"ai_exposure": 11}],
    }

    assert len(validate.site_problems(data)) == 1


def test_raise_if_invalid_reports_the_count_when_truncated() -> None:
    problems = [f"problem {index}" for index in range(25)]

    with pytest.raises(ValueError, match="and 5 more"):
        validate.raise_if_invalid(problems, "Test artefact")


def test_raise_if_invalid_is_silent_when_clean() -> None:
    validate.raise_if_invalid([], "Test artefact")
