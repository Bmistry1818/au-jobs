# Contributing

Thanks for taking a look. Issues and pull requests are welcome.

## Getting set up

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check .
ruff format --check .
mypy
pytest
```

CI runs exactly these, plus the test suite on Python 3.10–3.13 and a smoke test
that rebuilds `site/data.json` from `examples/` and diffs it against what is
committed.

## Conventions

* **Tests must not hit the network or the Claude API.** The suite fakes both:
  see `FakeSession` in `tests/test_extract.py` and `FakeClient` in
  `tests/test_score.py`. If you add a code path that talks to something
  external, add a seam so it can be faked.
* **Do not commit generated data.** Everything under `data/` is gitignored.
  `site/data.json` is the one exception — it holds the six-occupation sample
  dataset that makes the demo site work out of the box. If you change it,
  regenerate it rather than hand-editing:

  ```bash
  aujobs build \
    --occupations examples/sample_occupations.json \
    --scores examples/sample_scores.json \
    --output site/data.json \
    --sample
  ```

* **Treat pipeline output as untrusted in the browser.** Occupation titles and
  model rationales are inserted with `textContent`, never `innerHTML`. Keep it
  that way.
* Line length is 100; `ruff format` is the arbiter of style.

## Things that are especially useful

* **Parser fixes when JSA changes a workbook.** These break in the field and
  are hard for us to reproduce — the error message lists the columns the parser
  actually saw, so paste that into the issue.
* **The OSCA migration.** When JSA publishes full OSCA occupation profiles, the
  source pages in `src/aujobs/config.py` and the major-group mapping will both
  need revisiting.
* **Rubric critique.** The 0–10 scale in `src/aujobs/score.py` is a judgement
  call. If you think a band is miscalibrated, open an issue with examples.

## Reporting a security issue

See [SECURITY.md](SECURITY.md).
