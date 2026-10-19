"""Stage 1 — download and parse Jobs and Skills Australia (JSA) workbooks.

Produces ``data/occupations.json`` and ``data/occupations.csv``, one row per
ANZSCO 4-digit occupation.

Two things about the JSA source shape the design here:

* JSA renames the ``.xlsx`` downloads with each release (adding the release
  month), so the download URL is discovered from the data page at runtime
  rather than hardcoded.
* Column headers vary between releases, so columns are located by keyword
  rather than exact match. If a future release renames things enough to break
  this, :func:`parse_occupation_profiles` and
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
#: How many leading rows to scan when looking for the header row of a sheet.
HEADER_SCAN_ROWS = 12
#: ANZSCO 4-digit occupation codes. Anything else (major/sub-major group
#: subtotals, "Total", footnotes) is dropped.
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


def find_xlsx_link(session: requests.Session, page_url: str) -> str:
    """Return the first ``.xlsx`` download link on a JSA data page.

    The resolved URL is checked against the JSA host so a redirect or an edited
    page cannot point the downloader at an arbitrary third-party origin.
    """
    response = session.get(page_url, timeout=PAGE_TIMEOUT)
    response.raise_for_status()

    for match in re.finditer(r"""href=["']([^"']+?\.xlsx)["']""", response.text, re.IGNORECASE):
        url = urljoin(page_url, str(match.group(1)))
        host = urlparse(url).netloc.lower()
        if host == JSA_HOST or host.endswith(f".{JSA_HOST}"):
            return url
        log.warning("Ignoring off-site .xlsx link on %s: %s", page_url, url)

    raise ExtractError(
        f"No .xlsx download link found on {page_url}. "
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


def _find_header_row(frame: pd.DataFrame) -> int | None:
    """Index of the first row that looks like a header (mentions "anzsco")."""
    for i in range(len(frame)):
        if any("anzsco" in str(value).lower() for value in frame.iloc[i]):
            return i
    return None


def _load_anzsco_sheet(xlsx_path: Path) -> pd.DataFrame:
    """Return the largest sheet in the workbook that has an ANZSCO header row."""
    with pd.ExcelFile(xlsx_path) as workbook:
        best: pd.DataFrame | None = None
        best_sheet = ""
        for sheet in workbook.sheet_names:
            preview = pd.read_excel(workbook, sheet_name=sheet, header=None, nrows=HEADER_SCAN_ROWS)
            header_row = _find_header_row(preview)
            if header_row is None:
                continue
            frame = pd.read_excel(workbook, sheet_name=sheet, header=header_row)
            if best is None or len(frame) > len(best):
                best, best_sheet = frame, str(sheet)

    if best is None:
        raise ExtractError(
            f"No sheet in {xlsx_path.name} has a header row mentioning 'ANZSCO'. "
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
    """Render ANZSCO codes as 4-character strings (``2611.0`` -> ``2611``)."""
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\s+", "", regex=True)
    )


def _keep_valid_codes(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame["anzsco_code"].str.fullmatch(ANZSCO_CODE_RE).fillna(False)
    dropped = int((~valid).sum())
    if dropped:
        log.debug("Dropped %d rows without a 4-digit ANZSCO code", dropped)
    return frame.loc[valid].copy()


def parse_occupation_profiles(xlsx_path: Path) -> pd.DataFrame:
    """Parse employment, median earnings and education level per occupation."""
    sheet = _load_anzsco_sheet(xlsx_path)

    code_col = _require_column(sheet, "ANZSCO code", "anzsco")
    title_col = _find_column(sheet, "occupation") or _find_column(sheet, "title")
    if title_col is None:
        raise ExtractError(
            "Could not find an occupation title column (keywords: 'occupation' or 'title'). "
            f"Columns present: {', '.join(str(c) for c in sheet.columns[:20])}"
        )
    employment_col = _require_column(sheet, "employment", "employ")
    earnings_col = _find_column(sheet, "median", "earn") or _find_column(sheet, "median", "income")
    education_col = _find_column(sheet, "education") or _find_column(sheet, "qualification")
    # Task text is the best input the scorer has; it is optional because the column
    # name varies between releases.
    description_col = _find_column(sheet, "task") or _find_column(sheet, "description")

    parsed = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(sheet[code_col]),
            "title": sheet[title_col].astype("string").str.strip(),
            "employment": _to_numeric(sheet[employment_col]),
            "median_earnings": (_to_numeric(sheet[earnings_col]) if earnings_col else pd.NA),
            "education": (
                sheet[education_col].astype("string").str.strip() if education_col else pd.NA
            ),
            "description": (
                sheet[description_col].astype("string").str.strip() if description_col else pd.NA
            ),
        }
    )
    parsed = parsed.dropna(subset=["anzsco_code", "title"])
    parsed = _keep_valid_codes(parsed)
    return parsed.drop_duplicates(subset="anzsco_code", keep="first")


def parse_employment_projections(xlsx_path: Path) -> pd.DataFrame:
    """Parse 5-year and 10-year projected employment growth per occupation."""
    sheet = _load_anzsco_sheet(xlsx_path)

    code_col = _require_column(sheet, "ANZSCO code", "anzsco")
    five_year_col = (
        _find_column(sheet, "five year")
        or _find_column(sheet, "5 year")
        or _find_column(sheet, "growth")
    )
    ten_year_col = _find_column(sheet, "ten year") or _find_column(sheet, "10 year")

    if five_year_col is None and ten_year_col is None:
        raise ExtractError(
            "Could not find a projected growth column in the projections workbook. "
            f"Columns present: {', '.join(str(c) for c in sheet.columns[:20])}"
        )

    parsed = pd.DataFrame(
        {
            "anzsco_code": _normalise_codes(sheet[code_col]),
            "growth_5yr_pct": (_to_numeric(sheet[five_year_col]) if five_year_col else pd.NA),
            "growth_10yr_pct": (_to_numeric(sheet[ten_year_col]) if ten_year_col else pd.NA),
        }
    )
    parsed = parsed.dropna(subset=["anzsco_code"])
    parsed = _keep_valid_codes(parsed)
    return parsed.drop_duplicates(subset="anzsco_code", keep="first")


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
        profiles_url = find_xlsx_link(session, OCCUPATION_PROFILES_PAGE)
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
