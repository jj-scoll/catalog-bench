"""Delta Lake, written with delta-rs and read with DuckDB's delta extension."""

import deltalake
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError

EXTENSION, SUFFIX, CONFLICT = "delta", "", (CommitFailedError,)


def create(path, schema):
    by = ["trace"] if "batch" in schema.names else None  # the data tables are partitioned by trace, the control table is not
    DeltaTable.create(path, schema=deltalake.Schema.from_arrow(schema), partition_by=by)


def append(path, rows):
    write_deltalake(path, rows, mode="append")


def delete(path, predicate):
    DeltaTable(path).delete(predicate)


def replace(path, trace, version, rows):
    write_deltalake(path, rows, mode="overwrite", predicate=f"trace = {trace}")


def scan(path):
    return f"delta_scan('{path}')"


def compact(path):
    DeltaTable(path).optimize.compact()


def version():
    return f"deltalake {deltalake.__version__} (delta-rs)"
