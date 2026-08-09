# `examples/`

A six-occupation sample dataset, deliberately spanning the full exposure range
(farm labourers at 1, keyboard operators at 10). It exists so that the site,
the tests and CI all work without a network connection or an API key.

| File | Shape | Stands in for |
| --- | --- | --- |
| `sample_occupations.json` | list of records | `data/occupations.json` (`aujobs extract`) |
| `sample_scores.json` | `{code: {title, score, rationale}}` | `data/scores.json` (`aujobs score`) |

The employment, earnings and education figures are illustrative round numbers,
not current JSA statistics — run `aujobs extract` for real data. Projected
growth is `null` here because the sample has none.

Rebuild the demo site data from these files with:

```bash
aujobs build \
  --occupations examples/sample_occupations.json \
  --scores examples/sample_scores.json \
  --output site/data.json \
  --sample
```

CI runs exactly that and diffs the result against the committed
`site/data.json`, so the two cannot drift apart.
