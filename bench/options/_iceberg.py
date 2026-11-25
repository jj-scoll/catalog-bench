"""What the two Iceberg options share: which catalog to use, and the tables."""

import os
import time

import pyarrow as pa
import pyiceberg
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import DoubleType, IntegerType, NestedField, StringType

from bench.pipeline import SCHEMA
from bench.systems import SYSTEMS

# Any Iceberg system in bench/systems: iceberg-sql-postgres, s3-tables, lakekeeper or polaris.
CATALOG = os.environ.get("BENCH_ICEBERG_CATALOG", "iceberg-sql-postgres")
CONTROL = pa.schema([("trace", pa.int32()), ("version", pa.int32())])
TYPES = {pa.int32(): IntegerType(), pa.float64(): DoubleType(), pa.string(): StringType()}
TABLE = Schema(*(NestedField(i, f.name, TYPES[f.type], required=False) for i, f in enumerate(SCHEMA, start=1)))  # trace is field 1
BY_TRACE = PartitionSpec(PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="trace"))
COLUMNS = ("version", "batch", "attempt", "row")
ATTEMPTS = 10


class Base:
    """catalog names a system in bench/systems; local_dir replaces it with a SQLite catalog for the tests."""

    def _catalog(self):
        if self.local_dir:
            return SqlCatalog("local", uri=f"sqlite:///{self.local_dir}/catalog.db", warehouse=f"file://{self.local_dir}/warehouse")
        return SYSTEMS[self.catalog]()._catalog()

    def setup(self):
        if self.local_dir:
            os.makedirs(self.local_dir, exist_ok=True)
        else:
            SYSTEMS[self.catalog]().setup()  # leaves that system's own empty table behind, and a clean catalog
        catalog = self._catalog()
        catalog.create_namespace_if_not_exists("pipeline")
        for table in catalog.list_tables("pipeline"):
            catalog.purge_table(table)
        catalog.create_table("pipeline.output", schema=TABLE, partition_spec=BY_TRACE)
        catalog.create_table("pipeline.errors", schema=TABLE, partition_spec=BY_TRACE)
        catalog.create_table("pipeline.control", schema=CONTROL)

    def version(self):
        where = "a local SQLite catalog" if self.local_dir else SYSTEMS[self.catalog]().version()
        return f"pyiceberg {pyiceberg.__version__}, pyarrow {pa.__version__}; {where}"


def commit(table, change):
    """Apply change(table) on the table's latest state, again if another writer's commit got in first. Returns the
    number of extra tries, on top of whatever retrying pyiceberg does inside one call."""
    for attempt in range(ATTEMPTS):
        table.refresh()
        try:
            change(table)
            return attempt
        except CommitFailedException:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(min(2.0, 0.05 * 2**attempt))
