# Running the benchmark

The basic commands are in the [README](../README.md#quick-start). This page covers the services that need more than that.

## Settings

`--systems` picks a subset of systems; the default is all of them. Connection settings come from `BENCH_PG` and `BENCH_DATA_PATH` (see `bench/config.py`).

## Lakekeeper and Polaris

`lakekeeper` and `polaris` need no setup beyond the compose file: it starts both catalogs (ports 58181 and 58182), a RustFS object store for their data (port 59000) and a database for each in the Postgres service, and `setup()` creates the bucket and warehouse.

RustFS writes to `.data/rustfs` as its own container user, so empty that directory through Docker:

```sh
docker compose run --rm --entrypoint sh rustfs-perms -c 'rm -rf /data/*'
```

Their settings are the `BENCH_S3_*`, `BENCH_LAKEKEEPER_URL` and `BENCH_POLARIS_*` variables in `bench/config.py`.

## Amazon S3 Tables

`s3-tables` is skipped unless `BENCH_S3TABLES_ARN` names a table bucket you own (`arn:aws:s3tables:<region>:<account>:bucket/<name>`). Credentials come from the standard AWS environment.

Run it from a machine in the bucket's region, or the numbers measure your uplink.

`BENCH_S3TABLES_TABLES` sets how many tables the writers are spread over; set it to the writer count to give each writer its own table.

### Certificate verification errors

If every request fails with a certificate verification error, the Python process is not finding your system's CA bundle. Pointing it at the bundle `certifi` ships fixed it on the one machine where this happened; which of the three variables is the one that matters has not been isolated:

```sh
export SSL_CERT_FILE="$(uv run python -m certifi)"
export AWS_CA_BUNDLE="$SSL_CERT_FILE" CURL_CA_BUNDLE="$SSL_CERT_FILE"
```

## The pipeline workload

The two Iceberg options run on any Iceberg system in `bench/systems`, chosen with `BENCH_ICEBERG_CATALOG` (`iceberg-sql-postgres`, `s3-tables`, `lakekeeper` or `polaris`). `ducklake` uses the Postgres in `BENCH_PG`. `BENCH_PIPELINE_DATA` is where `files`, `ducklake` and the `iceberg-per-trace` files go: a local directory or an `s3://` URI. The rest is in [pipeline.md](pipeline.md).
