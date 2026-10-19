from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
import requests

from aujobs import cli, extract, score
from aujobs.config import read_json, write_json_atomic


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert "aujobs" in capsys.readouterr().out


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2


def test_build_subcommand_end_to_end(
    tmp_path: Path,
    sample_occupations: list[dict[str, Any]],
    sample_scores: dict[str, dict[str, Any]],
) -> None:
    occupations_path = tmp_path / "occupations.json"
    scores_path = tmp_path / "scores.json"
    output_path = tmp_path / "data.json"
    write_json_atomic(occupations_path, sample_occupations)
    write_json_atomic(scores_path, sample_scores)

    exit_code = cli.main(
        [
            "build",
            "--occupations",
            str(occupations_path),
            "--scores",
            str(scores_path),
            "--output",
            str(output_path),
            "--sample",
        ]
    )

    assert exit_code == 0
    assert read_json(output_path)["meta"]["sample"] is True


def test_missing_inputs_exit_non_zero(tmp_path: Path) -> None:
    exit_code = cli.main(
        [
            "build",
            "--occupations",
            str(tmp_path / "nope.json"),
            "--scores",
            str(tmp_path / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        ]
    )

    assert exit_code == 1


def test_extract_requires_both_offline_workbooks(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="must be given together"):
        cli.main(["extract", "--profiles-xlsx", str(tmp_path / "a.xlsx")])


def test_network_failure_reports_a_readable_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(**_: Any) -> None:
        raise requests.exceptions.ConnectionError("read timed out")

    monkeypatch.setattr(extract, "run", boom)

    exit_code = cli.main(["extract"])

    assert exit_code == 1
    assert "jobsandskills.gov.au" in caplog.text
    assert "network-restricted sandbox" in caplog.text


def test_bad_api_key_reports_a_readable_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(**_: Any) -> None:
        raise anthropic.AuthenticationError(
            "invalid x-api-key",
            response=httpx.Response(401, request=httpx.Request("POST", "https://x/")),
            body=None,
        )

    monkeypatch.setattr(score, "run", boom)

    exit_code = cli.main(["score"])

    assert exit_code == 1
    assert "ANTHROPIC_API_KEY" in caplog.text
