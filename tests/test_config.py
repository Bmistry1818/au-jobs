from __future__ import annotations

import json
from pathlib import Path

import pytest

from aujobs.config import major_group, read_json, write_json_atomic


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("1341", "Managers"),
        ("2611", "Professionals"),
        ("3222", "Technicians and Trades Workers"),
        ("5511", "Clerical and Administrative Workers"),
        ("8219", "Labourers"),
        (" 2544 ", "Professionals"),
        ("9999", "Other"),
        ("", "Other"),
    ],
)
def test_major_group(code: str, expected: str) -> None:
    assert major_group(code) == expected


def test_write_json_atomic_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.json"
    write_json_atomic(target, {"a": 1, "b": ["x"]})

    assert read_json(target) == {"a": 1, "b": ["x"]}
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_write_json_atomic_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json_atomic(target, {"a": 1})
    write_json_atomic(target, {"a": 2})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]
    assert read_json(target) == {"a": 2}


def test_write_json_atomic_does_not_clobber_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json_atomic(target, {"good": True})

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(target, {"bad": Unserialisable()})

    # The previous good file survives, and the temp file is cleaned up.
    assert read_json(target) == {"good": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


def test_read_json_reports_bad_json(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        read_json(target)


def test_write_json_atomic_is_utf8(tmp_path: Path) -> None:
    target = tmp_path / "utf8.json"
    write_json_atomic(target, {"title": "Café Workers"})

    assert json.loads(target.read_text(encoding="utf-8"))["title"] == "Café Workers"
