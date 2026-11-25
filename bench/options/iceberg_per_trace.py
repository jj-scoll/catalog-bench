from dataclasses import dataclass

import pyarrow.fs as pafs
import pyarrow.parquet as pq
from pyiceberg.expressions import EqualTo

from bench.config import PIPELINE_DATA
from bench.options._iceberg import CATALOG, COLUMNS, Base, commit


@dataclass
class Option(Base):
    """Iceberg through pyiceberg, one commit per trace. Each batch is written as plain Parquet files outside the table,
    at a key fixed by trace, version and batch, so a second delivery overwrites the first; a reader that wants a fresh
    batch reads that file. Closing a trace reads its files back and replaces the trace's rows in the output table, then
    in the errors table, as two commits. Whole-trace readers read the table. The files are kept. No maintenance is
    run."""

    catalog: str = CATALOG
    local_dir: str = ""
    tail: str = f"{PIPELINE_DATA}/tail"
    name = "iceberg-per-trace"

    @classmethod
    def local(cls, directory):
        return cls(local_dir=f"{directory}/iceberg", tail=f"{directory}/tail")

    def setup(self):
        super().setup()
        fs, root = pafs.FileSystem.from_uri(self.tail)
        fs.create_dir(root, recursive=True)
        fs.delete_dir_contents(root)

    def connect(self):
        return _Conn(self._catalog(), *pafs.FileSystem.from_uri(self.tail))


class _Conn:
    def __init__(self, catalog, fs, root):
        self.output, self.errors = catalog.load_table("pipeline.output"), catalog.load_table("pipeline.errors")
        self.fs, self.root, self.made, self.retries = fs, root, set(), 0

    def _dir(self, kind, trace, version):
        return f"{self.root}/{kind}/{trace}/v{version}"

    def write_batch(self, trace, version, batch, attempt, rows, errors):
        for kind, table in (("output", rows), ("errors", errors)):
            if (kind, trace, version) not in self.made:
                self.fs.create_dir(self._dir(kind, trace, version), recursive=True)
                self.made.add((kind, trace, version))
            pq.write_table(table, f"{self._dir(kind, trace, version)}/{batch}.parquet", filesystem=self.fs)

    def finalize(self, trace, version):
        for kind, table in (("output", self.output), ("errors", self.errors)):
            rows = pq.read_table(self._dir(kind, trace, version), filesystem=self.fs)
            self.retries += commit(table, lambda t: t.overwrite(rows, overwrite_filter=EqualTo("trace", trace)))

    def read_trace(self, trace):
        return self.output.refresh().scan(row_filter=EqualTo("trace", trace), selected_fields=COLUMNS).to_arrow().drop_columns("attempt")

    def read_batch(self, trace, version, batch):
        return pq.read_metadata(f"{self._dir('output', trace, version)}/{batch}.parquet", filesystem=self.fs).num_rows

    def close(self): ...
