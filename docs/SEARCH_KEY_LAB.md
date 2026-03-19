# Search Key Lab

`app.tools.search_key_lab` is the shadow-mode evaluator for plan v1.

It reuses the production YouTube search and discovery filtering logic, but it does **not**:

- insert `ContentItem`
- insert `VideoDiscoveryRun`
- mutate the production registry

It writes local artifacts only:

- `results.json`
- `summary.md`
- `best_registry.yaml`

## Baseline run

From `/Users/aarshad/dev/projects/blips/blips-ai-news-backend/src/backend`:

```bash
python -m app.tools.search_key_lab \
  --surface both \
  --regions US \
  --output-dir /tmp/blips-search-lab/baseline
```

That evaluates the current runtime registry.

## Candidate run

Create a temporary YAML file outside the repo, then point the lab at it:

```bash
python -m app.tools.search_key_lab \
  --registry /tmp/blips-search-lab/candidates-round1.yaml \
  --surface both \
  --regions US \
  --output-dir /tmp/blips-search-lab/round1
```

Optional flags:

- `--query-ids ai-video,chip-shorts`
- `--surface videos`
- `--regions US,GB,CA`

## Output interpretation

- `results.json` contains per-query, per-region raw candidate counts, accepted counts, rejection mix, and a quality-adjusted score.
- `summary.md` is the readable round summary.
- `best_registry.yaml` keeps only rows that passed the acceptance thresholds for every evaluated surface.

## Thresholds

- Videos must produce:
  - at least `3` accepted results
  - at least `2` distinct accepted channels
  - non-English rate `<= 30%`
  - off-topic rate `<= 15%`
- Reels must produce:
  - at least `2` accepted results
  - at least `2` distinct accepted channels
  - non-English rate `<= 30%`
  - off-topic rate `<= 10%`

High-fit accepted items count toward the score when `format_fit_score >= 0.7`.
