# catalog-bench

An open benchmark of lakehouse catalogs: how many commits per second each one accepts as writers are added, and how a whole write, close, read and reprocess pipeline behaves on each storage option.

[Results site](https://jj-scoll.github.io/catalog-bench/) · [Methodology](docs/methodology.md) · [Pipeline workload](docs/pipeline.md) · [Running it](docs/running.md) · [Adding a system or option](docs/adding-a-system.md)

One small file per system, one results format, and every number reproducible from this repository.

**Status: early.** Six systems are implemented: raw Postgres as a baseline, DuckLake on Postgres, the Iceberg SQL catalog on Postgres, Amazon S3 Tables, Lakekeeper and Polaris. All six have a published in-region run. A second workload, a whole pipeline flow, has its first results in `results/pipeline/`.

## Key findings

Commit rates are acknowledged single-row commits per second, each the median of three 60-second runs. Pipeline figures are from a single run each.

- **The Python Iceberg client, not the catalog, sets the Iceberg commit rate.** The Iceberg SQL catalog, Lakekeeper and Polaris accept 7.9, 6.7 and 6.5 commits/s with one writer. The catalog call is 11 to 20 ms of a 128 to 149 ms commit; pyiceberg and its object writes take the rest.
- **DuckLake on Postgres does not scale with writers.** It accepts 189 commits/s with one writer and 161 with 64. In every run with more than one writer, a single writer made all but a handful of the commits.
- **Amazon S3 Tables scales by table, not by writer.** One shared table stays between 1.8 and 2.3 commits/s from 1 to 64 writers. One table per writer reaches 49.1 commits/s at 64 writers, with 45% of attempts refused with Too Many Requests.
- **Raw Postgres is not the limit for any of them.** The baseline accepts 8,240 commits/s with one writer and 81,667 with 64, and is still rising.
- **In the pipeline workload, committing to Iceberg on every batch could not hold 1.25 batches/s.** On the Iceberg SQL catalog it wrote 1.07 batches/s and batches started up to 1,702 s behind schedule. Committing once per trace instead kept to schedule on all four Iceberg catalogs, as did DuckLake and plain files.
- **Committing once per trace moves the cost to closing the trace.** On S3 Tables the slowest close took 9.4 s for a 300-batch trace and 237 s for a 9,000-batch trace. DuckLake's slowest close at the same paced load was 55 to 87 ms.

## Commit rate

N writer processes each commit one single-row insert at a time, as fast as the system acknowledges them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/commit-rate-dark.svg">
  <img src="docs/img/commit-rate.svg" alt="Line chart of acknowledged commits per second against the number of concurrent writers, from 1 to 64, both axes on log scales, with one line per system. Postgres rises throughout, DuckLake stays near 160 to 190, and every Iceberg system writing to one shared table stays below 10. The figures are in the table below.">
</picture>

Two passes on 2026-11-25, each system on its own EC2 c6id.4xlarge (16 vCPUs, instance-store NVMe) in ca-central-1, with Postgres 18 in Docker on the same host and S3 Tables in the same region. The second pass used different instances of the same type from the first. Each point is the median of three 60-second runs. Every run passed verification. The raw numbers are in `results/`.

| System | 1 writer | 16 writers | Most writers measured | What limits it |
|---|---|---|---|---|
| Postgres (baseline) | 8,240 | 51,054 | 81,667 at 64 | Nothing reached; still rising |
| DuckLake on Postgres | 189 | 178 | 161 at 64 | Does not scale with writers, and writers starve: in every run with more than one writer, a single writer made all but a handful of the commits and each of the others made one or none |
| DuckLake on Postgres, inlining off | 162 | 145 | 124 at 64 | The same pattern, about 14% lower at one writer: every commit now writes a Parquet file |
| S3 Tables, one table per writer | 1.9 | 29.7 | 49.1 at 64 | Scales with tables until the service throttles: 45% of attempts at 64 writers were refused with Too Many Requests |
| Iceberg SQL catalog on Postgres | 7.9 | 7.9 | 5.1 at 64 | The client: the catalog call is 11 ms of a 128 ms commit, and conflicts take the rest as writers are added. 7.0 at 32 |
| Lakekeeper | 6.7 | 6.5 | 4.3 at 64 | The client: the catalog call is 17 ms of a 149 ms commit at one writer. Flat to 16 writers, then conflicts cost throughput |
| Polaris | 6.5 | 6.3 | 4.1 at 64 | The client: the catalog call is 20 ms of a 149 ms commit at one writer. Same shape as Lakekeeper |
| S3 Tables, one shared table | 1.9 | 2.1 | 1.9 at 64 | Commit conflicts, then throttling; the catalog call is 72 ms of a 528 ms commit |

All figures are acknowledged commits per second. "Inlining" is DuckLake's default of keeping a small insert as rows in the catalog database instead of writing a Parquet file.

## Pipeline workload

The commit ramp isolates one call. The pipeline workload runs the whole flow a batch pipeline has, against a storage option. Load arrives as *traces*, each a group of *batches* that is closed and read as a unit; a batch is 5,000 rows here, written by one of 16 workers in one call. When a trace's batches are in, a finaliser *closes* it, readers load every trace and the rows are compared with what was acknowledged, and one trace is then reprocessed under a new version that must replace the old rows. If the option has *compaction* (maintenance that merges small data files into larger ones), it runs and the reads are repeated.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/pipeline-dark.svg">
  <img src="docs/img/pipeline.svg" alt="Bar chart of the five S3-backed options at 1.25 batches per second, in three panels on one log scale: time to write one batch (median), to close a trace (slowest) and to read one trace back (median). DuckLake on Aurora is under 100 ms to write and close; Iceberg per batch on S3 Tables takes 6.1 s per write and fell behind schedule. The figures are in the table below.">
</picture>

The table is the paced run: 6 traces of 300 batches arriving at 1.25 batches/s, on an EC2 c6id.4xlarge in ca-central-1 on 2026-11-25. **Each row is a single run**, so treat small differences as noise.

| Option | Backed by | Writes/s | Batch commit p50 (ms) | Furthest behind schedule (ms) | Slowest close (ms) | Read one trace p50 (ms) | Same read after compaction (ms) | Verified |
|---|---|---|---|---|---|---|---|---|
| `files` | S3, no catalog | 1.28 | 342 | <1 | 331 | 5,724 | n/a | yes |
| `ducklake` | Postgres in Docker on the runner | 1.28 | 15.6 | <1 | 55.2 | 50.7 | 39.4 | yes |
| `ducklake` | Aurora PostgreSQL, provisioned | 1.28 | 93.9 | <1 | 81.9 | 349 | 95.6 | yes |
| `ducklake` | Aurora Serverless v2 | 1.28 | 96.3 | <1 | 87.3 | 340 | 98.0 | yes |
| `iceberg-per-trace` | Iceberg SQL catalog on local Postgres | 1.28 | 3.0 | <1 | 495 | 13.0 | n/a | yes |
| `iceberg-per-trace` | Lakekeeper | 1.28 | 3.0 | <1 | 681 | 25.0 | n/a | yes |
| `iceberg-per-trace` | Polaris | 1.28 | 3.1 | <1 | 649 | 26.9 | n/a | yes |
| `iceberg-per-trace` | Amazon S3 Tables | 1.27 | 341 | <1 | 9,407 | 271 | n/a | yes |
| `iceberg-per-batch` | Iceberg SQL catalog on local Postgres | 1.07 | 1,798 | 1,702,218 | 2,355 | 721 | n/a | yes |
| `iceberg-per-batch` | Amazon S3 Tables (3 traces, not 6) | 1.13 | 6,133 | 49,679 | 27,368 | 2,167 | n/a | yes |

How to read it:

- `files` is plain Parquet with no catalog. `ducklake` is one DuckLake transaction per batch. `iceberg-per-batch` is two Iceberg commits per batch. `iceberg-per-trace` writes each batch as a plain Parquet file and commits to Iceberg only when the trace closes.
- "Writes/s" counts every acknowledged write. Every 50th batch is deliberately delivered twice, so a run that keeps to the 1.25 batches/s schedule shows about 1.28.
- "Verified" means no acknowledged row was missing, duplicated or left over from an old version, after the first read, after reprocessing and, where it ran, after compaction.
- "Backed by" names the catalog or metadata store. Aurora was one provisioned db.r8g.xlarge instance and one Serverless v2 cluster set to 0.5 to 16 ACU, both PostgreSQL 16.8.
- **Batch data did not go to the same place in every row.** The `files`, Aurora and S3 Tables rows wrote batch data to S3. The rows backed by something on the runner (local Postgres, the Iceberg SQL catalog, Lakekeeper, Polaris) wrote it to the runner's instance-store NVMe. Their 3 to 16 ms batch writes are local disk writes and are not comparable with the S3 rows (94 to 342 ms).
- With the pacing removed, `ducklake` wrote 46.2 batches/s on local Postgres, 24.3 on provisioned Aurora and 23.6 on Aurora Serverless v2 (4 traces of 500 batches, one run each).

All 24 runs, the other load shapes and the definition of each column are in [docs/pipeline.md](docs/pipeline.md). The results site shows them too, in its pipeline section.

## Quick start

Needs Docker and [uv](https://docs.astral.sh/uv/).

```sh
docker compose up -d --wait
uv run python -m bench.run --writers 1 4 16 --seconds 10 --out results/local.json
uv run pytest
```

`--systems` picks a subset; the default is all of them. Connection settings come from `BENCH_PG` and `BENCH_DATA_PATH` (see `bench/config.py`).

The pipeline workload:

```sh
uv run python -m bench.pipeline --options files ducklake iceberg-per-batch iceberg-per-trace \
  --traces 4 --batches 100 --rows 1000 --workers 8 --rate 5 --redeliver-every 20 --out results/pipeline/local.json
```

Lakekeeper, Polaris and Amazon S3 Tables have setup notes in [docs/running.md](docs/running.md).

## What this does not show

Commit rate:

- For the five Iceberg rows the Python client (pyiceberg) and its object writes take most of each commit, so these are rates for that client, not ceilings for the catalogs. A catalog-only workload is planned.
- The DuckLake row is not like for like with the Iceberg rows. With default settings DuckLake keeps a single-row insert inline in the catalog database and writes no Parquet file, so its commit is one Postgres transaction. The Iceberg rows write data and metadata files on every commit. The "inlining off" row is the closer comparison.
- The Iceberg SQL points at 32 and 64 writers, the DuckLake point at 64, and the Lakekeeper, Polaris and inlining-off rows come from the second pass, so they ran on different instances from the rest.
- Single-row commits are the worst case for a catalog on purpose: they isolate commit cost from data volume. Batched and multi-table workloads are planned.
- Only the last attempt's catalog call is timed when the client retries a commit.
- The Python client's cost is negligible for raw Postgres (checked against `pgbench`). For the Iceberg systems it is most of each commit, so those rows measure pyiceberg as much as the catalog.

Pipeline workload:

- Every pipeline figure is from one run. There are no repetitions and no medians yet.
- The Iceberg options cannot yet reset Lakekeeper or Polaris for a second run, so those two have one pipeline run each, at one load shape.
- `iceberg-per-batch` on S3 Tables was run only at smaller shapes (2 or 3 traces), so its row above is not the same load as the others.
- The result files do not record the Aurora instance class or capacity setting, or where batch data files were written for the catalog-backed options. Those facts are stated above from the run setup, not from the files. The capacity Serverless v2 actually used during a run was not recorded.
- The runs backed by a catalog on the runner wrote batch data to local NVMe, not S3, so they understate what the same option costs with data on S3.

Both:

- Results from a workstation or a shared CI runner are noisy. Compare systems within one run on one machine, not across machines.
- Error messages in the published files have long numeric identifiers replaced with `<id>`.

## More detail

| Document | What is in it |
|---|---|
| [docs/methodology.md](docs/methodology.md) | What the commit-rate workload measures, the rules for a fair entry, the results format |
| [docs/pipeline.md](docs/pipeline.md) | The pipeline steps, the four options, the load shapes, what each column means, all 24 runs |
| [docs/running.md](docs/running.md) | Local services (Lakekeeper, Polaris, RustFS), Amazon S3 Tables, and the CA bundle fix for certificate errors |
| [docs/adding-a-system.md](docs/adding-a-system.md) | Adding a system to the commit-rate workload or an option to the pipeline workload |
| [docs/results-site.md](docs/results-site.md) | Building the results site locally, and what the GitHub workflows do |
