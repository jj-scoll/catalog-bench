import base64
import re
import subprocess
import urllib.parse
from dataclasses import dataclass

import pyarrow as pa
import pyiceberg
from pyiceberg.catalog import load_catalog

from bench.config import (
    POLARIS_CREDENTIAL,
    POLARIS_URL,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_ENDPOINT_INTERNAL,
    S3_REGION,
)
from bench.systems.iceberg_sql_postgres import SCHEMA, _Conn
from bench.systems.lakekeeper import (
    TABLE,
    drop_table,
    empty_warehouse,
    http,
    object_store_version,
)

CATALOG = "bench"
SCOPE = "PRINCIPAL_ROLE:ALL"


@dataclass
class System:
    """Apache Polaris (Iceberg REST catalog, metadata in Postgres) via pyiceberg, data in a local RustFS object store.
    One single-row append per commit, with pyiceberg's default retry-with-backoff. Server and client defaults."""

    name = "polaris"

    def _catalog(self):
        return load_catalog(
            "polaris", type="rest", uri=f"{POLARIS_URL}/api/catalog", warehouse=CATALOG, credential=POLARIS_CREDENTIAL, scope=SCOPE
        )

    def _token(self):
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
        basic = base64.b64encode(POLARIS_CREDENTIAL.encode()).decode()
        headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}
        return http("POST", f"{POLARIS_URL}/api/catalog/v1/oauth/tokens", form, headers)["access_token"]

    def setup(self):
        catalogs = f"{POLARIS_URL}/api/management/v1/catalogs"
        auth = {"Authorization": f"Bearer {self._token()}"}
        location = f"s3://{S3_BUCKET}/{self.name}"
        # Recreate the catalog rather than the table: it is the only way to leave nothing behind in Polaris.
        if http("GET", f"{catalogs}/{CATALOG}", headers=auth, missing_ok=True):
            drop_table(self._catalog())
            http("DELETE", f"{catalogs}/{CATALOG}", headers=auth)
        empty_warehouse(self.name)
        http("POST", catalogs, headers=auth, body={"catalog": {
            "name": CATALOG, "type": "INTERNAL", "readOnly": False,
            "properties": {"default-base-location": location},
            "storageConfigInfo": {
                "storageType": "S3", "allowedLocations": [location], "region": S3_REGION, "pathStyleAccess": True,
                "endpoint": S3_ENDPOINT, "endpointInternal": S3_ENDPOINT_INTERNAL,
            },
        }})  # fmt: skip
        grant = {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}  # lets the bootstrap principal write tables
        http("PUT", f"{catalogs}/{CATALOG}/catalog-roles/catalog_admin/grants", grant, auth)
        catalog = self._catalog()
        catalog.create_namespace("bench")
        catalog.create_table(TABLE, schema=SCHEMA)

    def connect(self):
        return _Conn(self._catalog())

    def count(self):
        return self._catalog().load_table(TABLE).scan().to_arrow().num_rows

    def version(self):
        # Polaris has no version endpoint or header, so ask the running compose container which server jar it holds.
        command = ["docker", "compose", "exec", "-T", "polaris", "ls", "/deployments/app"]
        try:
            listing = subprocess.run(command, check=False, capture_output=True, text=True).stdout
        except OSError:  # no docker here, e.g. BENCH_POLARIS_URL points at a server started some other way
            listing = ""
        found = re.search(r"polaris-server-(.+)\.jar", listing)
        server = found.group(1) if found else "(version unknown: not exposed over HTTP, and no compose container found)"
        return f"Polaris {server}, {object_store_version()}, pyiceberg {pyiceberg.__version__}, pyarrow {pa.__version__}"
