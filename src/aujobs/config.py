"""Paths, constants and shared helpers.

Every path is derived from :data:`PROJECT_ROOT` so the package behaves the same
whether it is run from the repository, an editable install, or a wheel with an
explicit ``--data-dir``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Final

# ``src/aujobs/config.py`` -> repository root
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

DATA_DIR: Final[Path] = Path(os.environ.get("AUJOBS_DATA_DIR", PROJECT_ROOT / "data"))
SITE_DIR: Final[Path] = Path(os.environ.get("AUJOBS_SITE_DIR", PROJECT_ROOT / "site"))

RAW_DIR: Final[Path] = DATA_DIR / "raw"
OCCUPATIONS_JSON: Final[Path] = DATA_DIR / "occupations.json"
OCCUPATIONS_CSV: Final[Path] = DATA_DIR / "occupations.csv"
SCORES_JSON: Final[Path] = DATA_DIR / "scores.json"
SITE_DATA_JSON: Final[Path] = SITE_DIR / "data.json"
DEMAND_JSON: Final[Path] = DATA_DIR / "demand.json"
MANIFEST_JSON: Final[Path] = RAW_DIR / "manifest.json"
OSCA_CROSSWALK_CSV: Final[Path] = DATA_DIR / "osca_crosswalk.csv"
EXAMPLES_DIR: Final[Path] = PROJECT_ROOT / "examples"

# Jobs and Skills Australia publishes these as .xlsx downloads whose filenames
# change with each release, so the download link is discovered at runtime.
JSA_HOST: Final[str] = "www.jobsandskills.gov.au"
JSA_BASE_URL: Final[str] = f"https://{JSA_HOST}"
OCCUPATION_PROFILES_PAGE: Final[str] = f"{JSA_BASE_URL}/data/occupation-and-industry-profiles"
EMPLOYMENT_PROJECTIONS_PAGE: Final[str] = f"{JSA_BASE_URL}/data/employment-projections"
IVI_PAGE: Final[str] = f"{JSA_BASE_URL}/work/internet-vacancy-index"

USER_AGENT: Final[str] = "au-jobs/0.1 (+https://github.com/bmistry1818/au-jobs)"

# ANZSCO major group, keyed on the first digit of the 4-digit occupation code.
MAJOR_GROUPS: Final[dict[str, str]] = {
    "1": "Managers",
    "2": "Professionals",
    "3": "Technicians and Trades Workers",
    "4": "Community and Personal Service Workers",
    "5": "Clerical and Administrative Workers",
    "6": "Sales Workers",
    "7": "Machinery Operators and Drivers",
    "8": "Labourers",
}
UNKNOWN_MAJOR_GROUP: Final[str] = "Other"

MIN_SCORE: Final[int] = 0
MAX_SCORE: Final[int] = 10


def major_group(anzsco_code: str) -> str:
    """Map an ANZSCO code to its major group name."""
    return MAJOR_GROUPS.get(str(anzsco_code).strip()[:1], UNKNOWN_MAJOR_GROUP)


def write_json_atomic(path: Path, payload: Any) -> None:
    """Serialise ``payload`` to ``path`` via a temp file + rename.

    Scoring checkpoints itself after every batch; a partial write during a crash
    would leave an unparseable ``scores.json`` and lose the whole run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file, raising a readable error if it is malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
