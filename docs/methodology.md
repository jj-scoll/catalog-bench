# Methodology: the commit-rate workload

This page says what the commit-rate workload measures, what a fair entry looks like, and what the results file contains. The pipeline workload has its own page: [pipeline.md](pipeline.md).

## What is measured

N writer processes each open their own connection and commit one single-row insert at a time, as fast as the system acknowledges them, for a fixed period. For each run the benchmark records:

- acknowledged commits per second
- commit latency: median, p95, p99, slowest, mean
- failed commits, with the error messages
- commits per writer, so a starved writer is visible
- a row count afterwards, to verify that every acknowledged commit is present

A commit counts only when the client was told it succeeded. A run is marked `verified` when the rows found equal the acknowledged commits, allowing for failed commits that landed anyway.

Single-row commits are the worst case for a catalog on purpose: they isolate commit cost from data volume. Batched and multi-table workloads are planned.

## The published runs

Two passes on 2026-11-25, each system on its own EC2 c6id.4xlarge (16 vCPUs, instance-store NVMe) in ca-central-1, with Postgres 18 in Docker on the same host and S3 Tables in the same region. The second pass used different instances of the same type from the first. Each point is the median of three 60-second runs. Every run passed verification.

The Iceberg SQL points at 32 and 64 writers, the DuckLake point at 64, and the Lakekeeper, Polaris and inlining-off rows come from the second pass (the `results/ec2-run2-*.json` files), so they ran on different instances from the rest.

The results table is in the [README](../README.md#commit-rate) and the raw numbers are in `results/`.

## Rules for a fair entry

- Use default settings. If you tune something, make it a separate entry whose name and docstring say what changed.
- `commit_one` must not return until the commit is durable by the system's own definition, and must raise if it failed. No client-side batching or retries beyond what the client library does by default.
- State what the client is. For systems where the client does real work per commit, the client is part of the result.

How to write an entry is in [adding-a-system.md](adding-a-system.md).

## Results format

`bench.run` writes one JSON document: the environment, machine, versions, each system's description, the harness ceiling (the rate the load generator reaches against a system that does nothing), and one object per run with the fields listed under "What is measured".

The format is the contract. A driver written in any language can produce it for the same workload, and its results can sit beside these, labelled by client.

Error messages in the published files have long numeric identifiers replaced with `<id>`.

## Limitations

- Results from a workstation or a shared CI runner are noisy. Compare systems within one run on one machine, not across machines.
- The Python client's cost is negligible for raw Postgres (checked against `pgbench`). For the Iceberg systems it is most of each commit, so those rows measure pyiceberg as much as the catalog. These are rates for that client, not ceilings for the catalogs. A catalog-only workload is planned.
- Only the last attempt's catalog call is timed when the client retries a commit.
- The DuckLake row is not like for like with the Iceberg rows. With default settings DuckLake keeps a single-row insert inline in the catalog database and writes no Parquet file, so its commit is one Postgres transaction. The Iceberg rows write data and metadata files on every commit. The "inlining off" row is the closer comparison.
