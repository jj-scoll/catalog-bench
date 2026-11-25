"""What the Delta Lake and Lance options share. A format is a module (_delta or _lance) that creates, appends to,
deletes from, replaces in and compacts a table at a path; everything is read back through DuckDB."""

import time
from dataclasses import dataclass

import duckdb
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from bench.config import PIPELINE_DATA
from bench.pipeline import SCHEMA

CONTROL = pa.schema([("trace", pa.int32()), ("version", pa.int32())])
ATTEMPTS = 10


def reader(extension, data):
    """A DuckDB connection with the extension loaded, and credentials from the environment if the data is on S3."""
    db = duckdb.connect()
    db.execute(f"INSTALL {extension}; LOAD {extension}")
    if data.startswith("s3://"):
        db.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
    return db


def commit(fmt, change):
    """Run change(), again if another writer's commit got in first. Returns the number of extra tries, on top of
    whatever retrying the format's own library does inside one call."""
    for attempt in range(ATTEMPTS):
        try:
            change()
            return attempt
        except fmt.CONFLICT:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(min(2.0, 0.05 * 2**attempt))


@dataclass
class Base:
    data: str = ""  # a directory or s3:// prefix; the default is under BENCH_PIPELINE_DATA

    @classmethod
    def local(cls, directory):
        return cls(data=f"{directory}/{cls.name}")

    def root(self):
        return self.data or f"{PIPELINE_DATA}/{self.name}"

    def path(self, table):
        return f"{self.root()}/{table}{self.format.SUFFIX}"

    def setup(self):
        fs, root = pafs.FileSystem.from_uri(self.root())
        fs.create_dir(root, recursive=True)
        fs.delete_dir_contents(root)
        for table in self.tables:
            self.format.create(self.path(table), CONTROL if table == "control" else SCHEMA)

    def connect(self):
        return self.conn(self)

    def maintain(self):
        for table in ("output", "errors"):
            self.format.compact(self.path(table))

    def version(self):
        where = "s3" if self.root().startswith("s3://") else "a local directory"
        return f"{self.format.version()}, read with duckdb {duckdb.__version__}, pyarrow {pa.__version__}; data under {where}"


class _Conn:
    def __init__(self, option):
        self.fmt, self.path, self.retries = option.format, option.path, 0
        self.db = reader(option.format.EXTENSION, option.root())

    def do(self, change):
        self.retries += commit(self.fmt, change)

    def scan(self, table):
        return self.fmt.scan(self.path(table))

    def close(self):
        self.db.close()


class _PerBatch(_Conn):
    def write_batch(self, trace, version, batch, attempt, rows, errors):
        self.do(lambda: self.fmt.append(self.path("output"), rows))
        self.do(lambda: self.fmt.append(self.path("errors"), errors))

    def finalize(self, trace, version):
        older = f"trace = {trace} AND version < {version}"
        self.do(lambda: self.fmt.delete(self.path("output"), older))
        self.do(lambda: self.fmt.delete(self.path("errors"), older))
        self.do(lambda: self.fmt.append(self.path("control"), pa.table({"trace": [trace], "version": [version]}, schema=CONTROL)))

    def read_trace(self, trace):
        version = self.db.sql(f"SELECT max(version) FROM {self.scan('control')} WHERE trace = {trace}").fetchall()[0][0]
        return self.db.sql(
            f"SELECT version, batch, row FROM {self.scan('output')} WHERE trace = {trace} AND version = {version} "
            "QUALIFY attempt = max(attempt) OVER (PARTITION BY batch)"
        ).to_arrow_table()

    def read_batch(self, trace, version, batch):
        return self.db.sql(
            f"SELECT count(*) FROM {self.scan('output')} WHERE trace = {trace} AND version = {version} AND batch = {batch}"
        ).fetchall()[0][0]


class _PerTrace(_Conn):
    def __init__(self, option):
        super().__init__(option)
        self.fs, self.tail = pafs.FileSystem.from_uri(f"{option.root()}/tail")
        self.made = set()

    def _dir(self, kind, trace, version):
        return f"{self.tail}/{kind}/{trace}/v{version}"

    def write_batch(self, trace, version, batch, attempt, rows, errors):
        for kind, table in (("output", rows), ("errors", errors)):
            if (kind, trace, version) not in self.made:
                self.fs.create_dir(self._dir(kind, trace, version), recursive=True)
                self.made.add((kind, trace, version))
            pq.write_table(table, f"{self._dir(kind, trace, version)}/{batch}.parquet", filesystem=self.fs)

    def finalize(self, trace, version):
        for kind in ("output", "errors"):
            rows = pq.read_table(self._dir(kind, trace, version), filesystem=self.fs)
            self.do(lambda: self.fmt.replace(self.path(kind), trace, version, rows))

    def read_trace(self, trace):  # the highest version present is complete: a version arrives in one commit
        return self.db.sql(
            f"SELECT version, batch, row FROM {self.scan('output')} WHERE trace = {trace} QUALIFY version = max(version) OVER ()"
        ).to_arrow_table()

    def read_batch(self, trace, version, batch):
        return pq.read_metadata(f"{self._dir('output', trace, version)}/{batch}.parquet", filesystem=self.fs).num_rows


class PerBatch(Base):
    tables, conn = ("output", "errors", "control"), _PerBatch


class PerTrace(Base):
    tables, conn = ("output", "errors"), _PerTrace
