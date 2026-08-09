"""Stage 2 — score each occupation 0-10 on AI exposure via the Claude API.

Writes ``data/scores.json`` as ``{anzsco_code: {title, score, rationale}}``.

The run is resumable: already-scored occupations are skipped, progress is
checkpointed to disk, and failures are logged rather than persisted, so
re-running picks up exactly what is missing.

Needs ``ANTHROPIC_API_KEY`` in the environment (or a profile from
``ant auth login``); no access to jobsandskills.gov.au is required.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import anthropic

from aujobs.config import (
    MAX_SCORE,
    MIN_SCORE,
    OCCUPATIONS_JSON,
    SCORES_JSON,
    read_json,
    write_json_atomic,
)

log = logging.getLogger(__name__)

#: Claude Opus 5 is the default: it supports structured outputs, which removes
#: the JSON-parsing failure mode entirely. Override with ``--model`` (e.g.
#: ``claude-sonnet-5``) to trade some quality for cost on large runs.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "low"
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_TOKENS = 2048
#: Flush scores.json to disk after this many newly scored occupations.
CHECKPOINT_EVERY = 20
#: Errors that will affect every remaining occupation, so the run should stop.
FATAL_ERRORS = (anthropic.AuthenticationError, anthropic.PermissionDeniedError)

RUBRIC = """You are scoring Australian occupations on how much AI will reshape them
over the next 5-10 years, NOT whether the job disappears entirely.

Score 0-10 on a single "AI Exposure" axis:

0-1  Minimal   — physical/manual work AI can't touch (e.g. concreters, farm
                 workers, cleaners, roof tilers)
2-3  Low       — hands-on skilled trades and care work with AI-assisted tools
                 at the margins (e.g. electricians, plumbers, paramedics,
                 aged care workers)
4-5  Moderate  — judgement-heavy human-facing roles where AI assists but
                 doesn't replace core work (e.g. registered nurses, police,
                 GPs, school teachers)
6-7  High      — knowledge work substantially reshaped by AI tools, though
                 human oversight/judgement remains central (e.g. civil
                 engineers, architects, managers, university lecturers)
8-9  Very high — knowledge/desk work where AI can already do a large share
                 of the core tasks (e.g. software developers, accountants,
                 solicitors, graphic designers)
10   Maximum   — narrow, repetitive information-processing tasks with no
                 human judgement required (e.g. keyboard operators,
                 switchboard operators)

Respond with a JSON object: an integer "score" between 0 and 10, and a
one-sentence "rationale" of at most 25 words."""

SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "enum": list(range(MIN_SCORE, MAX_SCORE + 1)),
            "description": "AI exposure on the 0-10 rubric.",
        },
        "rationale": {
            "type": "string",
            "description": "One sentence, at most 25 words.",
        },
    },
    "required": ["score", "rationale"],
    "additionalProperties": False,
}


class ScoringError(RuntimeError):
    """Raised when a single occupation could not be scored."""


@dataclass(frozen=True, slots=True)
class Occupation:
    anzsco_code: str
    title: str


@dataclass(frozen=True, slots=True)
class Score:
    title: str
    score: int
    rationale: str

    def as_record(self) -> dict[str, object]:
        return {"title": self.title, "score": self.score, "rationale": self.rationale}


def load_occupations(path: Path = OCCUPATIONS_JSON) -> list[Occupation]:
    """Read the extract stage's output, keeping only usable rows."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run `aujobs extract` first.")

    occupations: list[Occupation] = []
    for row in read_json(path):
        code = str(row.get("anzsco_code") or "").strip()
        title = str(row.get("title") or "").strip()
        if code and title:
            occupations.append(Occupation(anzsco_code=code, title=title))
    return occupations


def load_existing_scores(path: Path = SCORES_JSON) -> dict[str, dict[str, object]]:
    """Load previously written scores so a run can resume."""
    if not path.exists():
        return {}
    scores = read_json(path)
    if not isinstance(scores, dict):
        raise ValueError(f"{path} should contain a JSON object keyed by ANZSCO code.")
    # Drop malformed or unscored entries so they are retried.
    return {
        code: entry
        for code, entry in scores.items()
        if isinstance(entry, dict) and isinstance(entry.get("score"), int)
    }


def _extract_text(response: Any) -> str:
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _parse_payload(text: str) -> tuple[int, str]:
    """Parse the model's JSON reply, tolerating a stray markdown fence."""
    cleaned = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ScoringError(f"response was not valid JSON: {cleaned[:120]!r}") from exc

    try:
        score = int(payload["score"])
        rationale = str(payload["rationale"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise ScoringError(f"response missing score/rationale: {cleaned[:120]!r}") from exc

    if not MIN_SCORE <= score <= MAX_SCORE:
        raise ScoringError(f"score {score} outside {MIN_SCORE}-{MAX_SCORE}")
    return score, rationale


class Scorer:
    """Scores one occupation per call against the rubric."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        # Structured outputs are not available on every model; the first 400
        # flips this off and the run continues on prompt-guided JSON.
        self._structured_outputs = True
        self._lock = threading.Lock()

    def _output_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"effort": self._effort}
        if self._structured_outputs:
            config["format"] = {"type": "json_schema", "schema": SCORE_SCHEMA}
        return config

    def _disable_structured_outputs(self, reason: str) -> None:
        with self._lock:
            if self._structured_outputs:
                self._structured_outputs = False
                log.warning(
                    "Model %s rejected structured outputs (%s); "
                    "falling back to prompt-guided JSON.",
                    self._model,
                    reason,
                )

    def score(self, occupation: Occupation) -> Score:
        prompt = f"Occupation: {occupation.title} (ANZSCO {occupation.anzsco_code})"

        for attempt in range(2):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=RUBRIC,
                    # The SDK's TypedDicts don't cover a raw json_schema block.
                    output_config=cast("Any", self._output_config()),
                    messages=[{"role": "user", "content": prompt}],
                )
            except anthropic.BadRequestError as exc:
                if self._structured_outputs and attempt == 0:
                    self._disable_structured_outputs(str(exc)[:120])
                    continue
                raise ScoringError(f"API rejected the request: {exc}") from exc

            if response.stop_reason == "refusal":
                raise ScoringError("model declined to answer")
            if response.stop_reason == "max_tokens":
                raise ScoringError("response truncated — increase --max-tokens")

            score, rationale = _parse_payload(_extract_text(response))
            return Score(title=occupation.title, score=score, rationale=rationale)

        raise ScoringError("exhausted retries")  # pragma: no cover - unreachable


def run(
    *,
    occupations_path: Path = OCCUPATIONS_JSON,
    scores_path: Path = SCORES_JSON,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    limit: int | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict[str, dict[str, object]]:
    """Score every occupation that does not already have a score."""
    occupations = load_occupations(occupations_path)
    scores = load_existing_scores(scores_path)
    if scores:
        log.info("Resuming — %d occupations already scored", len(scores))

    pending = [occ for occ in occupations if occ.anzsco_code not in scores]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        log.info("Nothing to do — all %d occupations are scored", len(occupations))
        return scores

    # max_retries covers 429/5xx with backoff; concurrency is the throughput knob.
    client = client or anthropic.Anthropic(max_retries=5)
    scorer = Scorer(client, model=model, effort=effort, max_tokens=max_tokens)

    log.info("Scoring %d occupations with %s (concurrency %d)", len(pending), model, concurrency)
    failures = 0
    done = 0
    since_checkpoint = 0
    lock = threading.Lock()

    fatal: Exception | None = None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(scorer.score, occ): occ for occ in pending}
        for future in as_completed(futures):
            occupation = futures[future]
            try:
                result = future.result()
            except FATAL_ERRORS as exc:
                # Bad credentials or a revoked key will fail every remaining
                # occupation identically — stop rather than burn the whole list.
                fatal = exc
                for queued in futures:
                    queued.cancel()
                break
            except (ScoringError, anthropic.APIError) as exc:
                failures += 1
                log.error("%s (%s) failed: %s", occupation.title, occupation.anzsco_code, exc)
                continue

            with lock:
                scores[occupation.anzsco_code] = result.as_record()
                done += 1
                since_checkpoint += 1
                log.info(
                    "[%d/%d] %s (%s) -> %d",
                    done,
                    len(pending),
                    occupation.title,
                    occupation.anzsco_code,
                    result.score,
                )
                if since_checkpoint >= CHECKPOINT_EVERY:
                    write_json_atomic(scores_path, scores)
                    since_checkpoint = 0

    # Always persist what succeeded, including on the fatal path, so the run stays resumable.
    write_json_atomic(scores_path, scores)
    log.info("Wrote %s (%d scored, %d failed)", scores_path, len(scores), failures)
    if fatal is not None:
        raise fatal
    if failures:
        log.warning("Re-run `aujobs score` to retry the %d failed occupations.", failures)
    return scores


def summarise(scores: dict[str, dict[str, object]]) -> Iterable[str]:  # pragma: no cover
    yield f"{len(scores)} occupations scored"


__all__: Sequence[str] = (
    "DEFAULT_CONCURRENCY",
    "DEFAULT_EFFORT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "Occupation",
    "Score",
    "Scorer",
    "ScoringError",
    "load_existing_scores",
    "load_occupations",
    "run",
)
