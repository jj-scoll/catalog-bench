from bench.options import _lance
from bench.options._tables import PerTrace


class Option(PerTrace):
    """Lance datasets, one append per trace, with no catalog: each dataset's manifests are in the data directory.
    Each batch is written as plain Parquet files outside the dataset, at a key fixed by trace, version and batch, so a
    second delivery overwrites the first; a reader that wants a fresh batch reads that file. Closing a trace reads its
    files back, appends them to the output dataset in one commit and deletes the trace's older versions in a second,
    then does the same for the errors dataset. Whole-trace readers use DuckDB's lance extension and take the highest
    version present. The files are kept. Maintenance compacts the two datasets."""

    name, format = "lance-per-trace", _lance
