from bench.systems import SYSTEMS

INTERFACE = ("setup", "connect", "count", "version")


def test_every_file_in_bench_systems_is_registered_under_its_name():
    expected = {"postgres", "ducklake-postgres", "iceberg-sql-postgres", "s3-tables", "lakekeeper", "polaris"}
    assert expected <= set(SYSTEMS), f"missing: {sorted(expected - set(SYSTEMS))}"


def test_s3_tables_can_give_each_writer_its_own_table(tmp_path, monkeypatch):
    from pyiceberg.catalog.sql import SqlCatalog

    from bench.systems import s3_tables

    def local_catalog(self):  # stand-in for the REST endpoint, so this runs without AWS
        return SqlCatalog("fake", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{tmp_path}/warehouse")

    monkeypatch.setattr(s3_tables.System, "_catalog", local_catalog)
    monkeypatch.setattr(s3_tables, "TABLES", 3, raising=False)
    system = s3_tables.System()
    system.setup()
    for writer in range(3):
        conn = system.connect()
        conn.commit_one(writer, 0)
        conn.close()

    catalog = system._catalog()
    rows = {table[-1]: catalog.load_table(table).scan().to_arrow().num_rows for table in catalog.list_tables("bench")}
    assert rows == {"commits_0": 1, "commits_1": 1, "commits_2": 1}
    assert system.count() == 3


def test_every_registered_system_implements_the_interface():
    for name, cls in SYSTEMS.items():
        assert cls.name == name
        assert cls.__doc__, f"{name} needs a docstring; it is shown on the site"
        for method in INTERFACE:
            assert callable(getattr(cls, method, None)), f"{name} is missing {method}()"
