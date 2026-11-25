"""Delta Lake, written with delta-rs and read with DuckDB's delta extension."""

import deltalake
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError

EXTENSION, SUFFIX, CONFLICT = "delta", "", (CommitFailedError,)


_open = {}  # one DeltaTable per path in each process: opening one loads credentials again, which timed out under load


def _table(path):
    if path in _open:
        _open[path].update_incremental()
    else:
        _open[path] = DeltaTable(path)
    return _open[path]


def create(path, schema):
    by = ["trace"] if "batch" in schema.names else None  # the data tables are partitioned by trace, the control table is not
    _open[path] = DeltaTable.create(path, schema=deltalake.Schema.from_arrow(schema), partition_by=by)


def append(path, rows):
    write_deltalake(_table(path), rows, mode="append")


def delete(path, predicate):
    _table(path).delete(predicate)


def replace(path, trace, version, rows):
    write_deltalake(_table(path), rows, mode="overwrite", predicate=f"trace = {trace}")


def scan(path):
    return f"delta_scan('{path}')"


def compact(path):
    _table(path).optimize.compact()


def version():
    return f"deltalake {deltalake.__version__} (delta-rs)"
