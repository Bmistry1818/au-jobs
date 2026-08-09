# AI Exposure of the Australian Job Market

[![CI](https://github.com/bmistry1818/au-jobs/actions/workflows/ci.yml/badge.svg)](https://github.com/bmistry1818/au-jobs/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Scores every ANZSCO occupation in Australia on AI exposure (0–10) using official
[Jobs and Skills Australia](https://www.jobsandskills.gov.au/) data, then
visualises it as an interactive treemap. Box area is employment, colour is AI
exposure. Same methodology as [karpathy/jobs](https://github.com/karpathy/jobs),
adapted for the Australian labour market.

> **These scores are model-generated estimates, not forecasts.** The employment,
> earnings and growth figures come from JSA; the 0–10 exposure score and its
> rationale are produced by a language model against the rubric in
> [`src/aujobs/score.py`](src/aujobs/score.py). Treat them as a structured
> opinion to argue with, not as evidence about any individual's job.

## Quick start

Look at the site with the checked-in sample data — no API key, no network:

```bash
python3 -m http.server 8000 --directory site
```

Then open <http://localhost:8000>.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

For development, install the linting and test extras instead:

```bash
pip install -e ".[dev]"
```

## Running the pipeline

Three stages, each independently runnable and each writing its own artefact:

```bash
aujobs extract                   # JSA workbooks  -> data/occupations.{json,csv}
export ANTHROPIC_API_KEY=sk-ant-...
aujobs score                     # occupations    -> data/scores.json
aujobs build                     # both           -> site/data.json
```

Or `aujobs all` to run all three in order. `aujobs <command> --help` lists the
flags for each stage.

| Stage | What it does | Needs |
| --- | --- | --- |
| `extract` | Discovers the current `.xlsx` download links on the JSA data pages, downloads them, and parses employment, median earnings, education level and projected growth per 4-digit ANZSCO occupation. | Outbound access to `jobsandskills.gov.au` |
| `score` | Sends each occupation title to the Claude API with the AI-exposure rubric and records a 0–10 score plus a one-sentence rationale. | `ANTHROPIC_API_KEY` |
| `build` | Joins the two, tags each occupation with its ANZSCO major group, and computes the employment-weighted average exposure. | Nothing |

### Notes on `score`

* **Resumable.** Already-scored occupations are skipped, progress is
  checkpointed every 20 occupations, and failures are logged rather than
  written to disk — so re-running `aujobs score` retries exactly what is
  missing. Interrupting with Ctrl-C loses at most one checkpoint's work.
* **Model.** Defaults to `claude-opus-5`, which supports [structured
  outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) —
  the schema is enforced server-side, so malformed scores are not a failure mode.
  Pass `--model claude-sonnet-5` to trade some quality for cost on a full run.
  On a model without structured-output support the first request falls back to
  prompt-guided JSON automatically.
* **Throughput.** `--concurrency` (default 4) is the throughput knob; the SDK
  retries rate limits and 5xx with backoff. Use `--limit 5` for a cheap smoke
  test before committing to ~350 calls.

## The site

[`site/index.html`](site/index.html) is a single self-contained file: a D3
treemap, an employment/exposure summary, and per-major-group filter chips. It
fetches `data.json` from the same directory, so it needs to be served over HTTP
rather than opened as a `file://` URL.

D3 is loaded from cdnjs with a [subresource integrity](https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity)
hash pinned to 7.9.0. If you bump the version, recompute the hash:

```bash
curl -sS https://cdnjs.cloudflare.com/ajax/libs/d3/<version>/d3.min.js | openssl dgst -sha384 -binary | openssl base64 -A
```

Pushing to `main` publishes `site/` to GitHub Pages via
[`.github/workflows/pages.yml`](.github/workflows/pages.yml). Enable it once
under **Settings → Pages → Source → GitHub Actions**.

## Repository layout

```
src/aujobs/       Pipeline package (extract, score, build) and the `aujobs` CLI
site/             The static treemap; deployed to GitHub Pages
examples/         Six-occupation sample dataset used by tests and the demo site
data/             Generated artefacts (gitignored)
tests/            pytest suite — no network calls, no API calls
```

## Notes on the Australian data specifically

* Occupation data is coded on **ANZSCO**, which JSA has frozen while it
  transitions to a new classification called **OSCA**. Full OSCA occupation
  profiles are expected mid-to-late 2026 — worth checking
  jobsandskills.gov.au periodically and swapping the source pages in
  [`src/aujobs/config.py`](src/aujobs/config.py) once that lands.
* The extract stage discovers download links at runtime rather than hardcoding
  filenames, because JSA renames these files with each release.
* Column names vary between releases, so the parser matches on keywords
  (`"anzsco"`, `"median"` + `"earn"`) rather than exact headers. If a release
  changes terminology enough to break this, `parse_occupation_profiles()` and
  `parse_employment_projections()` are the two functions to fix — they raise an
  error listing the column names they actually found.
* Rows without a 4-digit ANZSCO code (major-group subtotals, "Total", footnotes)
  are dropped, and the weighted average is computed over scored occupations
  only so unscored rows can't drag it toward zero.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: `ruff check . && ruff format
--check . && mypy && pytest` should pass before you open a PR.

## Data source and licence

Source data is published by Jobs and Skills Australia, generally under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — check the licence
attached to the specific release you download. The code in this repository is
MIT licensed; see [LICENSE](LICENSE).
