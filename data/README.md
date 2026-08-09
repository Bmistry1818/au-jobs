# `data/`

Generated pipeline artefacts. Everything here except this file is gitignored.

| File | Written by | Contents |
| --- | --- | --- |
| `raw/*.xlsx` | `aujobs extract` | The JSA workbooks exactly as downloaded |
| `occupations.json` | `aujobs extract` | One record per 4-digit ANZSCO occupation |
| `occupations.csv` | `aujobs extract` | The same table, for spreadsheet use |
| `scores.json` | `aujobs score` | `{anzsco_code: {title, score, rationale}}` |

`aujobs score` resumes from `scores.json`, so deleting it forces a full re-score
(~350 API calls). To re-score a single occupation, delete just its key.

For a dataset you can use without running the pipeline, see [`../examples/`](../examples).
