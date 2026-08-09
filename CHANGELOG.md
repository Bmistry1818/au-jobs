# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-09

First packaged release.

### Added

- `aujobs` CLI with `extract`, `score`, `build` and `all` subcommands.
- Packaging via `pyproject.toml`, with a `dev` extra for ruff, mypy and pytest.
- Test suite (no network or API calls) covering parsing, scoring, merging and
  the CLI, plus CI on Python 3.10–3.13.
- GitHub Actions workflow that publishes `site/` to GitHub Pages.
- Six-occupation sample dataset in `examples/`, so the site and the tests work
  without an API key.
- Concurrent, resumable scoring with checkpointing and atomic writes.
- Structured outputs on the scoring call, with an automatic fallback to
  prompt-guided JSON on models that do not support them.
- Readable CLI errors (with exit code 1) for an unreachable JSA site, an
  unreachable Claude API, and rejected credentials, in place of a traceback.

### Changed

- Scoring defaults to `claude-opus-5` (was `claude-sonnet-4-6`); override with
  `--model`.
- Extract stage validates that discovered download links stay on
  `jobsandskills.gov.au`, retries transient HTTP failures, coerces `$`/`%`/comma
  formatted numbers, and drops rows without a 4-digit ANZSCO code.
- Weighted average AI exposure is computed over scored occupations only.
- Pipeline artefacts moved to `data/`; the site moved to `site/`, matching what
  the code and README always described.

### Fixed

- Occupation titles and model rationales are rendered as text in the treemap
  tooltip, closing an HTML-injection path from pipeline data into the page.
- D3 is pinned with a subresource-integrity hash instead of loading unverified
  from a CDN.
- Missing projected growth rendered as `—%`; it now renders as `—`.
- Failed scoring attempts are no longer persisted as `score: null`, so re-runs
  retry them instead of skipping them permanently.
- `window.resize` handler is debounced and no longer re-registered per render.
- An authentication or permission error now aborts the scoring run instead of
  failing all ~350 occupations one at a time.

[Unreleased]: https://github.com/bmistry1818/au-jobs/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bmistry1818/au-jobs/releases/tag/v0.1.0
