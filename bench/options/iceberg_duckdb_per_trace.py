from dataclasses import dataclass

import pyarrow.fs as pafs
import pyarrow.parquet as pq

from bench.config import PIPELINE_DATA
from bench.options import iceberg_per_trace
from bench.options._iceberg_duckdb import CATALOG, Base, Conn


@dataclass
class Option(Base):
    """Iceberg through DuckDB's iceberg extension, one commit per trace. The same steps as iceberg-per-trace with a
    different client: each batch is written as plain Parquet files outside the table, at a key fixed by trace, version
    and batch, so a second delivery overwrites the first; a reader that wants a fresh batch reads that file. Closing a
    trace reads its files back and replaces the trace's rows in the output table, then in the errors table, as two
    commits. Whole-trace readers read the table. The files are kept. No maintenance is run."""

    catalog: str = CATALOG
    tail: str = f"{PIPELINE_DATA}/tail-duckdb"
    name = "iceberg-duckdb-per-trace"

    @classmethod
    def local(cls, directory):  # not service-free: the tests skip it unless Lakekeeper from compose.yaml is running
        return cls(catalog="lakekeeper", tail=f"{directory}/tail")

    def setup(self):
        super().setup()
        fs, root = pafs.FileSystem.from_uri(self.tail)
        fs.create_dir(root, recursive=True)
        fs.delete_dir_contents(root)

    def connect(self):
        return _Conn(self.catalog, *pafs.FileSystem.from_uri(self.tail))


class _Conn(Conn, iceberg_per_trace._Conn):  # the batch files are written and probed exactly as in iceberg-per-trace
    def __init__(self, catalog, fs, root):
        Conn.__init__(self, catalog)
        self.fs, self.root, self.made = fs, root, set()

    def finalize(self, trace, version):
        for kind in ("output", "errors"):
            rows = pq.read_table(self._dir(kind, trace, version), filesystem=self.fs)
            self.do(f"BEGIN; DELETE FROM ice.pipeline.{kind} WHERE trace = {trace}; INSERT INTO ice.pipeline.{kind} SELECT * FROM rows; COMMIT", rows)

    def read_trace(self, trace):
        return self.db.sql(f"SELECT version, batch, row FROM ice.pipeline.output WHERE trace = {trace}").to_arrow_table()
