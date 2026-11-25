# The pipeline workload

The commit-rate workload isolates one call. `bench.pipeline` runs a whole write-and-read flow instead, the kind a batch pipeline has, against a storage option. This page describes the steps, the options, what is timed, and every published run.

## Terms

- **Trace**: a group of batches that is written, closed and read as a unit.
- **Batch**: one chunk of a trace, written by one worker in one call: output rows and a few error rows.
- **Close** (finalise): the step that marks a trace complete once its batches are in, and replaces any older version of it.
- **Compaction** (maintenance): merging small data files into larger ones so later reads open fewer files.
- **Version**: a trace can be reprocessed under a new version. Afterwards readers must see only the new version's rows.

## The steps

1. A load arrives as traces, each split into batches. Workers write one batch at a time: output rows and a few error rows. With `--rate` the batches arrive on a schedule whether or not the workers keep up, and the run reports how far behind they fell.
2. A separate reader looks for sampled batches as soon as they are acknowledged, which measures how long a batch takes to become readable.
3. When a trace's batches are in, a finaliser closes it.
4. A reader loads every trace and the rows are compared with what was acknowledged: missing, duplicated and left-over rows are counted.
5. One trace is reprocessed under a new version with half its batches. Afterwards a reader must see only the new version.
6. If the option has maintenance (compaction), it runs, and the reads are repeated.

Delivery is at least once: a failed write is delivered again, up to three times, and `--redeliver-every N` delivers every Nth batch twice.

## The options

| Option | A batch is | Closing a trace is |
|---|---|---|
| `files` | one Parquet file at a fixed key, no catalog | writing a small control object |
| `ducklake` | one DuckLake transaction | one transaction: delete older versions, record the new one |
| `iceberg-per-batch` | two Iceberg commits (output, errors) | three commits: two deletes and a control row |
| `iceberg-per-trace` | plain Parquet files outside the table | two commits that replace the trace's rows in the tables |
| `vortex` | one Vortex file per delivery at its own key, no catalog, written and read through DuckDB | writing a small control object |
| `delta-per-batch` | two Delta Lake commits (output, errors) through delta-rs, no catalog | three commits: two deletes and a control row |
| `delta-per-trace` | plain Parquet files outside the table | two commits that replace the trace's rows in the tables |
| `lance-per-batch` | two Lance commits (output, errors), no catalog | three commits: two deletes and a control row |
| `lance-per-trace` | plain Parquet files outside the dataset | four commits: an append and a delete of older versions, for each dataset |

`ducklake` runs maintenance that merges adjacent data files, and the Delta Lake and Lance options compact their tables. The Delta Lake, Lance and Vortex options are read through DuckDB's `delta`, `lance` and `vortex` extensions. None of the five uses a catalog service: the Delta Lake log and the Lance manifests are objects beside the data.

`vortex` never overwrites a file. DuckDB's vortex extension (0.92.0, with DuckDB 2.0.1) keeps file metadata for the life of the process and does not notice a file replaced at the same key: a process that read the old file then returned the old row count, and no rows for a filter the old file's statistics ruled out. Each option's docstring states what it does at every step, and that text is saved with the results.

The two Iceberg options run on any Iceberg system in `bench/systems`, chosen with `BENCH_ICEBERG_CATALOG` (`iceberg-sql-postgres`, `s3-tables`, `lakekeeper` or `polaris`). `ducklake` uses the Postgres in `BENCH_PG`. `BENCH_PIPELINE_DATA` is where `files`, `ducklake` and the `iceberg-per-trace` files go, and everything the Vortex, Delta Lake and Lance options write: a local directory or an `s3://` URI.

## Running it

```sh
docker compose up -d --wait
uv run python -m bench.pipeline --options files ducklake iceberg-per-batch iceberg-per-trace \
  --traces 4 --batches 100 --rows 1000 --workers 8 --rate 5 --redeliver-every 20 --out results/pipeline/local.json
```

## What is timed

| Result key | Meaning |
|---|---|
| `batches_per_s` | Acknowledged batch writes per second over the load. It counts every delivery, so batches delivered twice count twice |
| `commit_p50_ms`, `commit_p95_ms` | One batch write: the batch is durable and acknowledged |
| `behind_max_ms` | How late a batch started against its scheduled time. Paced runs only |
| `visible_after_*_ms` | From the acknowledgement until a separate reader first sees the batch |
| `fresh_read_*_ms` | How long that first successful read took |
| `finalize_p50_ms`, `finalize_max_ms` | Closing a trace, including replacing an older version |
| `finalize_retries` | Times the finaliser had to retry a close |
| `read_trace_p50_ms` | A reader loading every current row of one trace |
| `read_trace_after_maintain_p50_ms` | The same read after compaction. Absent when the option has no maintenance |
| `write_errors` | Batch writes that failed and were delivered again |
| `batches_failed` | Batches that were never acknowledged after three deliveries |
| `verified`, `reprocess_verified`, `verified_after_maintain` | No row missing, duplicated or left over from an old version: after the first read, after reprocessing, and after compaction |

## Load shapes

Every published run used 5,000 rows per batch, 16 workers, and delivered every 50th batch twice.

| Shape | Traces × batches per trace | Arrival |
|---|---|---|
| rate | 6 × 300 | paced at 1.25 batches/s |
| burst | 6 × 1,500 | paced at 12.5 batches/s |
| ceiling | 4 × 500 | unpaced, as fast as the workers go |
| big | 1 × 9,000 | unpaced |
| rate-small | 3 × 300 | paced at 1.25 batches/s |
| half-rate | 3 × 200 | paced at 0.63 batches/s |
| ceiling-small | 2 × 150 | unpaced |

Because every 50th batch is delivered twice, a paced run that keeps to its schedule shows `batches_per_s` about 2% above the target: 1.28 for a 1.25 target and 12.75 for 12.5. The rate is taken over the whole load including closing the traces, so an option with slow closes can read a little under that while still on schedule: `iceberg-per-trace` on S3 Tables reads 1.27 and 12.08 with no batch more than 2 ms late.

## Results

All runs are from 2026-11-25 on an EC2 c6id.4xlarge (16 vCPUs) in ca-central-1. **Each row is a single run.** There are no repetitions, so treat small differences as noise. The files are in `results/pipeline/`, one per run, named for the option, what backed it and the shape.

The files named `ec2-run3-...` are a third set of runs, made on one instance with batch data on S3 in the same region: `files` again as a reference, and `vortex`, `delta-per-trace`, `delta-per-batch`, `lance-per-trace` and `lance-per-batch`. They are marked † below. **The six † rate runs ran at the same time on that one instance** (their result files were written within 45 seconds of each other), so their timings include contention with each other. The † ceiling runs ran one at a time. Every row without a † had an instance to itself.

What backed each run:

| Name in the file | Meaning | Versions recorded |
|---|---|---|
| `local-postgres` | DuckLake metadata in Postgres in Docker on the runner | DuckDB 2.0.1, DuckLake format 1.1, PostgreSQL 18.7 |
| `aurora-prov` | DuckLake metadata in Aurora PostgreSQL, one provisioned db.r8g.xlarge instance | DuckDB 2.0.1, DuckLake format 1.1, PostgreSQL 16.8 |
| `aurora-sv2` | DuckLake metadata in Aurora Serverless v2, set to 0.5 to 16 ACU | DuckDB 2.0.1, DuckLake format 1.1, PostgreSQL 16.8 |
| `s3` | Parquet files on S3, no catalog | pyarrow 26.0.1 |
| `s3` (`vortex`) | Vortex files on S3, no catalog | DuckDB 2.0.1 with its vortex extension |
| `s3` (`delta-per-trace`, `delta-per-batch`) | Delta Lake tables on S3, no catalog and no lock table | deltalake 1.7.0 (delta-rs), read with DuckDB 2.0.1, pyarrow 26.0.1 |
| `s3` (`lance-per-trace`, `lance-per-batch`) | Lance datasets on S3, no catalog | lance 14.0.3, read with DuckDB 2.0.1, pyarrow 26.0.1 |
| `sql` | Iceberg SQL catalog on local Postgres | pyiceberg 0.12.0, pyarrow 26.0.1 |
| `s3tables` | Amazon S3 Tables REST endpoint in ca-central-1, one table | pyiceberg 0.12.0, pyarrow 26.0.1 |
| `lakekeeper` | Lakekeeper with RustFS as the object store | Lakekeeper 0.15.1, RustFS 1.0.6, pyiceberg 0.12.0 |
| `polaris` | Polaris with RustFS as the object store | Polaris 1.9.0, RustFS 1.0.6, pyiceberg 0.12.0 |

All times are in milliseconds. "n/a" means the measure does not apply: no schedule to fall behind in an unpaced run, or no compaction for the option. "<1" means under one millisecond. Every run in the table committed all of its batches (`batches_failed` is 0) and passed every verification it ran. One run produced no result and is not in the table: `delta-per-batch` at the ceiling shape (see [Known gaps](#known-gaps)).

| Shape | Option | Backed by | Batches | Writes/s | Commit p50 | Commit p95 | Behind max | Close p50 | Close max | Read trace p50 | Read after compaction p50 | Write errors | Close retries | Verified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rate | `files` | S3, no catalog | 1,800 | 1.28 | 342 | 545 | <1 | 297 | 331 | 5,724 | n/a | 0 | 0 | yes |
| rate | `ducklake` | Postgres in Docker on the runner | 1,800 | 1.28 | 15.6 | 17.5 | <1 | 15.4 | 55.2 | 50.7 | 39.4 | 0 | 0 | yes |
| rate | `ducklake` | Aurora PostgreSQL, provisioned | 1,800 | 1.28 | 93.9 | 109 | <1 | 33.2 | 81.9 | 349 | 95.6 | 0 | 0 | yes |
| rate | `ducklake` | Aurora Serverless v2 | 1,800 | 1.28 | 96.3 | 117 | <1 | 47.4 | 87.3 | 340 | 98.0 | 0 | 0 | yes |
| rate | `iceberg-per-trace` | Iceberg SQL catalog on local Postgres | 1,800 | 1.28 | 3.0 | 3.4 | <1 | 491 | 495 | 13.0 | n/a | 0 | 0 | yes |
| rate | `iceberg-per-trace` | Lakekeeper | 1,800 | 1.28 | 3.0 | 3.3 | <1 | 626 | 681 | 25.0 | n/a | 0 | 0 | yes |
| rate | `iceberg-per-trace` | Polaris | 1,800 | 1.28 | 3.1 | 3.5 | <1 | 603 | 649 | 26.9 | n/a | 0 | 0 | yes |
| rate | `iceberg-per-trace` | Amazon S3 Tables | 1,800 | 1.27 | 341 | 533 | <1 | 9,312 | 9,407 | 271 | n/a | 0 | 0 | yes |
| rate | `iceberg-per-batch` | Iceberg SQL catalog on local Postgres | 1,800 | 1.07 | 1,798 | 32,123 | 1,702,218 | 1,340 | 2,355 | 721 | n/a | 1 | 0 | yes |
| rate † | `files` (rerun) | S3, no catalog | 1,800 | 1.28 | 358 | 530 | 116 | 278 | 287 | 5,854 | n/a | 0 | 0 | yes |
| rate † | `vortex` | S3, no catalog | 1,800 | 1.28 | 361 | 592 | 185 | 283 | 292 | 2,712 | n/a | 0 | 0 | yes |
| rate † | `delta-per-trace` | S3, no catalog | 1,800 | 1.27 | 360 | 600 | 34.3 | 8,810 | 8,933 | 395 | 399 | 0 | 0 | yes |
| rate † | `delta-per-batch` | S3, no catalog | 1,800 | 1.27 | 1,285 | 1,495 | 10.2 | 1,090 | 1,151 | 2,057 | 1,341 | 0 | 0 | yes |
| rate † | `lance-per-trace` | S3, no catalog | 1,800 | 1.27 | 359 | 582 | 8.3 | 9,279 | 10,872 | 971 | 488 | 0 | 0 | yes |
| rate † | `lance-per-batch` | S3, no catalog | 1,800 | 1.27 | 634 | 755 | 5.2 | 2,551 | 3,928 | 4,339 | 346 | 0 | 0 | yes |
| burst | `ducklake` | Postgres in Docker on the runner | 9,000 | 12.75 | 15.0 | 17.1 | <1 | 17.0 | 55.6 | 171 | 126 | 0 | 0 | yes |
| burst | `ducklake` | Aurora PostgreSQL, provisioned | 9,000 | 12.75 | 182 | 346 | <1 | 33.8 | 83.9 | 1,285 | 214 | 0 | 0 | yes |
| burst | `ducklake` | Aurora Serverless v2 | 9,000 | 12.75 | 187 | 345 | <1 | 37.6 | 498 | 1,248 | 217 | 0 | 2 | yes |
| burst | `iceberg-per-trace` | Amazon S3 Tables | 9,000 | 12.08 | 333 | 382 | 1.9 | 39,620 | 39,668 | 403 | n/a | 0 | 0 | yes |
| ceiling | `ducklake` | Postgres in Docker on the runner | 2,000 | 46.18 | 13.3 | 16.5 | n/a | 8.5 | 671 | 313 | 62.7 | 56 | 3 | yes |
| ceiling | `ducklake` | Aurora PostgreSQL, provisioned | 2,000 | 24.34 | 302 | 1,880 | n/a | 18.9 | 267 | 287 | 161 | 8 | 1 | yes |
| ceiling | `ducklake` | Aurora Serverless v2 | 2,000 | 23.59 | 318 | 2,011 | n/a | 17.8 | 484 | 295 | 128 | 15 | 2 | yes |
| ceiling | `iceberg-per-trace` | Iceberg SQL catalog on local Postgres | 2,000 | 431.81 | 3.6 | 6.3 | n/a | 751 | 1,382 | 16.5 | n/a | 0 | 0 | yes |
| ceiling | `iceberg-per-trace` | Amazon S3 Tables | 2,000 | 29.33 | 336 | 440 | n/a | 14,546 | 14,573 | 289 | n/a | 0 | 0 | yes |
| ceiling † | `files` | S3, no catalog | 2,000 | 44.08 | 345 | 416 | n/a | 280 | 289 | 6,377 | n/a | 0 | 0 | yes |
| ceiling † | `vortex` | S3, no catalog | 2,000 | 42.14 | 358 | 437 | n/a | 258 | 271 | 3,684 | n/a | 0 | 0 | yes |
| ceiling † | `delta-per-trace` | S3, no catalog | 2,000 | 30.48 | 354 | 431 | n/a | 13,738 | 13,770 | 389 | 340 | 0 | 0 | yes |
| ceiling † | `lance-per-trace` | S3, no catalog | 2,000 | 28.90 | 355 | 422 | n/a | 14,645 | 14,676 | 577 | 481 | 0 | 0 | yes |
| ceiling † | `lance-per-batch` | S3, no catalog | 2,000 | 3.57 | 864 | 10,746 | n/a | 2,858 | 3,141 | 3,608 | 369 | 135 | 0 | yes |
| big | `files` | S3, no catalog | 9,000 | 47.26 | 329 | 367 | n/a | 317 | 317 | 109,767 | n/a | 0 | 0 | yes |
| big | `ducklake` | Aurora Serverless v2 | 9,000 | 24.27 | 313 | 1,925 | n/a | 88.4 | 88.4 | 4,350 | 531 | 62 | 0 | yes |
| big | `iceberg-per-trace` | Amazon S3 Tables | 9,000 | 21.16 | 332 | 379 | n/a | 237,338 | 237,338 | 592 | n/a | 0 | 0 | yes |
| rate-small | `iceberg-per-batch` | Amazon S3 Tables | 900 | 1.13 | 6,133 | 25,678 | 49,679 | 26,645 | 27,368 | 2,167 | n/a | 0 | 0 | yes |
| half-rate | `iceberg-per-batch` | Amazon S3 Tables | 600 | 0.63 | 1,605 | 1,972 | 8.3 | 17,460 | 17,973 | 1,573 | n/a | 0 | 0 | yes |
| ceiling-small | `iceberg-per-batch` | Amazon S3 Tables | 300 | 1.48 | 8,016 | 22,170 | n/a | 15,111 | 15,111 | 611 | n/a | 0 | 0 | yes |

### What the numbers say

- **Committing to Iceberg on every batch did not hold 1.25 batches/s.** On the Iceberg SQL catalog it wrote 1.07 batches/s with a p95 commit of 32 s, and batches started up to 1,702 s behind schedule. On S3 Tables with three traces it wrote 1.13 batches/s and fell 49.7 s behind. At half the rate (0.63 batches/s) S3 Tables kept to within 8.3 ms.
- **One commit per trace kept to schedule on all four Iceberg catalogs**, because a batch is then a plain file write: 3 ms at the median on the SQL catalog, Lakekeeper and Polaris runs, and 341 ms on the S3 Tables run.
- **The cost moves to closing the trace.** On S3 Tables the slowest close took 9.4 s for a 300-batch trace, 14.6 s for 500, 39.7 s for 1,500 and 237 s for 9,000. During that time the trace is written but not yet in the table.
- **DuckLake's unpaced rate roughly halved on Aurora.** It wrote 46.2 batches/s with Postgres on the runner, 24.3 on provisioned Aurora and 23.6 on Aurora Serverless v2. The two Aurora unpaced rates are within about 3% of each other, which one run each cannot separate.
- **DuckLake produced write errors only when unpaced.** The paced runs had none. The unpaced runs had 8 to 62 failed batch writes, the recorded error type is `TransactionException` in each, and every batch was acknowledged after redelivery.
- **DuckLake's trace reads were faster after compaction in every run**, for example from 349 ms to 95.6 ms on provisioned Aurora at the rate shape, and from 4,350 ms to 531 ms for the 9,000-batch trace.
- **Reading a trace from plain files is slow and grows with the trace**: 5.7 s at the median for a 300-batch trace and 110 s for the 9,000-batch trace.

From the third set of runs (†). The rate figures include contention between the six runs that shared the instance:

- **Delta Lake and Lance kept to 1.25 batches/s while committing on every batch.** A batch took 1,285 ms at the median on `delta-per-batch` and 634 ms on `lance-per-batch`, with a p95 of 1,495 ms and 755 ms, and no batch started more than 10.2 ms late. `iceberg-per-batch` at the same rate had a p95 of 26 s on S3 Tables and 32 s on the Iceberg SQL catalog and fell behind on both.
- **Unpaced, committing on every batch did not hold up on either.** `lance-per-batch` wrote 3.57 batches/s, with a p95 batch write of 10.7 s and 135 failed batch writes during the load. The file records 153 `OSError`s in all, the load's and those from reprocessing; the recorded example is "Too many concurrent writers. Attempted 8 times, but failed on retry_timeout of 30.000 seconds". Every batch was acknowledged after redelivery and the run verified. `delta-per-batch` produced no result at all.
- **Closing a trace cost about the same on Delta Lake, Lance and S3 Tables when committing once per trace.** The slowest close of a 300-batch trace took 8.9 s on `delta-per-trace`, 10.9 s on `lance-per-trace` and 9.4 s on `iceberg-per-trace` with S3 Tables. For a 500-batch trace, unpaced, it took 13.8 s, 14.7 s and 14.6 s. Committing on every batch made the close shorter: 1.2 s on `delta-per-batch` and 3.9 s on `lance-per-batch` at the rate shape.
- **A batch write with one commit per trace is a plain file write on all three**: 360 ms on `delta-per-trace`, 359 ms on `lance-per-trace` and 341 ms on `iceberg-per-trace` with S3 Tables, against 358 ms for `files` beside them.
- **Reading a trace from a table committed once per trace took under a second**: 395 ms from `delta-per-trace` and 971 ms from `lance-per-trace` at the rate shape, against 271 ms from `iceberg-per-trace` on S3 Tables. Compaction halved the Lance read to 488 ms and left the Delta Lake read where it was (399 ms).
- **The tables committed on every batch were slow to read until compacted.** `lance-per-batch` took 4,339 ms to read a trace before compaction and 346 ms after. `delta-per-batch` took 2,057 ms before and 1,341 ms after.
- **Vortex files read back about twice as fast as Parquet files**: 2.7 s against 5.9 s for a 300-batch trace in the same set of runs, and 3.7 s against 6.4 s for a 500-batch trace. Batch writes took the same time (361 ms and 358 ms). The readers differ as well as the formats: `vortex` reads through DuckDB and `files` through pyarrow.
- **The rerun of `files` came out close to the first run**: batch write 358 ms against 342 ms, trace read 5.9 s against 5.7 s. It started batches up to 116 ms late where the first run was under 1 ms late. All six † rate runs were between 5 and 185 ms late at worst; sharing the instance is the likely cause, and it was not isolated.
- **DuckLake on Aurora is still far faster to close a trace** (82 to 87 ms at the rate shape) than any option here that keeps a table without a database.

## Known gaps

- Every figure here is from one run.
- The six † rate runs ran at the same time on one instance, so they competed for its CPU and network, and the rows without a † did not. A small gap between a † row and another row is not a difference between options.
- `delta-per-batch` has no ceiling result. With the pacing removed, delta-rs timed out fetching instance credentials from the instance metadata service, and closing a trace failed. The option opens the table on every call and each open loads credentials. It was not a commit conflict. Two attempts ended the same way; the cause was read from the second, which had extra logging. The option now keeps one open table per process, but that change has not been run on S3, so the result is still missing.
- In the run above the harness carried on to the read step after the failed close and reported a DuckDB `BinderException` from there. It now stops with the close error instead.
- How Delta Lake and Lance arbitrate concurrent commits on S3 in this setup was not examined. No lock table was used. The runs verified, which says no row was lost, duplicated or left stale in these runs and nothing more general.
- The Delta Lake, Lance and Vortex options were run only at the rate and ceiling shapes, and only with no catalog service.
- `vortex` and `files` use different readers (DuckDB and pyarrow), so the difference in their read times is not the file format alone.
- The Iceberg options cannot yet reset Lakekeeper or Polaris for a second run, so those two have one run each, at the rate shape.
- `iceberg-per-batch` on S3 Tables was run only at the three smaller shapes, so it is not directly comparable with the six-trace rate runs.
- The result files do not record the Aurora instance class or capacity setting, or where the batch data files were written for the catalog-backed options. The `files`, `vortex`, Delta Lake and Lance runs record S3. The Aurora sizes in the table above and the data locations below come from the run setup, not from the files. The capacity Serverless v2 actually used during a run was not recorded.
- **Batch data did not go to the same place in every run.** The `s3`, `aurora-prov`, `aurora-sv2` and `s3tables` runs wrote batch data to S3. The `local-postgres`, `sql`, `lakekeeper` and `polaris` runs wrote it to the runner's instance-store NVMe (through RustFS for Lakekeeper and Polaris). The fast batch commits of `iceberg-per-trace` on the SQL catalog, Lakekeeper and Polaris (about 3 ms) are local disk writes, and are not comparable with the 341 ms S3 writes on the S3 Tables run. The same applies to DuckLake's 15.6 ms on local Postgres against 94 ms on Aurora.
- Error messages in the published files have long numeric identifiers replaced with `<id>` and home directories with `<home>`.

To add an option, see [adding-a-system.md](adding-a-system.md#add-a-pipeline-option).
