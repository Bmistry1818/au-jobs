"""Demand signal from the Jobs and Skills Australia Internet Vacancy Index (IVI).

The IVI is a monthly count of online job advertisements by occupation. It counts
advertised demand on the job boards JSA monitors, not every vacancy, so it is used
as a momentum signal rather than a headcount.

Like the extract stage, the workbook is read by keyword: the header row and the
month columns are located rather than assumed. The parser has been tested only
against a synthetic workbook, so check it against the first real release.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd

from aujobs import manifest
from aujobs.config import (
    DEMAND_JSON,
    IVI_PAGE,
    MANIFEST_JSON,
    RAW_DIR,
    read_json,
    write_json_atomic,
)
from aujobs.extract import (
    ExtractError,
    _find_column,
    _normalise_codes,
    _to_numeric,
    build_session,
    download,
    find_xlsx_link,
)

log = logging.getLogger(__name__)

HEADER_SCAN_ROWS = 15
#: IVI rows can be a 4-digit occupation or a broader group. Groups are matched to
#: occupations by code prefix (see :func:`match_to_occupations`).
DEMAND_CODE_RE = re.compile(r"\d{1,4}")
MONTH_FORMAT = "%Y-%m"


@dataclass(frozen=True, slots=True)
class DemandRow:
    code: str
    ads_latest: float
    ads_12m_avg: float | None
    change_12m_pct: float | None


@dataclass(frozen=True, slots=True)
class DemandSnapshot:
    as_of: str
    rows: tuple[DemandRow, ...]
    source_url: str = ""
    synthetic: bool = False


def _find_ivi_header(frame: pd.DataFrame) -> int | None:
    for index in range(len(frame)):
        cells = [str(value).lower() for value in frame.iloc[index]]
        if any("anzsco" in cell or "occupation" in cell for cell in cells):
            return index
    return None


def _as_month(value: object) -> pd.Timestamp | None:
    """Interpret a column header as a month, or ``None`` if it is not one.

    Bare numbers are rejected on purpose: a header such as ``2611`` must not be
    read as a year.
    """
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).to_period("M").to_timestamp()
    if isinstance(value, str) and re.search(r"[A-Za-z]", value) and re.search(r"\d{4}", value):
        parsed = pd.to_datetime(value, errors="coerce")
        if not pd.isna(parsed):
            return pd.Timestamp(parsed).to_period("M").to_timestamp()
    return None


def _optional(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def parse_ivi_workbook(xlsx_path: Path) -> DemandSnapshot:
    """Parse an IVI workbook into the latest month's counts and 12-month change."""
    frame: pd.DataFrame | None = None
    with pd.ExcelFile(xlsx_path) as workbook:
        for sheet in workbook.sheet_names:
            preview = pd.read_excel(workbook, sheet_name=sheet, header=None, nrows=HEADER_SCAN_ROWS)
            header_row = _find_ivi_header(preview)
            if header_row is None:
                continue
            candidate = pd.read_excel(workbook, sheet_name=sheet, header=header_row)
            if frame is None or len(candidate) > len(frame):
                frame = candidate

    if frame is None:
        raise ExtractError(f"No sheet in {xlsx_path.name} has an ANZSCO or occupation header row.")

    code_column = _find_column(frame, "anzsco")
    if code_column is None:
        raise ExtractError(
            "The IVI workbook has no ANZSCO code column. Columns present: "
            + ", ".join(str(column) for column in frame.columns[:20])
        )

    months: dict[Any, pd.Timestamp] = {}
    for column in frame.columns:
        month = _as_month(column)
        if month is not None:
            months[column] = month
    if not months:
        raise ExtractError(f"No month columns found in {xlsx_path.name}.")

    ordered = sorted(months.items(), key=lambda item: item[1])
    latest_column, latest_month = ordered[-1]
    # The 12 months ending with the latest one, and the same month a year earlier.
    window_start = latest_month - pd.DateOffset(months=12)
    recent_columns = [column for column, month in ordered if month > window_start]
    prior_columns = [column for column, month in ordered if month == window_start]

    latest_values = _to_numeric(frame[latest_column])
    table = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(frame[code_column]),
            "ads_latest": latest_values,
            "ads_12m_avg": pd.concat(
                [_to_numeric(frame[column]) for column in recent_columns], axis=1
            ).mean(axis=1, skipna=True),
        }
    )
    if prior_columns:
        prior_values = _to_numeric(frame[prior_columns[0]])
        table["change_12m_pct"] = ((latest_values - prior_values) / prior_values * 100).where(
            prior_values > 0
        )
    else:
        table["change_12m_pct"] = pd.NA

    table = table.dropna(subset=["anzsco_code", "ads_latest"])
    table = table[table["anzsco_code"].str.fullmatch(DEMAND_CODE_RE).fillna(False).astype(bool)]
    table = table.drop_duplicates(subset="anzsco_code", keep="first")

    rows = tuple(
        DemandRow(
            code=str(row.anzsco_code),
            ads_latest=float(cast("float", row.ads_latest)),
            ads_12m_avg=_optional(row.ads_12m_avg),
            change_12m_pct=_optional(row.change_12m_pct),
        )
        for row in table.itertuples(index=False)
    )
    if not rows:
        raise ExtractError(f"No usable occupation rows in {xlsx_path.name}.")
    return DemandSnapshot(as_of=latest_month.strftime(MONTH_FORMAT), rows=rows)


def match_to_occupations(
    codes: Iterable[str], rows: Iterable[DemandRow]
) -> dict[str, tuple[str, DemandRow]]:
    """Map each occupation to its most specific demand row.

    A 4-digit occupation takes its own row if one exists, otherwise the row for its
    3-digit, 2-digit or 1-digit group. The returned key is the code that matched.
    """
    by_code = {row.code: row for row in rows}
    matches: dict[str, tuple[str, DemandRow]] = {}
    for code in codes:
        for length in range(len(code), 0, -1):
            row = by_code.get(code[:length])
            if row is not None:
                matches[code] = (code[:length], row)
                break
    return matches


def write_demand(snapshot: DemandSnapshot, path: Path = DEMAND_JSON) -> None:
    write_json_atomic(
        path,
        {
            "as_of": snapshot.as_of,
            "source_url": snapshot.source_url,
            "synthetic": snapshot.synthetic,
            "rows": [asdict(row) for row in snapshot.rows],
        },
    )


def load_demand(path: Path = DEMAND_JSON) -> DemandSnapshot:
    payload: Mapping[str, Any] = read_json(path)
    try:
        rows = tuple(DemandRow(**row) for row in payload["rows"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path} is not a valid demand file: {exc}") from exc
    return DemandSnapshot(
        as_of=str(payload["as_of"]),
        rows=rows,
        source_url=str(payload.get("source_url", "")),
        synthetic=bool(payload.get("synthetic", False)),
    )


def run(
    *,
    xlsx_path: Path | None = None,
    raw_dir: Path = RAW_DIR,
    output_path: Path = DEMAND_JSON,
    manifest_path: Path = MANIFEST_JSON,
) -> DemandSnapshot:
    """Fetch (or read a local copy of) the IVI workbook and write ``demand.json``."""
    if xlsx_path is None:
        session = build_session()
        source_url = find_xlsx_link(session, IVI_PAGE)
        xlsx_path = download(session, source_url, raw_dir / "internet_vacancy_index.xlsx")
        manifest.record(
            "internet_vacancy_index", xlsx_path, source_url, manifest_path=manifest_path
        )
    else:
        source_url = f"local file: {xlsx_path.name}"

    parsed = parse_ivi_workbook(xlsx_path)
    snapshot = DemandSnapshot(as_of=parsed.as_of, rows=parsed.rows, source_url=source_url)
    write_demand(snapshot, output_path)
    log.info("Wrote %s (%d rows, latest month %s)", output_path, len(snapshot.rows), snapshot.as_of)
    return snapshot


__all__: Sequence[str] = (
    "DemandRow",
    "DemandSnapshot",
    "load_demand",
    "match_to_occupations",
    "parse_ivi_workbook",
    "run",
    "write_demand",
)
