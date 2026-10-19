"""Schema checks for every artefact the pipeline reads or writes.

Each check returns a list of human-readable problems. An empty list means the
artefact is valid. ``aujobs validate`` runs them all, and ``aujobs build`` runs
the input checks before it writes anything.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ANZSCO_RE = re.compile(r"\d{4}")
_CODE_RE = re.compile(r"\d{1,4}")
_MONTH_RE = re.compile(r"\d{4}-\d{2}")
_MAX_REPORTED = 20


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def occupation_problems(rows: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        code = str(row.get("anzsco_code") or "")
        where = f"occupations[{index}] ({code or 'no code'})"
        if not _ANZSCO_RE.fullmatch(code):
            problems.append(f"{where}: anzsco_code must be 4 digits")
        elif code in seen:
            problems.append(f"{where}: duplicate anzsco_code")
        seen.add(code)
        if not str(row.get("title") or "").strip():
            problems.append(f"{where}: missing title")
        employment = row.get("employment")
        if employment is not None and (not _is_number(employment) or employment < 0):
            problems.append(f"{where}: employment must be a non-negative number or null")
    return problems


def score_problems(scores: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    for code, entry in scores.items():
        where = f"scores[{code}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: entry must be an object")
            continue
        score = entry.get("score")
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 10:
            problems.append(f"{where}: score must be an integer from 0 to 10")
        if not isinstance(entry.get("rationale"), str):
            problems.append(f"{where}: rationale must be a string")
    return problems


def demand_problems(payload: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    as_of = str(payload.get("as_of") or "")
    if not _MONTH_RE.fullmatch(as_of):
        problems.append(f"demand: as_of must look like YYYY-MM, got {as_of!r}")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return [*problems, "demand: rows must be a list"]
    for index, row in enumerate(rows):
        where = f"demand.rows[{index}]"
        if not _CODE_RE.fullmatch(str(row.get("code") or "")):
            problems.append(f"{where}: code must be 1 to 4 digits")
        ads = row.get("ads_latest")
        if not _is_number(ads) or ads < 0:
            problems.append(f"{where}: ads_latest must be a non-negative number")
    return problems


def site_problems(data: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return ["site data: meta is missing"]
    for key in ("total_occupations", "scored_occupations", "sample"):
        if key not in meta:
            problems.append(f"site data: meta.{key} is missing")
    occupations = data.get("occupations")
    if not isinstance(occupations, list):
        return [*problems, "site data: occupations must be a list"]
    for index, row in enumerate(occupations):
        exposure = row.get("ai_exposure")
        if exposure is not None and (not _is_number(exposure) or not 0 <= exposure <= 10):
            problems.append(f"site data: occupations[{index}].ai_exposure must be 0 to 10 or null")
    return problems


def raise_if_invalid(problems: list[str], what: str) -> None:
    """Raise ``ValueError`` listing the first problems, if there are any."""
    if not problems:
        return
    shown = problems[:_MAX_REPORTED]
    extra = len(problems) - len(shown)
    message = f"{what} failed validation:\n  " + "\n  ".join(shown)
    if extra:
        message += f"\n  ...and {extra} more"
    raise ValueError(message)
