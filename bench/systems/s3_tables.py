import os
from dataclasses import dataclass

import pyarrow as pa
import pyiceberg
from pyiceberg.catalog import load_catalog

from bench.systems.iceberg_sql_postgres import SCHEMA, _Conn

ARN = os.environ.get("BENCH_S3TABLES_ARN", "")  # arn:aws:s3tables:<region>:<account>:bucket/<name>
# Writer w commits to table w mod TABLES. Set it to the writer count to give each writer its own table, which takes
# Iceberg's optimistic concurrency out of the picture and leaves the service's own request limit.
TABLES = int(os.environ.get("BENCH_S3TABLES_TABLES", "1"))


def _table(writer):
    return "bench.commits" if TABLES == 1 else f"bench.commits_{writer % TABLES}"


@dataclass
class System:
    """Amazon S3 Tables through its Iceberg REST endpoint, via pyiceberg. One single-row append per commit, with
    pyiceberg's default retry-with-backoff. Credentials come from the standard AWS environment."""

    name = "s3-tables"
    requires_env = "BENCH_S3TABLES_ARN"

    def _catalog(self):
        region = ARN.split(":")[3]
        return load_catalog(
            "s3tables",
            **{
                "type": "rest",
                "uri": f"https://s3tables.{region}.amazonaws.com/iceberg",
                "warehouse": ARN,
                "rest.sigv4-enabled": "true",
                "rest.signing-name": "s3tables",
                "rest.signing-region": region,
                "s3.region": region,
            },
        )

    def setup(self):
        catalog = self._catalog()
        catalog.create_namespace_if_not_exists("bench")
        for table in catalog.list_tables("bench"):
            catalog.purge_table(table)
        for i in range(TABLES):
            catalog.create_table(_table(i), schema=SCHEMA)

    def teardown(self):
        """Remove everything this system created inside the table bucket."""
        catalog = self._catalog()
        for table in catalog.list_tables("bench"):
            catalog.purge_table(table)
        catalog.drop_namespace("bench")

    def connect(self):
        return _PerWriter(self._catalog())

    def count(self):
        catalog = self._catalog()
        return sum(catalog.load_table(_table(i)).scan().to_arrow().num_rows for i in range(TABLES))

    def version(self):
        return (f"pyiceberg {pyiceberg.__version__}, pyarrow {pa.__version__}, S3 Tables REST endpoint in "
                f"{ARN.split(':')[3]}, {TABLES} table(s)")


class _PerWriter:
    """Opens the writer's table on its first commit, because connect() is not told which writer it serves."""

    def __init__(self, catalog):
        self.catalog, self.conn = catalog, None

    def commit_one(self, writer, seq):
        if self.conn is None:
            self.conn = _Conn(self.catalog, _table(writer))
        return self.conn.commit_one(writer, seq)

    def close(self): ...
