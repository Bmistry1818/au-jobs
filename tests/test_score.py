from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest

from aujobs import score
from aujobs.config import write_json_atomic


class FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason


class FakeMessages:
    """Records the requests made and replays scripted responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, FakeResponse)
        return result


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = FakeMessages(responses)


def _bad_request() -> anthropic.BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/"))
    return anthropic.BadRequestError(
        "output_config.format is not supported on this model",
        response=response,
        body=None,
    )


OCC = score.Occupation(anzsco_code="2611", title="ICT Business and Systems Analysts")


def test_score_parses_a_valid_response() -> None:
    client = FakeClient([FakeResponse('{"score": 8, "rationale": "Mostly desk work."}')])

    result = score.Scorer(client).score(OCC)  # type: ignore[arg-type]

    assert result == score.Score(title=OCC.title, score=8, rationale="Mostly desk work.")


def test_score_requests_structured_output_by_default() -> None:
    client = FakeClient([FakeResponse('{"score": 3, "rationale": "Hands-on."}')])

    score.Scorer(client).score(OCC)  # type: ignore[arg-type]

    output_config = client.messages.calls[0]["output_config"]
    assert output_config["format"]["schema"] == score.SCORE_SCHEMA
    assert output_config["effort"] == score.DEFAULT_EFFORT


def test_score_tolerates_a_markdown_fence() -> None:
    client = FakeClient([FakeResponse('```json\n{"score": 5, "rationale": "Mixed."}\n```')])

    assert score.Scorer(client).score(OCC).score == 5  # type: ignore[arg-type]


def test_score_falls_back_when_structured_outputs_unsupported() -> None:
    client = FakeClient([_bad_request(), FakeResponse('{"score": 2, "rationale": "Manual."}')])
    scorer = score.Scorer(client, model="some-older-model")  # type: ignore[arg-type]

    assert scorer.score(OCC).score == 2
    assert "format" in client.messages.calls[0]["output_config"]
    assert "format" not in client.messages.calls[1]["output_config"]


def test_score_raises_when_the_request_is_rejected_twice() -> None:
    client = FakeClient([_bad_request(), _bad_request()])
    scorer = score.Scorer(client)  # type: ignore[arg-type]

    with pytest.raises(score.ScoringError, match="API rejected"):
        scorer.score(OCC)


def test_score_surfaces_a_refusal() -> None:
    client = FakeClient([FakeResponse("", stop_reason="refusal")])

    with pytest.raises(score.ScoringError, match="declined"):
        score.Scorer(client).score(OCC)  # type: ignore[arg-type]


def test_score_surfaces_truncation() -> None:
    client = FakeClient([FakeResponse('{"score": 8,', stop_reason="max_tokens")])

    with pytest.raises(score.ScoringError, match="truncated"):
        score.Scorer(client).score(OCC)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    ["not json at all", '{"rationale": "no score"}', '{"score": 42, "rationale": "too high"}'],
)
def test_score_rejects_unusable_payloads(payload: str) -> None:
    client = FakeClient([FakeResponse(payload)])

    with pytest.raises(score.ScoringError):
        score.Scorer(client).score(OCC)  # type: ignore[arg-type]


def test_load_occupations_skips_rows_without_a_code_or_title(tmp_path: Path) -> None:
    path = tmp_path / "occupations.json"
    write_json_atomic(
        path,
        [
            {"anzsco_code": "2611", "title": "Analysts"},
            {"anzsco_code": "", "title": "Nameless"},
            {"anzsco_code": "3222", "title": ""},
        ],
    )

    assert score.load_occupations(path) == [score.Occupation("2611", "Analysts")]


def test_load_occupations_requires_the_extract_stage(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="aujobs extract"):
        score.load_occupations(tmp_path / "missing.json")


def test_load_existing_scores_drops_unscored_entries(tmp_path: Path) -> None:
    path = tmp_path / "scores.json"
    write_json_atomic(
        path,
        {
            "2611": {"title": "Analysts", "score": 8, "rationale": "ok"},
            "2544": {"title": "Nurses", "score": None, "rationale": "parse_error: ..."},
            "3222": "garbage",
        },
    )

    assert list(score.load_existing_scores(path)) == ["2611"]


def test_run_scores_only_missing_occupations(tmp_path: Path) -> None:
    occupations = tmp_path / "occupations.json"
    scores = tmp_path / "scores.json"
    write_json_atomic(
        occupations,
        [
            {"anzsco_code": "2611", "title": "Analysts"},
            {"anzsco_code": "3222", "title": "Electricians"},
        ],
    )
    write_json_atomic(scores, {"2611": {"title": "Analysts", "score": 8, "rationale": "prior"}})
    client = FakeClient([FakeResponse('{"score": 2, "rationale": "Physical work."}')])

    result = score.run(
        occupations_path=occupations,
        scores_path=scores,
        concurrency=1,
        client=client,  # type: ignore[arg-type]
    )

    assert len(client.messages.calls) == 1
    assert result["2611"]["rationale"] == "prior"
    assert result["3222"]["score"] == 2
    assert json.loads(scores.read_text(encoding="utf-8"))["3222"]["score"] == 2


def test_run_keeps_going_when_one_occupation_fails(tmp_path: Path) -> None:
    occupations = tmp_path / "occupations.json"
    scores = tmp_path / "scores.json"
    write_json_atomic(
        occupations,
        [
            {"anzsco_code": "2611", "title": "Analysts"},
            {"anzsco_code": "3222", "title": "Electricians"},
        ],
    )
    client = FakeClient([FakeResponse("nonsense"), FakeResponse('{"score": 2, "rationale": "x"}')])

    result = score.run(
        occupations_path=occupations,
        scores_path=scores,
        concurrency=1,
        client=client,  # type: ignore[arg-type]
    )

    # One succeeded; the failure is not persisted, so a re-run retries it.
    assert len(result) == 1


def test_run_aborts_immediately_on_an_auth_error(tmp_path: Path) -> None:
    occupations = tmp_path / "occupations.json"
    scores = tmp_path / "scores.json"
    write_json_atomic(
        occupations,
        [{"anzsco_code": f"26{i:02d}", "title": f"Job {i}"} for i in range(5)],
    )
    auth_error = anthropic.AuthenticationError(
        "invalid x-api-key",
        response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/")),
        body=None,
    )
    client = FakeClient([auth_error] * 5)

    with pytest.raises(anthropic.AuthenticationError):
        score.run(
            occupations_path=occupations,
            scores_path=scores,
            concurrency=1,
            client=client,  # type: ignore[arg-type]
        )

    # Stopped after the first failure rather than retrying all five,
    # and the (empty) checkpoint was still written.
    assert len(client.messages.calls) == 1
    assert scores.exists()


def test_run_is_a_no_op_when_everything_is_scored(tmp_path: Path) -> None:
    occupations = tmp_path / "occupations.json"
    scores = tmp_path / "scores.json"
    write_json_atomic(occupations, [{"anzsco_code": "2611", "title": "Analysts"}])
    write_json_atomic(scores, {"2611": {"title": "Analysts", "score": 8, "rationale": "prior"}})
    client = FakeClient([])

    score.run(
        occupations_path=occupations,
        scores_path=scores,
        client=client,  # type: ignore[arg-type]
    )

    assert client.messages.calls == []


def test_run_honours_limit(tmp_path: Path) -> None:
    occupations = tmp_path / "occupations.json"
    write_json_atomic(
        occupations,
        [{"anzsco_code": f"26{i:02d}", "title": f"Job {i}"} for i in range(5)],
    )
    client = FakeClient([FakeResponse('{"score": 4, "rationale": "x"}') for _ in range(5)])

    result = score.run(
        occupations_path=occupations,
        scores_path=tmp_path / "scores.json",
        concurrency=1,
        limit=2,
        client=client,  # type: ignore[arg-type]
    )

    assert len(result) == 2
