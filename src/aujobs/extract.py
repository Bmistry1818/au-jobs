"""Stage 1 — download and parse Jobs and Skills Australia (JSA) workbooks.

Produces ``data/occupations.json`` and ``data/occupations.csv``, one row per
ANZSCO 4-digit occupation (unit group).

The ANZSCO occupation workbook is split into one table per topic (employment,
earnings, qualifications, tasks), and the projections workbook is split the same
way. Each table is found by its headings, so the parser does not depend on sheet
names or positions. Each table is then joined on the 4-digit code.

Two things about the JSA source shape the design here:

* JSA renames the ``.xlsx`` downloads with each release (adding the release
  month), so the download URL is discovered from the data page at runtime.
  Where a page links several workbooks (the OSCA and ANZSCO files share one page),
  the link is chosen by name.
* Column headers and table layouts change between releases. If a future release
  breaks the parser, :func:`parse_occupation_profiles` and
  :func:`parse_employment_projections` are the two functions to fix.

Requires outbound network access to ``jobsandskills.gov.au``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from aujobs import manifest
from aujobs.config import (
    EMPLOYMENT_PROJECTIONS_PAGE,
    JSA_HOST,
    OCCUPATION_PROFILES_PAGE,
    OCCUPATIONS_CSV,
    OCCUPATIONS_JSON,
    RAW_DIR,
    USER_AGENT,
    write_json_atomic,
)

log = logging.getLogger(__name__)

PAGE_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120
#: How many leading rows to scan when looking for a table's header row.
HEADER_SCAN_ROWS = 12
#: ANZSCO 4-digit unit-group codes. Anything else (major, sub-major and minor groups,
#: six-digit occupations, footnotes) is dropped.
ANZSCO_CODE_RE = re.compile(r"^\d{4}$")

OUTPUT_COLUMNS = [
    "anzsco_code",
    "title",
    "employment",
    "median_earnings",
    "education",
    "growth_5yr_pct",
    "growth_10yr_pct",
    "description",
]


class ExtractError(RuntimeError):
    """Raised when a JSA page or workbook cannot be parsed as expected."""


def build_session() -> requests.Session:
    """A requests session that retries idempotent failures with backoff."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def find_xlsx_link(
    session: requests.Session, page_url: str, must_contain: str | None = None
) -> str:
    """Return the first ``.xlsx`` download link on a JSA data page.

    ``must_contain`` (case-insensitive) selects one workbook when a page links several.
    The OSCA and ANZSCO occupation files share a page, so the pipeline asks for ANZSCO
    by name rather than taking whichever file comes first.

    The resolved URL is checked against the JSA host so a redirect or an edited
    page cannot point the downloader at an arbitrary third-party origin.
    """
    response = session.get(page_url, timeout=PAGE_TIMEOUT)
    response.raise_for_status()

    for match in re.finditer(r"""href=["']([^"']+?\.xlsx)["']""", response.text, re.IGNORECASE):
        url = urljoin(page_url, str(match.group(1)))
        if must_contain and must_contain.lower() not in url.lower():
            continue
        host = urlparse(url).netloc.lower()
        if host == JSA_HOST or host.endswith(f".{JSA_HOST}"):
            return url
        log.warning("Ignoring off-site .xlsx link on %s: %s", page_url, url)

    wanted = f" containing {must_contain!r}" if must_contain else ""
    raise ExtractError(
        f"No .xlsx download link{wanted} found on {page_url}. "
        "JSA may have changed the page structure — check the URL in aujobs/config.py."
    )


def download(session: requests.Session, url: str, dest: Path) -> Path:
    """Stream a file to ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s", url)
    with session.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=65_536):
                handle.write(chunk)
    log.info("Wrote %s (%s bytes)", dest, f"{dest.stat().st_size:,}")
    return dest


def _find_header_row(frame: pd.DataFrame, keywords: Sequence[str]) -> int | None:
    """Index of the first row that mentions "anzsco" and every keyword."""
    for index in range(len(frame)):
        cells = " | ".join(str(value).lower() for value in frame.iloc[index])
        if "anzsco" in cells and all(keyword in cells for keyword in keywords):
            return index
    return None


def _find_table(xlsx_path: Path, *keywords: str) -> pd.DataFrame:
    """Return the largest sheet whose header row mentions "anzsco" and every keyword.

    The workbook has one table per topic, so each parser asks for the table it needs
    by its headings rather than by sheet name.
    """
    best: pd.DataFrame | None = None
    best_sheet = ""
    with pd.ExcelFile(xlsx_path) as workbook:
        for sheet in workbook.sheet_names:
            preview = pd.read_excel(workbook, sheet_name=sheet, header=None, nrows=HEADER_SCAN_ROWS)
            header_row = _find_header_row(preview, keywords)
            if header_row is None:
                continue
            frame = pd.read_excel(workbook, sheet_name=sheet, header=header_row)
            if best is None or len(frame) > len(best):
                best, best_sheet = frame, str(sheet)

    if best is None:
        wanted = " and ".join(["'anzsco'", *(f"'{keyword}'" for keyword in keywords)])
        raise ExtractError(
            f"No table in {xlsx_path.name} has a header row mentioning {wanted}. "
            "The workbook layout has probably changed."
        )
    log.debug("Using sheet %r from %s (%d rows)", best_sheet, xlsx_path.name, len(best))
    return best


def _find_column(frame: pd.DataFrame, *keywords: str) -> str | None:
    """First column whose lowercased name contains every keyword."""
    for column in frame.columns:
        name = str(column).strip().lower()
        if all(keyword in name for keyword in keywords):
            return str(column)
    return None


def _require_column(frame: pd.DataFrame, label: str, *keywords: str) -> str:
    column = _find_column(frame, *keywords)
    if column is None:
        available = ", ".join(str(c) for c in frame.columns[:20])
        raise ExtractError(
            f"Could not find a {label} column (keywords: {' + '.join(keywords)}). "
            f"Columns present: {available}"
        )
    return column


def _to_numeric(series: pd.Series) -> pd.Series:
    """Coerce a column that may contain ``$``, ``%``, commas or ``na`` markers."""
    cleaned = (
        series.astype("string")
        .str.replace(r"[,$%\s]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # (1.2) accounting negatives
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _normalise_codes(series: pd.Series) -> pd.Series:
    """Render ANZSCO codes as strings (``2611.0`` -> ``2611``)."""
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\s+", "", regex=True)
    )


def _keep_valid_codes(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame["anzsco_code"].str.fullmatch(ANZSCO_CODE_RE).fillna(False).astype(bool)
    dropped = int((~valid).sum())
    if dropped:
        log.debug("Dropped %d rows without a 4-digit ANZSCO code", dropped)
    return frame.loc[valid].copy()


def _level_label(name: object) -> str | None:
    """Turn a qualification column header such as ``"Bachelor degree\\n(%)"`` into a label."""
    if not isinstance(name, str):
        return None
    cleaned = re.sub(r"\(%\)", "", name.replace("\n", " "))
    return re.sub(r"\s+", " ", cleaned).strip(" /") or None


def _education_per_occupation(xlsx_path: Path) -> pd.DataFrame:
    """The most common qualification level per occupation, from the share table."""
    table = _find_table(xlsx_path, "bachelor")
    code_col = _require_column(table, "ANZSCO code", "anzsco")
    title_col = _find_column(table, "occupation")
    level_cols = [column for column in table.columns if column not in {code_col, title_col}]
    shares = pd.DataFrame(
        {column: _to_numeric(table[column]) for column in level_cols}, index=table.index
    )
    top_level = shares.fillna(-1).idxmax(axis=1).where(shares.max(axis=1).notna())
    frame = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(table[code_col]),
            "education": top_level.map(_level_label),
        }
    )
    frame = _keep_valid_codes(frame)
    return frame.drop_duplicates(subset="anzsco_code", keep="first")


def _tasks_per_occupation(xlsx_path: Path) -> pd.DataFrame:
    """All task statements for an occupation, joined into one text field.

    The tasks table has one row per task, so an occupation can span many rows.
    """
    table = _find_table(xlsx_path, "tasks")
    code_col = _require_column(table, "ANZSCO code", "anzsco")
    tasks_col = _require_column(table, "tasks", "tasks")
    frame = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(table[code_col]),
            "task": table[tasks_col].astype("string").str.strip(),
        }
    )
    frame = _keep_valid_codes(frame.dropna(subset=["task"]))
    grouped = frame.groupby("anzsco_code", sort=False)["task"].agg(
        lambda tasks: "; ".join(dict.fromkeys(str(task) for task in tasks))
    )
    return grouped.rename("description").reset_index()


def parse_occupation_profiles(xlsx_path: Path) -> pd.DataFrame:
    """Join the workbook's topic tables into one row per 4-digit occupation.

    Employment, weekly earnings and the occupation title come from the table with
    "Employed". The most common qualification comes from the table with "Bachelor",
    and the task text from the table with "Tasks".
    """
    summary = _find_table(xlsx_path, "employed")
    code_col = _require_column(summary, "ANZSCO code", "anzsco")
    title_col = _require_column(summary, "occupation title", "occupation")
    employment_col = _require_column(summary, "employment", "employed")
    earnings_col = _find_column(summary, "median", "weekly", "earn")

    base = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(summary[code_col]),
            "title": summary[title_col].astype("string").str.strip(),
            "employment": _to_numeric(summary[employment_col]),
            "median_earnings": (_to_numeric(summary[earnings_col]) if earnings_col else pd.NA),
        }
    )
    base = _keep_valid_codes(base.dropna(subset=["title"]))
    base = base.drop_duplicates(subset="anzsco_code", keep="first")

    return base.merge(_education_per_occupation(xlsx_path), on="anzsco_code", how="left").merge(
        _tasks_per_occupation(xlsx_path), on="anzsco_code", how="left"
    )


def _percent_after(table: pd.DataFrame, column: str) -> pd.Series:
    """The percentage column JSA places directly after a change-level column.

    The percentages are stored as fractions (0.0766 means 7.66%), so they are scaled.
    """
    position = list(table.columns).index(column)
    if position + 1 >= len(table.columns):
        return pd.Series(pd.NA, index=table.index, dtype="Float64")
    return _to_numeric(table.iloc[:, position + 1]) * 100


def parse_employment_projections(xlsx_path: Path) -> pd.DataFrame:
    """Parse 5-year and 10-year projected employment growth per unit group.

    Only rows that are not "not further defined" (NFD) are kept, so each unit group
    appears once.
    """
    table = _find_table(xlsx_path, "5-year change")
    code_col = _require_column(table, "ANZSCO code", "anzsco")
    five_year_col = _require_column(table, "5-year change", "5-year change")
    ten_year_col = _require_column(table, "10-year change", "10-year change")

    frame = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(table[code_col]),
            "growth_5yr_pct": _percent_after(table, five_year_col),
            "growth_10yr_pct": _percent_after(table, ten_year_col),
        }
    )
    nfd_col = _find_column(table, "nfd")
    if nfd_col is not None:
        is_defined = table[nfd_col].astype("string").str.strip().str.upper().eq("N")
        frame = frame[is_defined.fillna(False).astype(bool)]
    frame = _keep_valid_codes(frame)
    return frame.drop_duplicates(subset="anzsco_code", keep="first")


def merge(profiles: pd.DataFrame, projections: pd.DataFrame) -> pd.DataFrame:
    """Left-join projections onto profiles and order the output columns."""
    merged = profiles.merge(projections, on="anzsco_code", how="left")
    for column in OUTPUT_COLUMNS:
        if column not in merged.columns:
            merged[column] = pd.NA
    return merged[OUTPUT_COLUMNS].sort_values("anzsco_code").reset_index(drop=True)


def to_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    """DataFrame -> JSON-safe records, with ``NaN``/``NA`` rendered as ``null``."""
    records: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        record: dict[str, object] = {}
        for key, value in row.items():
            record[str(key)] = None if pd.isna(value) else value
        records.append(record)
    return records


def write_outputs(
    frame: pd.DataFrame,
    json_path: Path = OCCUPATIONS_JSON,
    csv_path: Path = OCCUPATIONS_CSV,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(csv_path, index=False)
    write_json_atomic(json_path, to_records(frame))
    log.info("Wrote %s and %s (%d occupations)", json_path, csv_path, len(frame))


def run(
    *,
    raw_dir: Path = RAW_DIR,
    json_path: Path = OCCUPATIONS_JSON,
    csv_path: Path = OCCUPATIONS_CSV,
    offline_profiles: Path | None = None,
    offline_projections: Path | None = None,
) -> pd.DataFrame:
    """Run the extract stage end to end.

    Pass ``offline_profiles`` / ``offline_projections`` to parse workbooks that
    are already on disk instead of downloading them.
    """
    if offline_profiles and offline_projections:
        profiles_path, projections_path = offline_profiles, offline_projections
    else:
        session = build_session()
        log.info("Locating current JSA download links")
        # The profiles page also links the OSCA (2021 Census) workbook; this pipeline is ANZSCO.
        profiles_url = find_xlsx_link(session, OCCUPATION_PROFILES_PAGE, must_contain="anzsco")
        projections_url = find_xlsx_link(session, EMPLOYMENT_PROJECTIONS_PAGE)
        profiles_path = download(session, profiles_url, raw_dir / "occupation_profiles.xlsx")
        projections_path = download(
            session, projections_url, raw_dir / "employment_projections.xlsx"
        )
        # Record each download with its checksum so published figures trace to an exact file.
        manifest_path = raw_dir / "manifest.json"
        manifest.record(
            "occupation_profiles", profiles_path, profiles_url, manifest_path=manifest_path
        )
        manifest.record(
            "employment_projections",
            projections_path,
            projections_url,
            manifest_path=manifest_path,
        )

    profiles = parse_occupation_profiles(profiles_path)
    log.info("Parsed %d occupation profiles", len(profiles))
    projections = parse_employment_projections(projections_path)
    log.info("Parsed %d projection rows", len(projections))

    merged = merge(profiles, projections)
    write_outputs(merged, json_path=json_path, csv_path=csv_path)
    return merged


__all__: Sequence[str] = (
    "JSA_HOST",
    "ExtractError",
    "build_session",
    "find_xlsx_link",
    "merge",
    "parse_employment_projections",
    "parse_occupation_profiles",
    "run",
    "to_records",
)
