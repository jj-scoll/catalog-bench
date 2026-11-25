from bench.options import _delta
from bench.options._tables import PerBatch


class Option(PerBatch):
    """Delta Lake through delta-rs, one commit per batch, with no catalog: the table's log is in the data directory.
    Each batch appends its output rows and then its error rows, as two commits, to tables partitioned by trace. Closing
    a trace deletes the trace's older versions and appends its new version to a control table, as three more commits.
    Readers use DuckDB's delta extension, and take the recorded version and one delivery per batch. Maintenance
    compacts the two data tables."""

    name, format = "delta-per-batch", _delta
