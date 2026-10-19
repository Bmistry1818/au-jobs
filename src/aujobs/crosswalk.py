"""ANZSCO to OSCA crosswalk (Phase 3).

OSCA (Occupation Standard Classification for Australia) is replacing ANZSCO. This
module does not invent a mapping. The maintainer supplies ``data/osca_crosswalk.csv``
from ABS's published concordance, and every row is checked here before use.

Expected columns: ``anzsco_code``, ``osca_code``. An ANZSCO code may appear once.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from aujobs.config import OSCA_CROSSWALK_CSV

_ANZSCO_RE = re.compile(r"\d{4}")
_REQUIRED_COLUMNS = {"anzsco_code", "osca_code"}


def load(path: Path = OSCA_CROSSWALK_CSV) -> dict[str, str]:
    """Return ``{anzsco_code: osca_code}``. Empty when no crosswalk file exists."""
    if not path.exists():
        return {}

    mapping: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing column(s): {', '.join(sorted(missing))}")

        for line_number, row in enumerate(reader, start=2):
            anzsco = (row.get("anzsco_code") or "").strip()
            osca = (row.get("osca_code") or "").strip()
            if not _ANZSCO_RE.fullmatch(anzsco):
                raise ValueError(f"{path}:{line_number}: ANZSCO code {anzsco!r} must be 4 digits")
            if not osca:
                raise ValueError(f"{path}:{line_number}: no OSCA code given for {anzsco}")
            if mapping.get(anzsco, osca) != osca:
                raise ValueError(
                    f"{path}:{line_number}: {anzsco} maps to both {mapping[anzsco]} and {osca}"
                )
            mapping[anzsco] = osca
    return mapping
