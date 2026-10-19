from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aujobs import demand
from aujobs.extract import ExtractError


def _months() -> list[pd.Timestamp]:
    """June 2025 to June 2026: thirteen monthly columns."""
    return [pd.Timestamp(2025, month, 1) for month in range(6, 13)] + [
        pd.Timestamp(2026, month, 1) for month in range(1, 7)
    ]


def _write_ivi(path: Path) -> Path:
    frame = pd.DataFrame(
        [
            ["2611", "ICT Business and Systems Analysts", *([100] * 12), 90],
            ["26", "ICT Professionals", *([500] * 13)],
        ],
        columns=["ANZSCO code", "Occupation", *_months()],
    )
    frame.to_excel(path, index=False)
    return path


def test_parse_reports_latest_month_and_twelve_month_change(tmp_path: Path) -> None:
    snapshot = demand.parse_ivi_workbook(_write_ivi(tmp_path / "ivi.xlsx"))

    rows = {row.code: row for row in snapshot.rows}
    assert snapshot.as_of == "2026-06"
    assert rows["2611"].ads_latest == 90
    assert rows["2611"].change_12m_pct == pytest.approx(-10.0)
    assert rows["2611"].ads_12m_avg == pytest.approx((100 * 11 + 90) / 12)
    assert rows["26"].change_12m_pct == pytest.approx(0.0)


def test_parse_rejects_a_workbook_without_an_anzsco_column(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    pd.DataFrame([["Analysts", 1]], columns=["Occupation", "Count"]).to_excel(path, index=False)

    with pytest.raises(ExtractError, match="no ANZSCO"):
        demand.parse_ivi_workbook(path)


def test_bare_numbers_are_not_read_as_months() -> None:
    # A column header of 2611 must not be mistaken for the year 2611.
    assert demand._as_month(2611) is None
    assert demand._as_month("2611") is None
    assert demand._as_month("Jun 2026") == pd.Timestamp(2026, 6, 1)


def test_match_prefers_the_most_specific_code() -> None:
    rows = [demand.DemandRow("2611", 1.0, None, None), demand.DemandRow("26", 2.0, None, None)]

    matches = demand.match_to_occupations(["2611", "2612"], rows)

    assert matches["2611"][0] == "2611"
    assert matches["2612"][0] == "26"


def test_match_falls_back_to_a_group_row_and_ignores_unrelated_codes() -> None:
    rows = [demand.DemandRow("25", 3.0, None, None)]

    matches = demand.match_to_occupations(["2544", "3222"], rows)

    assert matches["2544"][0] == "25"
    assert "3222" not in matches


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    snapshot = demand.DemandSnapshot(
        as_of="2026-06",
        rows=(demand.DemandRow("2611", 90.0, 99.2, -10.0),),
        source_url="https://example.test/ivi.xlsx",
    )
    path = tmp_path / "demand.json"

    demand.write_demand(snapshot, path)

    assert demand.load_demand(path) == snapshot
