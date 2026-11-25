"""Connection settings shared by systems. Defaults match compose.yaml; override with environment variables."""

import os

PG = os.environ.get("BENCH_PG", "host=localhost port=55432 user=postgres password=bench")
DATA_PATH = os.environ.get("BENCH_DATA_PATH", os.path.abspath(".data/lake")) + "/"
PG_URL = os.environ.get("BENCH_PG_URL", "postgresql+psycopg2://postgres:bench@localhost:55432")
# Where pipeline options keep their files: a local directory or an s3://bucket/prefix URI.
PIPELINE_DATA = os.environ.get("BENCH_PIPELINE_DATA", os.path.abspath(".data/pipeline")).rstrip("/")
ICEBERG_WAREHOUSE = os.environ.get("BENCH_ICEBERG_WAREHOUSE", os.path.abspath(".data/iceberg"))

# Object store and REST catalogs (lakekeeper, polaris). The credentials are the throwaway ones in compose.yaml.
S3_ENDPOINT = os.environ.get("BENCH_S3_ENDPOINT", "http://localhost:59000").rstrip("/")
S3_ENDPOINT_INTERNAL = os.environ.get("BENCH_S3_ENDPOINT_INTERNAL", "http://rustfs:59000").rstrip("/")  # as Polaris sees it
S3_ACCESS_KEY = os.environ.get("BENCH_S3_ACCESS_KEY", "bench")
S3_SECRET_KEY = os.environ.get("BENCH_S3_SECRET_KEY", "benchbench")
S3_REGION = os.environ.get("BENCH_S3_REGION", "us-east-1")
S3_BUCKET = os.environ.get("BENCH_S3_BUCKET", "catalog-bench").strip("/")
LAKEKEEPER_URL = os.environ.get("BENCH_LAKEKEEPER_URL", "http://localhost:58181").rstrip("/")
POLARIS_URL = os.environ.get("BENCH_POLARIS_URL", "http://localhost:58182").rstrip("/")
POLARIS_CREDENTIAL = os.environ.get("BENCH_POLARIS_CREDENTIAL", "root:s3cr3t")  # client-id:client-secret
