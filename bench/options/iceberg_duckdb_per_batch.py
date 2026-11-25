from dataclasses import dataclass

from bench.options._iceberg_duckdb import CATALOG, Base, Conn


@dataclass
class Option(Base):
    """Iceberg through DuckDB's iceberg extension, one commit per batch. The same steps as iceberg-per-batch with a
    different client: each batch appends its output rows and then its error rows, as two commits, to tables partitioned
    by trace. Closing a trace deletes the trace's older versions and appends its new version to a control table, as
    three more commits. Readers take the recorded version and one delivery per batch. No maintenance is run."""

    catalog: str = CATALOG
    name = "iceberg-duckdb-per-batch"

    @classmethod
    def local(cls, directory):  # not service-free: the tests skip it unless Lakekeeper from compose.yaml is running
        return cls(catalog="lakekeeper")

    def connect(self):
        return _Conn(self.catalog)


class _Conn(Conn):
    def write_batch(self, trace, version, batch, attempt, rows, errors):
        self.do("INSERT INTO ice.pipeline.output SELECT * FROM rows", rows)
        self.do("INSERT INTO ice.pipeline.errors SELECT * FROM rows", errors)

    def finalize(self, trace, version):
        self.do(f"DELETE FROM ice.pipeline.output WHERE trace = {trace} AND version < {version}")
        self.do(f"DELETE FROM ice.pipeline.errors WHERE trace = {trace} AND version < {version}")
        self.do(f"INSERT INTO ice.pipeline.control VALUES ({trace}, {version})")

    def read_trace(self, trace):
        version = self.db.sql(f"SELECT max(version) FROM ice.pipeline.control WHERE trace = {trace}").fetchall()[0][0]
        return self.db.sql(
            f"SELECT version, batch, row FROM ice.pipeline.output WHERE trace = {trace} AND version = {version} "
            "QUALIFY attempt = max(attempt) OVER (PARTITION BY batch)"
        ).to_arrow_table()

    def read_batch(self, trace, version, batch):
        return self.db.sql(
            f"SELECT count(*) FROM ice.pipeline.output WHERE trace = {trace} AND version = {version} AND batch = {batch}"
        ).fetchall()[0][0]
