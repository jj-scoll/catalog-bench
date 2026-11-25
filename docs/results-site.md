# The results site

The published site is at <https://jj-scoll.github.io/catalog-bench/>.

`site/` is a static page that reads one file, `site/data.json`, built from every `results/*.json` and `results/pipeline/*.json`:

```sh
uv run python -m bench.site
python3 -m http.server 8000 --directory site   # then open http://localhost:8000
```

Each environment gets its own ranking; runs shorter than 30 seconds or with a single repetition are shown but labelled as not comparable. `site/data.json` is generated, so it is not committed.

The site shows both workloads. The comparability labels above apply to the commit-rate workload; every pipeline row is a single run and is shown as such, and a set that was repeated shows a row for each repeat. The pipeline figures are also tabulated in [pipeline.md](pipeline.md).

## Automation

On GitHub, pushes to `main` rebuild the data and deploy `site/` to Pages, and a manually started workflow runs the self-hosted systems on a GitHub-hosted runner as a smoke test and uploads the results file as an artifact. In-region AWS runs (for example S3 Tables) are manual for now: there is no AWS workflow.
