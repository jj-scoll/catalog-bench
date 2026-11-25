from bench.options import _delta
from bench.options._tables import PerTrace


class Option(PerTrace):
    """Delta Lake through delta-rs, one commit per trace, with no catalog: the table's log is in the data directory.
    Each batch is written as plain Parquet files outside the table, at a key fixed by trace, version and batch, so a
    second delivery overwrites the first; a reader that wants a fresh batch reads that file. Closing a trace reads its
    files back and replaces the trace's rows in the output table, then in the errors table, as two commits. Whole-trace
    readers use DuckDB's delta extension. The files are kept. Maintenance compacts the two tables."""

    name, format = "delta-per-trace", _delta
