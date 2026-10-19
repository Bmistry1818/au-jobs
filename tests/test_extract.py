from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from aujobs import extract


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, url: str, **_: Any) -> FakeResponse:
        return FakeResponse(self._text)


PAGE = "https://www.jobsandskills.gov.au/data/occupation-and-industry-profiles"


def test_find_xlsx_link_resolves_relative_urls() -> None:
    session = FakeSession('<a href="/sites/default/files/profiles_may_2025.xlsx">Download</a>')

    url = extract.find_xlsx_link(session, PAGE)  # type: ignore[arg-type]

    assert url == "https://www.jobsandskills.gov.au/sites/default/files/profiles_may_2025.xlsx"


def test_find_xlsx_link_rejects_offsite_downloads() -> None:
    session = FakeSession('<a href="https://evil.example.com/payload.xlsx">Download</a>')

    with pytest.raises(extract.ExtractError, match=r"No \.xlsx download link"):
        extract.find_xlsx_link(session, PAGE)  # type: ignore[arg-type]


def test_find_xlsx_link_errors_when_page_has_no_workbook() -> None:
    session = FakeSession("<html><body>No downloads here</body></html>")

    with pytest.raises(extract.ExtractError, match="changed the page structure"):
        extract.find_xlsx_link(session, PAGE)  # type: ignore[arg-type]


# ---- synthetic workbooks laid out like the JSA ANZSCO release -----------------------

PREAMBLE = [[None, None, None]] * 6  # JSA puts title rows above each table header.


def _write_tables(path: Path, tables: dict[str, list[list[Any]]]) -> Path:
    with pd.ExcelWriter(path) as writer:
        for name, rows in tables.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name, header=False, index=False)
    return path


def _profiles_tables(*, include_tasks: bool = True) -> dict[str, list[list[Any]]]:
    tables: dict[str, list[list[Any]]] = {
        "Contents": [["Contents"]],
        "Table_1": [
            *PREAMBLE,
            ["ANZSCO Code", "Occupation", "Employed", "Median weekly earnings ($)"],
            ["1111", "Chief Executives", "63,000", 2100],
            ["1112", "General Managers", "69300", None],
            # Six-digit occupation and total row: both must be dropped.
            ["111111", "Chief Executive (six digit)", "1", 1],
            ["Total", "All occupations", "14000000", None],
        ],
        "Table_8": [
            *PREAMBLE,
            ["ANZSCO Code", "Occupation", "Bachelor degree\n(%)", "Year 12\n(%)"],
            ["1111", "Chief Executives", 60, 20],
            ["1112", "General Managers", 10, 80],
        ],
    }
    if include_tasks:
        tables["Table_3"] = [
            *PREAMBLE,
            ["ANZSCO Code", "Occupation", "Tasks"],
            ["1111", "Chief Executives", "Determining objectives"],
            ["1111", "Chief Executives", "Providing direction"],
            ["1111", "Chief Executives", "Determining objectives"],  # repeated: kept once
            ["1112", "General Managers", "Planning operations"],
        ]
    return tables


def _projections_tables() -> dict[str, list[list[Any]]]:
    return {
        "Table_6 Occupation Unit Group": [
            *PREAMBLE,
            ["Title", None, None, None, None, None, None, None, None],
            ["Units row", None, None, None, None, None, None, None, None],
            [
                "Occupation Level",
                "NFD Indicator",
                "ANZSCO Code",
                "Occupation",
                "Baseline",
                "5-Year Change",
                None,
                "10-Year Change",
                None,
            ],
            [None, None, None, None, "May 2025 ('000)", "Level ('000)", "%", "Level ('000)", "%"],
            [4, "N", 1111, "Chief Executives", 100.0, 7.5, 0.0766, 15.0, 0.1493],
            [4, "N", 1112, "General Managers", 200.0, -2.0, -0.01, 3.0, 0.0150],
            # Not further defined, and a six-digit code: both must be dropped.
            [2, "Y", 10, "Managers nfd", 6.0, 0.5, 0.0940, 1.0, 0.1698],
            [6, "N", 111111, "Chief Executive", 1.0, 0.0, 0.0, 0.0, 0.0],
        ],
    }


@pytest.fixture
def profiles_xlsx(tmp_path: Path) -> Path:
    return _write_tables(tmp_path / "profiles.xlsx", _profiles_tables())


@pytest.fixture
def projections_xlsx(tmp_path: Path) -> Path:
    return _write_tables(tmp_path / "projections.xlsx", _projections_tables())


def test_profiles_join_employment_earnings_education_and_tasks(profiles_xlsx: Path) -> None:
    parsed = extract.parse_occupation_profiles(profiles_xlsx).set_index("anzsco_code")

    rows = parsed.to_dict("index")
    assert list(rows) == ["1111", "1112"]
    assert rows["1111"]["title"] == "Chief Executives"
    assert rows["1111"]["employment"] == 63000  # "63,000" is coerced to a number
    assert rows["1111"]["median_earnings"] == 2100
    assert pd.isna(rows["1112"]["median_earnings"])
    assert rows["1111"]["education"] == "Bachelor degree"
    assert rows["1112"]["education"] == "Year 12"


def test_task_rows_are_joined_once_per_task(profiles_xlsx: Path) -> None:
    parsed = extract.parse_occupation_profiles(profiles_xlsx).set_index("anzsco_code")

    assert parsed.loc["1111", "description"] == "Determining objectives; Providing direction"
    assert parsed.loc["1112", "description"] == "Planning operations"


def test_projections_scale_fractions_and_drop_nfd_units_and_six_digit_rows(
    projections_xlsx: Path,
) -> None:
    parsed = extract.parse_employment_projections(projections_xlsx)

    assert list(parsed["anzsco_code"]) == ["1111", "1112"]
    indexed = parsed.set_index("anzsco_code")
    assert indexed.loc["1111", "growth_5yr_pct"] == pytest.approx(7.66)
    assert indexed.loc["1111", "growth_10yr_pct"] == pytest.approx(14.93)
    assert indexed.loc["1112", "growth_5yr_pct"] == pytest.approx(-1.0)


def test_missing_tasks_table_is_reported_by_name(tmp_path: Path) -> None:
    path = _write_tables(tmp_path / "no_tasks.xlsx", _profiles_tables(include_tasks=False))

    with pytest.raises(extract.ExtractError, match="'tasks'"):
        extract.parse_occupation_profiles(path)


def test_workbook_without_an_anzsco_table_is_reported(tmp_path: Path) -> None:
    path = _write_tables(tmp_path / "bad.xlsx", {"Sheet1": [["Job", "Count"], ["Nurse", 1]]})

    with pytest.raises(extract.ExtractError, match="'anzsco'"):
        extract.parse_occupation_profiles(path)


def test_run_offline_writes_both_outputs(
    tmp_path: Path, profiles_xlsx: Path, projections_xlsx: Path
) -> None:
    json_path = tmp_path / "out" / "occupations.json"
    csv_path = tmp_path / "out" / "occupations.csv"

    merged = extract.run(
        json_path=json_path,
        csv_path=csv_path,
        offline_profiles=profiles_xlsx,
        offline_projections=projections_xlsx,
    )

    assert list(merged.columns) == extract.OUTPUT_COLUMNS
    assert json_path.exists() and csv_path.exists()
    records = {r["anzsco_code"]: r for r in extract.to_records(merged)}
    assert set(records) == {"1111", "1112"}
    assert records["1112"]["growth_10yr_pct"] == pytest.approx(1.5)


def test_to_records_renders_missing_values_as_null() -> None:
    frame = pd.DataFrame({"anzsco_code": ["2611"], "median_earnings": [pd.NA]})

    assert extract.to_records(frame) == [{"anzsco_code": "2611", "median_earnings": None}]


def test_merge_left_joins_and_keeps_unmatched_occupations() -> None:
    profiles = pd.DataFrame(
        {
            "anzsco_code": ["2611", "9999"],
            "title": ["Analysts", "Mystery"],
            "employment": [1.0, 2.0],
            "median_earnings": [10.0, 20.0],
            "education": ["a", "b"],
        }
    )
    projections = pd.DataFrame(
        {"anzsco_code": ["2611"], "growth_5yr_pct": [1.0], "growth_10yr_pct": [2.0]}
    )

    merged = extract.merge(profiles, projections)

    assert len(merged) == 2
    assert pd.isna(merged.loc[merged["anzsco_code"] == "9999", "growth_5yr_pct"]).all()
