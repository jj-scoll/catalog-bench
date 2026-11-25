"""What the two DuckDB Iceberg options share: the tables of the pyiceberg options, written and read through DuckDB's
iceberg extension instead. DuckDB writes Iceberg only through a REST catalog, so the catalog is Lakekeeper or S3 Tables."""

import os
import time

import duckdb

from bench.config import (
    LAKEKEEPER_URL,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
)
from bench.options._iceberg import ATTEMPTS
from bench.options._iceberg import Base as Tables
from bench.systems import SYSTEMS
from bench.systems.lakekeeper import WAREHOUSE
from bench.systems.s3_tables import ARN

CATALOG = os.environ.get("BENCH_ICEBERG_CATALOG", "lakekeeper")


def attach(catalog):
    """A DuckDB connection with the catalog attached as `ice`."""
    db = duckdb.connect()
    db.execute("INSTALL iceberg; LOAD iceberg; SET enable_progress_bar = false")
    if catalog == "lakekeeper":
        scheme, host = S3_ENDPOINT.split("://")
        db.execute(
            f"CREATE SECRET (TYPE s3, KEY_ID '{S3_ACCESS_KEY}', SECRET '{S3_SECRET_KEY}', ENDPOINT '{host}', "
            f"URL_STYLE 'path', USE_SSL {scheme == 'https'}, REGION '{S3_REGION}')"
        )
        db.execute(f"ATTACH '{WAREHOUSE}' AS ice (TYPE iceberg, ENDPOINT '{LAKEKEEPER_URL}/catalog', AUTHORIZATION_TYPE 'none')")
    elif catalog == "s3-tables":
        db.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
        db.execute(f"ATTACH '{ARN}' AS ice (TYPE iceberg, ENDPOINT_TYPE s3_tables)")
    else:
        raise ValueError(f"DuckDB writes Iceberg through a REST catalog: lakekeeper or s3-tables, not {catalog}")
    return db


class Base(Tables):
    """The tables are created by pyiceberg, exactly as for the pyiceberg options, so the two clients meet the same
    schema and partitioning. Setup is not timed."""

    local_dir = ""

    def version(self):
        return f"written and read with duckdb {duckdb.__version__} (iceberg extension); {SYSTEMS[self.catalog]().version()}"


class Conn:
    def __init__(self, catalog):
        self.db, self.retries = attach(catalog), 0

    def do(self, sql, rows=None):  # rows is read by sql, as a table named rows
        """Run sql as one commit, again if another writer's commit got in first: DuckDB reports the conflict and
        leaves the retry to the caller. The same number of tries and the same waits as the pyiceberg options."""
        for attempt in range(ATTEMPTS):
            try:
                self.db.execute(sql)
                self.retries += attempt
                return
            except duckdb.TransactionException as e:
                if "Failed to commit" not in str(e) or attempt == ATTEMPTS - 1:
                    raise
                if "BEGIN" in sql:
                    try:
                        self.db.execute("ROLLBACK")
                    except duckdb.Error:  # the failed commit already ended the transaction
                        pass
                time.sleep(min(2.0, 0.05 * 2**attempt))

    def close(self):
        self.db.close()
