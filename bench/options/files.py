import json
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
from bench.config import PIPELINE_DATA

COLUMNS = ["version", "batch", "row"]


@dataclass
class Option:
    """Plain Parquet files and no catalog. Each batch is one file at a fixed key, so a second delivery overwrites the
    first. Closing a trace writes a small control object naming its current version, and readers list the trace's files
    and keep the rows of that version. Nothing is ever deleted."""

    data: str = f"{PIPELINE_DATA}/files"
    name = "files"

    def setup(self):
        fs, root = pafs.FileSystem.from_uri(self.data)
        fs.create_dir(root, recursive=True)
        fs.delete_dir_contents(root)

    def connect(self):
        return _Conn(*pafs.FileSystem.from_uri(self.data))

    def version(self):
        return f"pyarrow {pa.__version__}, Parquet files under {self.data.split('://')[0] if '://' in self.data else 'a local directory'}"


class _Conn:
    def __init__(self, fs, root):
        self.fs, self.root, self.made = fs, root, set()

    def key(self, kind, trace, batch):
        return f"{self.root}/{kind}/{trace}/{trace}-{batch}.parquet"

    def _write(self, table, kind, trace, batch):
        if (kind, trace) not in self.made:  # a local directory must exist first; on S3 this is one request per trace
            self.fs.create_dir(f"{self.root}/{kind}/{trace}", recursive=True)
            self.made.add((kind, trace))
        pq.write_table(table, self.key(kind, trace, batch), filesystem=self.fs)

    def write_batch(self, trace, version, batch, attempt, rows, errors):
        self._write(rows, "output", trace, batch)
        self._write(errors, "errors", trace, batch)

    def finalize(self, trace, version):
        self.fs.create_dir(f"{self.root}/control", recursive=True)
        with self.fs.open_output_stream(f"{self.root}/control/{trace}.json") as f:
            f.write(json.dumps({"version": version}).encode())

    def current_version(self, trace):
        with self.fs.open_input_stream(f"{self.root}/control/{trace}.json") as f:
            return json.loads(f.read())["version"]

    def read_trace(self, trace):
        version = self.current_version(trace)
        return pq.read_table(f"{self.root}/output/{trace}", filesystem=self.fs, columns=COLUMNS, filters=[("version", "=", version)])

    def read_batch(self, trace, version, batch):
        table = pq.read_table(self.key("output", trace, batch), filesystem=self.fs, columns=["version"])
        return table.filter(pa.compute.equal(table["version"], version)).num_rows

    def close(self): ...
