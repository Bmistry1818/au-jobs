from __future__ import annotations

from pathlib import Path

import pytest

from aujobs import crosswalk


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_gives_an_empty_mapping(tmp_path: Path) -> None:
    assert crosswalk.load(tmp_path / "absent.csv") == {}


def test_valid_file_loads(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.csv", "anzsco_code,osca_code\n2611,ABC123\n2544,DEF456\n")

    assert crosswalk.load(path) == {"2611": "ABC123", "2544": "DEF456"}


def test_rejects_a_non_four_digit_anzsco_code(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.csv", "anzsco_code,osca_code\n26,ABC123\n")

    with pytest.raises(ValueError, match="must be 4 digits"):
        crosswalk.load(path)


def test_rejects_an_empty_osca_code(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.csv", "anzsco_code,osca_code\n2611,\n")

    with pytest.raises(ValueError, match="no OSCA code"):
        crosswalk.load(path)


def test_rejects_conflicting_mappings_for_one_code(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.csv", "anzsco_code,osca_code\n2611,ABC123\n2611,ZZZ999\n")

    with pytest.raises(ValueError, match="maps to both"):
        crosswalk.load(path)


def test_rejects_a_missing_column(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.csv", "anzsco_code\n2611\n")

    with pytest.raises(ValueError, match="osca_code"):
        crosswalk.load(path)


def test_shipped_example_template_is_empty_but_valid() -> None:
    from aujobs.config import EXAMPLES_DIR

    assert crosswalk.load(EXAMPLES_DIR / "osca_crosswalk.example.csv") == {}
