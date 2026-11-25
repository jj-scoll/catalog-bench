import shutil
import time
from dataclasses import dataclass

import psycopg
import pyarrow as pa
import pyiceberg
from pyiceberg.catalog.sql import SqlCatalog

from bench.config import ICEBERG_WAREHOUSE, PG, PG_URL

SCHEMA = pa.schema([("writer", pa.int32()), ("seq", pa.int32())])


@dataclass
class System:
    """Iceberg via pyiceberg with the plain SQL catalog in Postgres, data on local disk. One single-row append per
    commit, with pyiceberg's default retry-with-backoff when a commit loses the optimistic-concurrency race."""

    name = "iceberg-sql-postgres"

    def _catalog(self):
        return SqlCatalog("bench", uri=f"{PG_URL}/iceberg", warehouse=f"file://{ICEBERG_WAREHOUSE}")

    def setup(self):
        with psycopg.connect(f"{PG} dbname=postgres", autocommit=True) as pg:
            pg.execute("DROP DATABASE IF EXISTS iceberg WITH (FORCE)")
            pg.execute("CREATE DATABASE iceberg")
        shutil.rmtree(ICEBERG_WAREHOUSE, ignore_errors=True)
        catalog = self._catalog()
        catalog.create_namespace("bench")
        catalog.create_table("bench.commits", schema=SCHEMA)

    def connect(self):
        return _Conn(self._catalog())

    def count(self):
        return self._catalog().load_table("bench.commits").scan().to_arrow().num_rows

    def version(self):
        return f"pyiceberg {pyiceberg.__version__}, pyarrow {pa.__version__}, SQL catalog on PostgreSQL"


class _Conn:
    def __init__(self, catalog, table="bench.commits"):
        self.table = catalog.load_table(table)
        self.catalog_seconds = 0.0
        commit_table = catalog.commit_table

        def timed(*args, **kwargs):  # the only part of an append that talks to the catalog
            start = time.monotonic()
            try:
                return commit_table(*args, **kwargs)
            finally:
                self.catalog_seconds = time.monotonic() - start

        catalog.commit_table = timed

    def commit_one(self, writer, seq):
        try:
            self.table.append(pa.table({"writer": [writer], "seq": [seq]}, schema=SCHEMA))
        except Exception:
            self.table.refresh()  # pick up the winner's snapshot so the next attempt starts current
            raise
        return self.catalog_seconds

    def close(self): ...
