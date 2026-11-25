from dataclasses import dataclass

import duckdb
import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, LessThan

from bench.options._iceberg import CATALOG, COLUMNS, CONTROL, Base, commit


@dataclass
class Option(Base):
    """Iceberg through pyiceberg, one commit per batch. Each batch appends its output rows and then its error rows, as
    two commits, to tables partitioned by trace. Closing a trace deletes the trace's older versions and appends its new
    version to a control table, as three more commits. Readers take the recorded version and one delivery per batch.
    No maintenance is run."""

    catalog: str = CATALOG
    local_dir: str = ""
    name = "iceberg-per-batch"

    @classmethod
    def local(cls, directory):
        return cls(local_dir=f"{directory}/iceberg")

    def connect(self):
        return _Conn(self._catalog())


class _Conn:
    def __init__(self, catalog):
        self.output, self.errors, self.control = (catalog.load_table(f"pipeline.{t}") for t in ("output", "errors", "control"))
        self.retries = 0

    def write_batch(self, trace, version, batch, attempt, rows, errors):
        self.retries += commit(self.output, lambda t: t.append(rows))
        self.retries += commit(self.errors, lambda t: t.append(errors))

    def finalize(self, trace, version):
        older = And(EqualTo("trace", trace), LessThan("version", version))
        self.retries += commit(self.output, lambda t: t.delete(older))
        self.retries += commit(self.errors, lambda t: t.delete(older))
        self.retries += commit(self.control, lambda t: t.append(pa.table({"trace": [trace], "version": [version]}, schema=CONTROL)))

    def read_trace(self, trace):
        recorded = self.control.refresh().scan(row_filter=EqualTo("trace", trace)).to_arrow()
        version = pa.compute.max(recorded["version"]).as_py()
        found = self.output.refresh().scan(  # noqa: F841 (read by the query below)
            row_filter=And(EqualTo("trace", trace), EqualTo("version", version)), selected_fields=COLUMNS
        ).to_arrow()
        return duckdb.sql("SELECT version, batch, row FROM found QUALIFY attempt = max(attempt) OVER (PARTITION BY batch)").to_arrow_table()

    def read_batch(self, trace, version, batch):
        this = And(EqualTo("trace", trace), EqualTo("version", version), EqualTo("batch", batch))
        return self.output.refresh().scan(row_filter=this, selected_fields=("row",)).to_arrow().num_rows

    def close(self): ...
