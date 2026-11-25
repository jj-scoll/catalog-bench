from bench.options import _lance
from bench.options._tables import PerBatch


class Option(PerBatch):
    """Lance datasets, one commit per batch, with no catalog: each dataset's manifests are in the data directory.
    Each batch appends its output rows and then its error rows, as two commits. Closing a trace deletes the trace's
    older versions and appends its new version to a control dataset, as three more commits. Readers use DuckDB's lance
    extension, and take the recorded version and one delivery per batch. Maintenance compacts the two data datasets."""

    name, format = "lance-per-batch", _lance
