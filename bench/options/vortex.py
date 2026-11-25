import json
from dataclasses import dataclass

import duckdb
import pyarrow.fs as pafs

from bench.config import PIPELINE_DATA
from bench.options._tables import reader


@dataclass
class Option:
    """Vortex files and no catalog, written and read through DuckDB's vortex extension. Each delivery of a batch is
    one file at its own key, named for the trace, version, batch and attempt, and no file is ever overwritten: the
    extension keeps file metadata for the life of the process, and a file replaced at the same key is then read wrongly.
    Closing a trace writes a small control object naming its current version, and readers scan that version's files
    and keep the latest delivery of each batch. Nothing is ever deleted."""

    data: str = f"{PIPELINE_DATA}/vortex"
    name = "vortex"

    @classmethod
    def local(cls, directory):
        return cls(data=f"{directory}/vortex")

    def setup(self):
        fs, root = pafs.FileSystem.from_uri(self.data)
        fs.create_dir(root, recursive=True)
        fs.delete_dir_contents(root)

    def connect(self):
        return _Conn(self.data, *pafs.FileSystem.from_uri(self.data))

    def version(self):
        where = "s3" if self.data.startswith("s3://") else "a local directory"
        return f"duckdb {duckdb.__version__} with its vortex extension, Vortex files under {where}"


class _Conn:
    def __init__(self, data, fs, root):
        self.data, self.fs, self.root, self.made = data, fs, root, set()
        self.db = reader("vortex", data)

    def _dir(self, kind, trace, version):
        return f"{kind}/{trace}/v{version}"

    def _write(self, rows, kind, trace, version, batch, attempt):  # rows is read by name in the COPY below
        where = self._dir(kind, trace, version)
        if where not in self.made:  # a local directory must exist first; on S3 this is one request per trace and version
            self.fs.create_dir(f"{self.root}/{where}", recursive=True)
            self.made.add(where)
        self.db.execute(f"COPY rows TO '{self.data}/{where}/{batch}-{attempt}.vortex' (FORMAT vortex)")

    def write_batch(self, trace, version, batch, attempt, rows, errors):
        self._write(rows, "output", trace, version, batch, attempt)
        self._write(errors, "errors", trace, version, batch, attempt)

    def finalize(self, trace, version):
        self.fs.create_dir(f"{self.root}/control", recursive=True)
        with self.fs.open_output_stream(f"{self.root}/control/{trace}.json") as f:
            f.write(json.dumps({"version": version}).encode())

    def read_trace(self, trace):
        with self.fs.open_input_stream(f"{self.root}/control/{trace}.json") as f:
            version = json.loads(f.read())["version"]
        return self.db.sql(
            f"SELECT version, batch, row FROM read_vortex('{self.data}/{self._dir('output', trace, version)}/*.vortex') "
            "QUALIFY attempt = max(attempt) OVER (PARTITION BY batch)"
        ).to_arrow_table()

    def read_batch(self, trace, version, batch):
        files = f"{self.data}/{self._dir('output', trace, version)}/{batch}-*.vortex"
        return self.db.sql(f"SELECT count(*) FROM read_vortex('{files}')").fetchall()[0][0]

    def close(self):
        self.db.close()
