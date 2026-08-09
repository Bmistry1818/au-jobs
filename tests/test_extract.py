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


def test_find_xlsx_link_accepts_absolute_same_host_urls() -> None:
    href = "https://www.jobsandskills.gov.au/files/x.xlsx"
    session = FakeSession(f"<a href='{href}'>x</a>")

    assert extract.find_xlsx_link(session, PAGE) == href  # type: ignore[arg-type]


def test_find_xlsx_link_rejects_offsite_downloads() -> None:
    session = FakeSession('<a href="https://evil.example.com/payload.xlsx">Download</a>')

    with pytest.raises(extract.ExtractError, match=r"No \.xlsx download link"):
        extract.find_xlsx_link(session, PAGE)  # type: ignore[arg-type]


def test_find_xlsx_link_errors_when_page_has_no_workbook() -> None:
    session = FakeSession("<html><body>No downloads here</body></html>")

    with pytest.raises(extract.ExtractError, match="changed the page structure"):
        extract.find_xlsx_link(session, PAGE)  # type: ignore[arg-type]


def _write_workbook(
    path: Path, preamble_rows: int, header: list[str], rows: list[list[Any]]
) -> Path:
    """Write an .xlsx with junk rows above the header, as JSA workbooks have."""
    body = [["Some JSA preamble", None, None]] * preamble_rows + [header, *rows]
    frame = pd.DataFrame(body)
    frame.to_excel(path, index=False, header=False, sheet_name="Data")
    return path


@pytest.fixture
def profiles_xlsx(tmp_path: Path) -> Path:
    return _write_workbook(
        tmp_path / "profiles.xlsx",
        preamble_rows=3,
        header=[
            "ANZSCO Code",
            "Occupation Name",
            "Employment level (000s)",
            "Median weekly earnings",
            "Main education level",
        ],
        rows=[
            ["2611", "ICT Business and Systems Analysts", "89,000", "$2,100", "Bachelor's degree"],
            ["2544", "Registered Nurses", "345000", "1750", "Bachelor's degree"],
            # Major-group subtotal and a footnote row: both must be dropped.
            ["26", "ICT Professionals", "500000", "2000", "Bachelor's degree"],
            ["Total", "All occupations", "14000000", None, None],
            # Duplicate code: first occurrence wins.
            ["2611", "ICT Business and Systems Analysts (dup)", "1", "1", "x"],
        ],
    )


@pytest.fixture
def projections_xlsx(tmp_path: Path) -> Path:
    return _write_workbook(
        tmp_path / "projections.xlsx",
        preamble_rows=5,
        header=["ANZSCO code", "Five year projected growth (%)", "Ten year projected growth (%)"],
        rows=[
            ["2611", "12.5", "24.0"],
            ["2544", "(1.5)", "9.0"],
            ["26", "10.0", "20.0"],
        ],
    )


def test_parse_occupation_profiles(profiles_xlsx: Path) -> None:
    parsed = extract.parse_occupation_profiles(profiles_xlsx)

    assert list(parsed["anzsco_code"]) == ["2611", "2544"]
    assert parsed.loc[0, "title"] == "ICT Business and Systems Analysts"
    # "$2,100" and "89,000" are coerced to numbers.
    assert parsed.loc[0, "employment"] == 89000
    assert parsed.loc[0, "median_earnings"] == 2100


def test_parse_employment_projections_handles_accounting_negatives(projections_xlsx: Path) -> None:
    parsed = extract.parse_employment_projections(projections_xlsx)

    assert list(parsed["anzsco_code"]) == ["2611", "2544"]
    assert parsed.loc[0, "growth_5yr_pct"] == 12.5
    assert parsed.loc[1, "growth_5yr_pct"] == -1.5


def test_parse_rejects_workbook_without_anzsco_header(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "bad.xlsx", preamble_rows=1, header=["Job", "Count"], rows=[["Nurse", 1]]
    )

    with pytest.raises(extract.ExtractError, match="header row mentioning 'ANZSCO'"):
        extract.parse_occupation_profiles(path)


def test_parse_reports_missing_employment_column(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "no_employment.xlsx",
        preamble_rows=0,
        header=["ANZSCO Code", "Occupation Name", "Median weekly earnings"],
        rows=[["2611", "Analysts", "2100"]],
    )

    with pytest.raises(extract.ExtractError, match="employment column"):
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
    records = extract.to_records(merged)
    assert {r["anzsco_code"] for r in records} == {"2544", "2611"}
    assert records[0]["growth_10yr_pct"] == 9.0


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
