import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.fs as pafs
import pyiceberg
from pyiceberg.catalog import load_catalog

from bench.config import (
    LAKEKEEPER_URL,
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
)
from bench.systems.iceberg_sql_postgres import SCHEMA, _Conn

TABLE = "bench.commits"
WAREHOUSE = "bench"


def http(method, url, body=None, headers=None, missing_ok=False):
    """Call a catalog's management API with a JSON body (or a ready-encoded one) and return the decoded JSON reply."""
    data = body if isinstance(body, bytes | None) else json.dumps(body).encode()
    request = urllib.request.Request(url, data, {"Content-Type": "application/json", **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or "null")
    except urllib.error.HTTPError as e:
        if missing_ok and e.code == 404:
            return None
        raise RuntimeError(f"{method} {url}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from None


def drop_table(catalog):
    """Remove every table and namespace from a catalog: the pipeline options leave their own beside the benchmark's,
    and a warehouse that still holds any cannot be deleted."""
    for namespace in catalog.list_namespaces():
        for table in catalog.list_tables(namespace):
            catalog.drop_table(table)
        catalog.drop_namespace(namespace)


def empty_warehouse(prefix):
    """Create the bucket if needed and delete every object under `prefix`, so no earlier run's files remain."""
    s3 = pafs.S3FileSystem(
        endpoint_override=S3_ENDPOINT, access_key=S3_ACCESS_KEY, secret_key=S3_SECRET_KEY, region=S3_REGION,
        allow_bucket_creation=True,
    )  # fmt: skip
    s3.create_dir(S3_BUCKET)
    for attempt in range(10):  # RustFS answers a large delete after a busy run with "service unavailable, please retry"
        try:
            return s3.delete_dir_contents(f"{S3_BUCKET}/{prefix}", missing_dir_ok=True)
        except OSError:
            if attempt == 9:
                raise
            time.sleep(3)


def object_store_version():
    return f"RustFS {http('GET', f'{S3_ENDPOINT}/health').get('version', 'unknown')}"


@dataclass
class System:
    """Lakekeeper (Iceberg REST catalog, metadata in Postgres) via pyiceberg, data in a local RustFS object store.
    One single-row append per commit, with pyiceberg's default retry-with-backoff. Server and client defaults."""

    name = "lakekeeper"

    def _catalog(self):
        return load_catalog("lakekeeper", type="rest", uri=f"{LAKEKEEPER_URL}/catalog", warehouse=WAREHOUSE)

    def setup(self):
        management = f"{LAKEKEEPER_URL}/management/v1"
        if not http("GET", f"{management}/info")["bootstrapped"]:
            http("POST", f"{management}/bootstrap", {"accept-terms-of-use": True})
        # Recreate the warehouse rather than the table: it is the only way to leave nothing behind in the catalog.
        for warehouse in http("GET", f"{management}/warehouse")["warehouses"]:
            if warehouse["name"] == WAREHOUSE:
                drop_table(self._catalog())
                http("DELETE", f"{management}/warehouse/{warehouse['id']}")
        empty_warehouse(self.name)
        http("POST", f"{management}/warehouse", {
            "warehouse-name": WAREHOUSE,
            "storage-profile": {
                "type": "s3", "flavor": "s3-compat", "bucket": S3_BUCKET, "key-prefix": self.name,
                "endpoint": S3_ENDPOINT, "region": S3_REGION, "path-style-access": True, "sts-enabled": True,
            },
            "storage-credential": {
                "type": "s3", "credential-type": "access-key",
                "aws-access-key-id": S3_ACCESS_KEY, "aws-secret-access-key": S3_SECRET_KEY,
            },
        })  # fmt: skip
        catalog = self._catalog()
        catalog.create_namespace("bench")
        catalog.create_table(TABLE, schema=SCHEMA)

    def connect(self):
        return _Conn(self._catalog())

    def count(self):
        return self._catalog().load_table(TABLE).scan().to_arrow().num_rows

    def version(self):
        server = http("GET", f"{LAKEKEEPER_URL}/management/v1/info")["version"]
        return f"Lakekeeper {server}, {object_store_version()}, pyiceberg {pyiceberg.__version__}, pyarrow {pa.__version__}"
