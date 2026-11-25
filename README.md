# catalog-bench

How many commits per second does a lakehouse catalog accept, and what happens to writers when several commit at once?

This is an open benchmark: one small file per system, one results format, and every number reproducible from this repository.

**Status: early.** Six systems are implemented: raw Postgres as a baseline, DuckLake on Postgres, the Iceberg SQL catalog on Postgres, Amazon S3 Tables, Lakekeeper and Polaris. The first four have a published in-region run; Lakekeeper and Polaris do not yet.

## Results so far

One run on one machine, on 2026-11-25: an EC2 c6id.4xlarge (16 vCPUs, instance-store NVMe) in ca-central-1, with Postgres 18 in Docker on the same host and S3 Tables in the same region. Each point is the median of three 60-second runs. Every run passed verification. The raw numbers are in `results/`.

| System | 1 writer | 16 writers | Most writers measured | What limits it |
|---|---|---|---|---|
| Postgres (baseline) | 8,240 | 51,054 | 81,667 at 64 | Nothing reached; still rising |
| DuckLake on Postgres | 189 | 178 | 170 at 32 | Does not scale with writers, and writers starve: in every run with more than one writer, a single writer made all but a handful of the commits and each of the others made one or none |
| S3 Tables, one table per writer | 1.9 | 29.7 | 49.1 at 64 | Scales with tables until the service throttles: 45% of attempts at 64 writers were refused with Too Many Requests |
| Iceberg SQL catalog on Postgres | 7.9 | 7.9 | 7.9 at 16 | The client: the catalog call is 11 ms of a 128 ms commit, and conflicts take the rest as writers are added |
| S3 Tables, one shared table | 1.9 | 2.1 | 1.9 at 64 | Commit conflicts, then throttling; the catalog call is 72 ms of a 528 ms commit |

All figures are acknowledged commits per second.

What this run does not show:

- For the three Iceberg rows the Python client (pyiceberg) and its object writes take most of each commit, so these are rates for that client, not ceilings for the catalogs. A catalog-only workload is planned.
- The Iceberg SQL ramp stopped at 16 writers and the DuckLake ramp at 32, because the machine's time budget ran out. A single DuckLake run at 64 writers (164 commits per second) is left out for having no repetitions.
- Error messages in the published files have long numeric identifiers replaced with `<id>`.

## What is measured

N writer processes each open their own connection and commit one single-row insert at a time, as fast as the system acknowledges them, for a fixed period. For each run the benchmark records:

- acknowledged commits per second
- commit latency: median, p95, p99, slowest, mean
- failed commits, with the error messages
- commits per writer, so a starved writer is visible
- a row count afterwards, to verify that every acknowledged commit is present

A commit counts only when the client was told it succeeded. A run is marked `verified` when the rows found equal the acknowledged commits, allowing for failed commits that landed anyway.

Single-row commits are the worst case for a catalog on purpose: they isolate commit cost from data volume. Batched and multi-table workloads are planned.

## Run it

Needs Docker and [uv](https://docs.astral.sh/uv/).

```sh
docker compose up -d --wait
uv run python -m bench.run --writers 1 4 16 --seconds 10 --out results/local.json
uv run pytest
```

`--systems` picks a subset; the default is all of them. Connection settings come from `BENCH_PG` and `BENCH_DATA_PATH` (see `bench/config.py`).

`lakekeeper` and `polaris` need no setup beyond the compose file: it starts both catalogs (ports 58181 and 58182), a RustFS object store for their data (port 59000) and a database for each in the Postgres service, and `setup()` creates the bucket and warehouse. RustFS writes to `.data/rustfs` as its own container user, so empty that directory through Docker: `docker compose run --rm --entrypoint sh rustfs-perms -c 'rm -rf /data/*'`. Their settings are the `BENCH_S3_*`, `BENCH_LAKEKEEPER_URL` and `BENCH_POLARIS_*` variables in `bench/config.py`.

### Amazon S3 Tables

`s3-tables` is skipped unless `BENCH_S3TABLES_ARN` names a table bucket you own (`arn:aws:s3tables:<region>:<account>:bucket/<name>`). Credentials come from the standard AWS environment. Run it from a machine in the bucket's region, or the numbers measure your uplink. `BENCH_S3TABLES_TABLES` sets how many tables the writers are spread over; set it to the writer count to give each writer its own table.

If every request fails with a certificate verification error, the Python process is not finding your system's CA bundle. Pointing it at the bundle `certifi` ships fixed it on the one machine where this happened; which of the three variables is the one that matters has not been isolated:

```sh
export SSL_CERT_FILE="$(uv run python -m certifi)"
export AWS_CA_BUNDLE="$SSL_CERT_FILE" CURL_CA_BUNDLE="$SSL_CERT_FILE"
```

## Add a system

Copy `bench/systems/postgres.py` to a new file in the same directory and edit it. Any module there with a `System` class is registered automatically. The interface is five things, documented at the top of `bench/systems/__init__.py`:

| Member | Purpose |
|---|---|
| `name` and docstring | Identifier, and one line saying exactly what is measured |
| `setup()` | Start from an empty table. Leftover snapshots and files change commit cost, so clean thoroughly |
| `connect()` | Called once per writer process. Returns an object with `commit_one(writer, seq)` and `close()` |
| `count()` | Rows in the table, for verification |
| `version()` | Versions of everything involved |

If the system needs a service, add it to `compose.yaml`.

### Rules for a fair entry

- Use default settings. If you tune something, make it a separate entry whose name and docstring say what changed.
- `commit_one` must not return until the commit is durable by the system's own definition, and must raise if it failed. No client-side batching or retries beyond what the client library does by default.
- State what the client is. For systems where the client does real work per commit, the client is part of the result.

## Results format

`bench.run` writes one JSON document: the environment, machine, versions, each system's description, the harness ceiling (the rate the load generator reaches against a system that does nothing), and one object per run with the fields listed above.

The format is the contract. A driver written in any language can produce it for the same workload, and its results can sit beside these, labelled by client.

## Results site

`site/` is a static page that reads one file, `site/data.json`, built from every `results/*.json`:

```sh
uv run python -m bench.site
python3 -m http.server 8000 --directory site   # then open http://localhost:8000
```

Each environment gets its own ranking; runs shorter than 30 seconds or with a single repetition are shown but labelled as not comparable. `site/data.json` is generated, so it is not committed.

On GitHub, pushes to `main` rebuild the data and deploy `site/` to Pages, and a manually started workflow runs the self-hosted systems on a GitHub-hosted runner as a smoke test and uploads the results file as an artifact. In-region AWS runs (for example S3 Tables) are manual for now: there is no AWS workflow.

## Limitations

- Results from a workstation or a shared CI runner are noisy. Compare systems within one run on one machine, not across machines.
- The Python client's cost is negligible for raw Postgres (checked against `pgbench`). For the Iceberg systems it is most of each commit, so those rows measure pyiceberg as much as the catalog.
- Only the last attempt's catalog call is timed when the client retries a commit.
