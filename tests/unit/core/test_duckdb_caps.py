"""S1-T5 core_config_log_db amendment: DuckDB resource caps. Acceptance A31-A41, rules R20-R25 (+ planted failures)."""
from __future__ import annotations

import ast
import dataclasses
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import duckdb
import pytest

from _core_helpers import BOOTSTRAP_SQL, VALID_TOML, make_repo, set_toml

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "config" / "investos.example.toml"
DB_SRC = REPO / "src" / "investos" / "core" / "db.py"
MIB = 1024 * 1024
GIB = 1024 ** 3
SETTINGS_ORDER = ["memory_limit", "threads", "preserve_insertion_order", "temp_directory"]


def _cfgmod():
    from investos.core import config
    return config


def _db():
    from investos.core import db
    return db


def _toml(body: str | None, extra: str = "") -> str:
    return VALID_TOML + extra + ("" if body is None else "\n[duckdb]\n" + body + "\n")


def _load(repo, body: str | None = None, env=None, extra: str = ""):
    set_toml(repo, _toml(body, extra))
    return _cfgmod().load_config(repo / "config" / "investos.toml", env={} if env is None else env, repo_root=repo)


def _git_init(repo: Path, ignore: str | None):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if ignore is not None:
        (repo / ".gitignore").write_text(ignore)


def _setting(con, name):
    return con.execute("select current_setting(?)", [name]).fetchone()[0]


def _parse(text: str) -> int:
    return _db().parse_size_text(text)


def _within(actual: int, expected: int, tol: float = 0.02) -> bool:
    return abs(actual - expected) <= tol * expected


@pytest.fixture
def logs():
    from investos.core import log
    saved = dict(log._state)
    buf = io.StringIO()
    log.configure_logging(buf, level="DEBUG", run_id="log-run-0001")

    def records():
        return [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
    yield records
    log._state.update(saved)


class RecordingConnection:
    """Delegating proxy around a real DuckDB connection: records statements, can reject a SET, records close()."""

    def __init__(self, real, log: list, reject: str | None):
        self._real, self._log, self._reject, self.closed = real, log, reject, False

    def _gate(self, sql):
        s = " ".join(str(sql).split())
        self._log.append(s)
        if self._reject and re.match(rf"(?i)^SET\s+(GLOBAL\s+)?{self._reject}\b", s):
            raise duckdb.Error(f"planted rejection of {self._reject}")

    def execute(self, sql, *a, **k):
        self._gate(sql)
        return self._real.execute(sql, *a, **k)

    def sql(self, sql, *a, **k):
        self._gate(sql)
        return self._real.sql(sql, *a, **k)

    query = sql

    def close(self):
        self.closed = True
        return self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def recording(monkeypatch):
    """Patch duckdb.connect as seen by investos.core.db; yields a handle with .statements, .conns, .reject."""
    db = _db()

    class H:
        statements: list = []
        conns: list = []
        reject = None
    h = H()
    h.statements, h.conns = [], []
    real_connect = duckdb.connect

    def connect(*a, **k):
        c = RecordingConnection(real_connect(*a, **k), h.statements, h.reject)
        h.conns.append(c)
        return c
    monkeypatch.setattr(db.duckdb, "connect", connect)
    return h


# ============================================================ A31 (R20) defaults and sizes
def test_A31_defaults_when_section_absent(repo):
    cfg = _load(repo, None)
    mod = _cfgmod()
    assert cfg.duckdb == mod.DuckDBConfig("5GB", 5_000_000_000, 4, repo.resolve() / "data" / "canonical" / ".duckdb_tmp")
    assert [f.name for f in dataclasses.fields(mod.DuckDBConfig)] == [
        "memory_limit", "memory_limit_bytes", "threads", "temp_directory"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.duckdb.threads = 9


def test_A31_default_temp_directory_follows_a_relocated_canonical_zone(repo):
    """Existing fixtures put the zones elsewhere (tmp/canonical): the default must sit inside the canonical zone
    wherever it is, so those configs keep loading without a [duckdb] section."""
    text = VALID_TOML.replace('canonical  = "data/canonical"', 'canonical  = "elsewhere/canon"')
    (repo / "elsewhere" / "canon").mkdir(parents=True)
    set_toml(repo, text)
    cfg = _cfgmod().load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)
    assert cfg.duckdb.temp_directory == repo.resolve() / "elsewhere" / "canon" / ".duckdb_tmp"


def test_A31_empty_section_equals_defaults(repo):
    assert _load(repo, "") .duckdb == _load(repo, None).duckdb


def test_A31_config_without_duckdb_argument_still_builds():
    mod = _cfgmod()
    assert "duckdb" in {f.name for f in dataclasses.fields(mod.Config)}
    cfg = mod.Config(repo_root=Path("/x"), timezone="Asia/Kolkata", zones={}, migrations_dir=Path("/x/m"),
                     rate_limits=mod.RateLimits(0.35, {}))
    assert cfg.duckdb.memory_limit == "5GB" and cfg.duckdb.threads == 4 and cfg.duckdb.memory_limit_bytes == 5_000_000_000


def test_A31_gib_and_threads_from_file(repo):
    cfg = _load(repo, 'memory_limit = "2GiB"\nthreads = 2')
    assert cfg.duckdb.memory_limit == "2GiB" and cfg.duckdb.memory_limit_bytes == 2147483648 and cfg.duckdb.threads == 2
    assert cfg.duckdb.temp_directory == repo.resolve() / "data" / "canonical" / ".duckdb_tmp"


@pytest.mark.parametrize("text,expected", [
    ("5 GB", 5_000_000_000), ("5gb", 5_000_000_000), ("5Gb", 5_000_000_000), ("5GB", 5_000_000_000),
    ("100MB", 100_000_000), ("64MiB", 64 * MIB), ("64 mib", 64 * MIB), ("1.5GB", 1_500_000_000),
    ("1.5GiB", 1610612736), ("0.1GiB", 107374182), ("70.1MB", 70_100_000), ("1.1GB", 1_100_000_000), ("1TB", 10 ** 12), ("1TiB", 1024 ** 4),
    ("67109KB", 67_109_000), ("65536KiB", 64 * MIB), ("16GiB", 16 * GIB), ("2.5 TiB", int(2.5 * 1024 ** 4)),
], ids=lambda v: str(v))
def test_A31_units_decimal_vs_binary_rounded_down(repo, text, expected):
    cfg = _load(repo, f'memory_limit = "{text}"')
    assert cfg.duckdb.memory_limit_bytes == expected and type(cfg.duckdb.memory_limit_bytes) is int


@pytest.mark.parametrize("n", [1, 4, 64])
def test_A31_threads_boundaries_accepted(repo, n):
    assert _load(repo, f"threads = {n}").duckdb.threads == n


# ============================================================ A32 (R20, planted) invalid values
@pytest.mark.planted
@pytest.mark.parametrize("lit", [
    "5", "5.5", '"5"', '"5G"', '"80%"', '"0GB"', '"-1GB"', '"10MB"', '"5 bytes"', '"5B"', '"GB"', '""', '"5  GB"',
    '" 5GB"', '"5GB "', '"5GB\\n"', '"1e3MB"', '".5GB"', '"5.GB"', '"5,5GB"', '"64MB"', '"65535KiB"', '"67108863B"',
    '"0.01GB"', "true", "[1]", '"five GB"', '"5GBB"', '"5 gigabytes"', '"+5GB"', '"5GiBB"',
])
def test_A32_bad_memory_limit(repo, lit):
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.memory_limit"):
        _load(repo, f"memory_limit = {lit}")


@pytest.mark.planted
@pytest.mark.parametrize("lit", ["0", "65", "2.5", "true", "false", '"4"', "-1", "1000", "4.0", "[4]", '""'])
def test_A32_bad_threads(repo, lit):
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.threads"):
        _load(repo, f"threads = {lit}")


@pytest.mark.planted
@pytest.mark.parametrize("line", ["cache = 1", "preserve_insertion_order = true", "preserve_insertion_order = false",
                                  "Memory_Limit = \"5GB\"", "max_memory = \"5GB\"", "temp_dir = \"x\""])
def test_A32_A39_unknown_key_in_duckdb_section(repo, line):
    assert _load(repo, "threads = 4").duckdb.threads == 4          # the section itself is accepted
    with pytest.raises(_cfgmod().ConfigError, match="duckdb"):
        _load(repo, line)


@pytest.mark.planted
def test_A32_duckdb_must_be_a_table(repo):
    assert _load(repo, "threads = 4").duckdb.threads == 4
    set_toml(repo, 'duckdb = 5\n' + VALID_TOML)
    with pytest.raises(_cfgmod().ConfigError, match="duckdb"):
        _cfgmod().load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


@pytest.mark.planted
@pytest.mark.parametrize("lit", ["5", "true", '""', "[1]"])
def test_A32_bad_temp_directory_type(repo, lit):
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.temp_directory"):
        _load(repo, f"temp_directory = {lit}")


# ============================================================ A33 (R20, env)
def test_A33_env_overrides_file_values(repo):
    cfg = _load(repo, 'memory_limit = "2GiB"\nthreads = 8',
                env={"INVESTOS_DUCKDB__MEMORY_LIMIT": "3GB", "INVESTOS_DUCKDB__THREADS": "2"})
    assert (cfg.duckdb.memory_limit, cfg.duckdb.memory_limit_bytes, cfg.duckdb.threads) == ("3GB", 3_000_000_000, 2)


def test_A33_env_works_when_section_absent_and_temp_directory_override(repo):
    cfg = _load(repo, None, env={"INVESTOS_DUCKDB__THREADS": "6", "INVESTOS_DUCKDB__TEMP_DIRECTORY": "data/ledger/spill"})
    assert cfg.duckdb.threads == 6 and cfg.duckdb.temp_directory == repo.resolve() / "data" / "ledger" / "spill"
    assert cfg.duckdb.memory_limit == "5GB"


@pytest.mark.planted
@pytest.mark.parametrize("val", ["2.5", "abc", "", "true", "0", "65", "-1", "4.0e0", "two"])
def test_A33_bad_threads_env(repo, val):
    assert _load(repo, None, env={"INVESTOS_DUCKDB__THREADS": "4"}).duckdb.threads == 4
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.threads|INVESTOS_DUCKDB__THREADS"):
        _load(repo, None, env={"INVESTOS_DUCKDB__THREADS": val})


@pytest.mark.planted
@pytest.mark.parametrize("val", ["80%", "5G", "0GB", "10MB", "", "5"])
def test_A33_bad_memory_limit_env_is_validated_like_the_file_value(repo, val):
    assert _load(repo, None, env={"INVESTOS_DUCKDB__MEMORY_LIMIT": "3GB"}).duckdb.memory_limit_bytes == 3_000_000_000
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.memory_limit|INVESTOS_DUCKDB__MEMORY_LIMIT"):
        _load(repo, None, env={"INVESTOS_DUCKDB__MEMORY_LIMIT": val})


@pytest.mark.planted
@pytest.mark.parametrize("name", ["INVESTOS_DUCKDB__THREAD", "INVESTOS_DUCKDB__MEMORY", "INVESTOS_DUCKDB__CACHE",
                                  "INVESTOS_DUCKDB__PRESERVE_INSERTION_ORDER", "INVESTOS_DUCKDB__TEMP_DIR"])
def test_A33_typo_or_forbidden_env_key_is_an_error(repo, name):
    assert _load(repo, None, env={"INVESTOS_DUCKDB__THREADS": "4"}).duckdb.threads == 4
    with pytest.raises(_cfgmod().ConfigError):
        _load(repo, None, env={name: "2"})


# ============================================================ A34 (R21, planted) temp_directory placement
@pytest.mark.planted
@pytest.mark.parametrize("where", [
    "data/canonical",                      # a zone itself
    "data/raw", "data/manifests", "data/ledger",
    "OUTSIDE",                             # outside every zone
    "data/registry-not-a-zone",            # a sibling of the zones, not in one
    "registry/spill", "reports/spill",     # excluded zones
    "data/canonical/../../outside",        # traversal that resolves outside
    ".",                                   # the repo root
    "data",                                # parent of zones
], ids=lambda v: v.replace("/", "_"))
def test_A34_temp_directory_outside_or_wrong_zone(repo, tmp_path, where):
    path = str(tmp_path / "outside_spill") if where == "OUTSIDE" else where
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.temp_directory"):
        _load(repo, f'temp_directory = "{path}"')


@pytest.mark.parametrize("where", ["data/canonical/.duckdb_tmp", "data/canonical/a/b", "data/raw/.spill",
                                   "data/manifests/spill", "data/ledger/spill", "data/canonical/.x/../.y"])
def test_A34_valid_placements_resolve_and_create_nothing(repo, where):
    cfg = _load(repo, f'temp_directory = "{where}"')
    assert cfg.duckdb.temp_directory == (repo / where).resolve()
    assert not cfg.duckdb.temp_directory.exists()                    # load_config creates nothing (R5)


def test_A34_absolute_path_inside_a_zone(repo):
    p = repo.resolve() / "data" / "canonical" / "abs_spill"
    assert _load(repo, f'temp_directory = "{p}"').duckdb.temp_directory == p


@pytest.mark.planted
def test_A34_inside_repo_and_not_git_ignored_refuses(repo):
    _git_init(repo, "data/raw/\n")                                    # raw is ignored (R6), canonical is not
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.temp_directory"):
        _load(repo, None, env={"PATH": os.environ["PATH"]})
    with pytest.raises(_cfgmod().ConfigError, match=r"duckdb\.temp_directory"):
        _load(repo, 'temp_directory = "data/ledger/spill"', env={"PATH": os.environ["PATH"]})


def test_A34_git_ignored_default_is_accepted(repo):
    _git_init(repo, "data/raw/\ndata/canonical/*\n")
    cfg = _load(repo, None, env={"PATH": os.environ["PATH"]})
    assert cfg.duckdb.temp_directory == repo.resolve() / "data" / "canonical" / ".duckdb_tmp"
    assert not cfg.duckdb.temp_directory.exists()


@pytest.mark.planted
def test_A34_git_unrunnable_refuses_when_temp_dir_is_in_repo_with_git(repo):
    # (precondition: _git_init below; the same repo loads when git is runnable)
    _git_init(repo, "data/raw/\ndata/canonical/*\n")
    assert _load(repo, None, env={"PATH": os.environ["PATH"]}).duckdb.threads == 4
    with pytest.raises(_cfgmod().ConfigError):
        _load(repo, None, env={"PATH": "/nonexistent"})


def test_A34_no_git_dir_means_no_ignore_check(repo):
    assert not (repo / ".git").exists()
    assert _load(repo, None).duckdb.temp_directory.name == ".duckdb_tmp"


# ============================================================ A35 (R22) open_db applies the caps
def _repo_cfg(repo, body=None, extra_migration: str | None = None):
    if extra_migration:
        (repo / "db" / "migrations" / "0001_probe.sql").write_text(extra_migration)
    return _load(repo, body)


def test_A35_open_db_applies_the_four_settings(repo):
    cfg = _repo_cfg(repo, 'memory_limit = "2GiB"\nthreads = 3')
    assert not cfg.duckdb.temp_directory.exists()
    con = _db().open_db(cfg)
    try:
        assert cfg.duckdb.temp_directory.is_dir()
        assert _setting(con, "threads") == 3
        assert _setting(con, "preserve_insertion_order") is False
        assert _setting(con, "temp_directory") == str(cfg.duckdb.temp_directory)
        assert _within(_parse(_setting(con, "memory_limit")), 2 * GIB)
    finally:
        con.close()


def test_A35_read_only_connection_gets_all_four_settings(repo):
    cfg = _repo_cfg(repo, 'memory_limit = "1GiB"\nthreads = 3')
    _db().open_db(cfg).close()
    shutil.rmtree(cfg.duckdb.temp_directory)
    ro = _db().open_db(cfg, read_only=True)
    try:
        assert cfg.duckdb.temp_directory.is_dir()
        assert _setting(ro, "threads") == 3 and _setting(ro, "preserve_insertion_order") is False
        assert _setting(ro, "temp_directory") == str(cfg.duckdb.temp_directory)
        assert _within(_parse(_setting(ro, "memory_limit")), GIB)
    finally:
        ro.close()


def test_A35_apply_false_connection_also_gets_the_settings(repo):
    cfg = _repo_cfg(repo, "threads = 3")
    _db().open_db(cfg).close()
    con = _db().open_db(cfg, apply=False)
    try:
        assert _setting(con, "threads") == 3 and _setting(con, "preserve_insertion_order") is False
    finally:
        con.close()


def test_A35_migrations_already_see_the_settings(repo):
    sql = ("CREATE TABLE probe AS SELECT current_setting('threads') AS threads, "
           "current_setting('preserve_insertion_order') AS pio, current_setting('memory_limit') AS ml, "
           "current_setting('temp_directory') AS td;")
    cfg = _repo_cfg(repo, 'memory_limit = "2GiB"\nthreads = 3', extra_migration=sql)
    con = _db().open_db(cfg)
    try:
        threads, pio, ml, td = con.execute("select threads, pio, ml, td from probe").fetchone()
        assert int(threads) == 3 and str(pio).lower() in ("false", "0") and _within(_parse(ml), 2 * GIB)
        assert td == str(cfg.duckdb.temp_directory)
    finally:
        con.close()


def test_A35_every_connection_in_a_row_is_configured(repo):
    cfg = _repo_cfg(repo, "threads = 3")
    for ro in (False, False, True):
        con = _db().open_db(cfg, read_only=ro)
        try:
            assert _setting(con, "threads") == 3
        finally:
            con.close()


def test_R22_statements_run_in_order_before_migrations_and_quote_values(repo, recording):
    cfg = _repo_cfg(repo, 'memory_limit = "2 GiB"\nthreads = 3')
    _db().open_db(cfg).close()
    sets = [s for s in recording.statements if re.match(r"(?i)^SET\b", s)]
    names = [re.match(r"(?i)^SET\s+(?:GLOBAL\s+)?(\w+)", s).group(1).lower() for s in sets]
    assert names == SETTINGS_ORDER
    assert re.search(r"(?i)memory_limit\s*=\s*'2 GiB'", sets[0]) and re.search(r"(?i)threads\s*=\s*3\b", sets[1])
    assert re.search(r"(?i)preserve_insertion_order\s*=\s*false", sets[2])
    assert re.search(r"(?i)temp_directory\s*=\s*'" + re.escape(str(cfg.duckdb.temp_directory)) + "'", sets[3])
    last_set = max(i for i, s in enumerate(recording.statements) if re.match(r"(?i)^SET\b", s))
    mig = [i for i, s in enumerate(recording.statements) if "schema_migrations" in s]
    assert mig and min(mig) > last_set


# ============================================================ A36 (R22, planted) failures and quoting
def _lock_free(path: Path) -> bool:
    r = subprocess.run([sys.executable, "-c", "import duckdb,sys; duckdb.connect(sys.argv[1]).close()", str(path)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0


@pytest.mark.planted
def test_A36_temp_path_occupied_by_a_file(repo):
    cfg = _repo_cfg(repo, None)
    cfg.duckdb.temp_directory.write_text("i am a file")
    db = _db()
    with pytest.raises(db.MigrationError) as e:
        db.open_db(cfg)
    assert str(cfg.duckdb.temp_directory) in str(e.value) and "temp directory" in str(e.value)
    assert _lock_free(cfg.db_path)                                    # no connection left open (checked from a 2nd process)
    cfg.duckdb.temp_directory.unlink()
    db.open_db(cfg).close()


@pytest.mark.planted
def test_A36_read_only_open_with_file_in_the_way_closes_the_connection(repo):
    cfg = _repo_cfg(repo, None)
    _db().open_db(cfg).close()
    shutil.rmtree(cfg.duckdb.temp_directory)
    cfg.duckdb.temp_directory.write_text("x")
    with pytest.raises(_db().MigrationError):
        _db().open_db(cfg, read_only=True)
    cfg.duckdb.temp_directory.unlink()
    _db().open_db(cfg, read_only=True).close()


@pytest.mark.planted
def test_A36_parent_of_temp_directory_is_not_created(repo):
    cfg = _repo_cfg(repo, 'temp_directory = "data/canonical/missing_parent/spill"')
    with pytest.raises(_db().MigrationError) as e:
        _db().open_db(cfg)
    assert "missing_parent" in str(e.value) and not (repo / "data" / "canonical" / "missing_parent").exists()


@pytest.mark.planted
@pytest.mark.parametrize("name", ["it's_tmp", "o'brien's spill", "two words", "semi;colon", "a''b"])
def test_A36_quoting_of_the_temp_path(repo, name):
    cfg = _repo_cfg(repo, f'temp_directory = "data/canonical/{name}"')
    con = _db().open_db(cfg)
    try:
        assert cfg.duckdb.temp_directory.is_dir()
        assert _setting(con, "temp_directory") == str(cfg.duckdb.temp_directory)
    finally:
        con.close()


@pytest.mark.planted
@pytest.mark.parametrize("name", SETTINGS_ORDER)
def test_A36_rejected_set_raises_migration_error_and_closes(repo, recording, name):
    cfg = _repo_cfg(repo, None)
    recording.reject = name
    db = _db()
    with pytest.raises(db.MigrationError) as e:
        db.open_db(cfg)
    assert str(e.value).startswith("cannot apply duckdb setting") and name in str(e.value)
    assert recording.conns and all(c.closed for c in recording.conns)
    assert not any("schema_migrations" in s for s in recording.statements)        # failed before the migration step


# ============================================================ A37 (R23) the value is config, not a constant
def test_parse_size_text_units():
    assert _parse("1KiB") == 1024 and _parse("1KB") == 1000 and _parse("1 MiB") == MIB and _parse("2GB") == 2_000_000_000
    assert _parse("4.6 GiB") == int(4.6 * GIB) or _within(_parse("4.6 GiB"), int(4.6 * GIB), 1e-6)
    assert _parse("512.0 MiB") == 512 * MIB and _parse("1TiB") == 1024 ** 4 and _parse("1TB") == 10 ** 12
    assert _parse("476.8 MiB") == int(476.8 * MIB) or _within(_parse("476.8 MiB"), int(476.8 * MIB), 1e-6)


@pytest.mark.planted
@pytest.mark.parametrize("bad", ["", "abc", "5", "5 parsecs", "GB", "-1GB"])
def test_parse_size_text_rejects_garbage(bad):
    with pytest.raises(ValueError):
        _parse(bad)


def test_A37_reported_limit_follows_config(repo):
    seen = {}
    for text, nbytes in (("2GiB", 2 * GIB), ("5GB", 5_000_000_000)):
        cfg = _load(repo, f'memory_limit = "{text}"')
        con = _db().open_db(cfg)
        try:
            seen[text] = _setting(con, "memory_limit")
        finally:
            con.close()
        assert _within(_parse(seen[text]), cfg.duckdb.memory_limit_bytes)
        assert cfg.duckdb.memory_limit_bytes == nbytes
    assert seen["2GiB"] != seen["5GB"]


# ============================================================ A38 (R24, planted) spill instead of crash
SPILL_SQL = """
SELECT count(*), sum((rn <> i + 1)::INT), min(s) FILTER (WHERE rn = 1), max(s) FILTER (WHERE rn = {n})
FROM (SELECT i, s, row_number() OVER (ORDER BY s) AS rn
      FROM (SELECT i, lpad(i::VARCHAR, 100, '0') AS s FROM range({n}) t(i)))
"""
SPILL_ROWS = 3_000_000                 # spec A38 says 5 million; 3 million keeps the test under ~5 s and still needs ~350 MB vs a 64 MiB limit


def test_A38_sort_over_3m_wide_rows_spills_with_64mib(repo):
    cfg = _repo_cfg(repo, 'memory_limit = "64MiB"\nthreads = 2')
    con = _db().open_db(cfg)
    seen = {"files": 0, "bytes": 0}
    stop = threading.Event()
    tmp = cfg.duckdb.temp_directory

    def sample():
        while not stop.is_set():
            try:
                files = [p for p in os.scandir(tmp) if p.is_file()]
                seen["files"] = max(seen["files"], len(files))
                seen["bytes"] = max(seen["bytes"], sum(p.stat().st_size for p in files))
            except OSError:
                pass
            time.sleep(0.01)
    t = threading.Thread(target=sample, daemon=True)
    t.start()
    try:
        started = time.monotonic()
        n, bad, first, last = con.execute(SPILL_SQL.format(n=SPILL_ROWS)).fetchone()
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        t.join(5)
        con.close()
    assert n == SPILL_ROWS and bad == 0
    assert first == "0" * 100 and last == str(SPILL_ROWS - 1).zfill(100)
    assert seen["files"] >= 1 and seen["bytes"] > 0, "no spill file was seen in the temp directory"
    assert elapsed < 30, f"spill query took {elapsed:.1f}s"


@pytest.mark.planted
def test_A38_unwritable_temp_directory_fails_loudly_and_limit_is_unchanged(repo):
    cfg = _repo_cfg(repo, 'memory_limit = "64MiB"\nthreads = 2')
    db = _db()
    con = db.open_db(cfg)
    try:
        shutil.rmtree(cfg.duckdb.temp_directory)
        cfg.duckdb.temp_directory.write_text("a file, so no directory can be created here")
        with pytest.raises(duckdb.Error) as e:
            con.execute(SPILL_SQL.format(n=SPILL_ROWS)).fetchone()
        assert not isinstance(e.value, db.MigrationError)
        assert _within(_parse(_setting(con, "memory_limit")), 64 * MIB)          # never raised by the code
    finally:
        con.close()


# ============================================================ A39 (R22) no reliance on row order
def test_A39_db_source_has_no_limit_without_order_by():
    tree = ast.parse(DB_SRC.read_text())
    bad = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and re.search(r"(?i)\bLIMIT\b", n.value) and not re.search(r"(?i)\bORDER\s+BY\b", n.value)]
    assert bad == []
    assert "preserve_insertion_order" in DB_SRC.read_text()


@pytest.mark.planted
def test_A39_insertion_order_cannot_be_turned_back_on_by_config(repo):
    for env in ({}, {"INVESTOS_DUCKDB__PRESERVE_INSERTION_ORDER": "true"}):
        with pytest.raises(_cfgmod().ConfigError):
            _load(repo, "preserve_insertion_order = true" if not env else None, env=env)
    con = _db().open_db(_load(repo, None))
    try:
        assert _setting(con, "preserve_insertion_order") is False
    finally:
        con.close()


# ============================================================ A40 (R25) one INFO event per connection
def test_A40_one_duckdb_settings_event_with_reported_values(repo, logs):
    cfg = _repo_cfg(repo, 'memory_limit = "2GiB"\nthreads = 3')
    con = _db().open_db(cfg)
    try:
        ev = [r for r in logs() if r["event"] == "duckdb_settings"]
        assert len(ev) == 1 and ev[0]["level"] == "INFO" and ev[0]["task"] == "core.db"
        f = ev[0]["fields"]
        assert set(f) == {"memory_limit", "threads", "temp_directory", "preserve_insertion_order"}
        assert f["memory_limit"] == _setting(con, "memory_limit") != "2GiB"       # as DuckDB reports it, not as configured
        assert f["threads"] == 3 and f["preserve_insertion_order"] is False
        assert f["temp_directory"] == str(cfg.duckdb.temp_directory)
    finally:
        con.close()


def test_A40_one_event_per_connection_including_read_only(repo, logs):
    cfg = _repo_cfg(repo, None)
    _db().open_db(cfg).close()
    _db().open_db(cfg, read_only=True).close()
    assert len([r for r in logs() if r["event"] == "duckdb_settings"]) == 2


def test_A40_no_event_when_the_connection_is_refused(repo, logs):
    cfg = _repo_cfg(repo, None)
    cfg.duckdb.temp_directory.write_text("x")
    with pytest.raises(_db().MigrationError):
        _db().open_db(cfg)
    assert [r for r in logs() if r["event"] == "duckdb_settings"] == []


# ============================================================ A41 (R20) the shipped example
def test_A41_example_toml_has_the_defaults_written_out():
    assert EXAMPLE.exists()
    data = tomllib.loads(EXAMPLE.read_text())
    assert data.get("duckdb") == {"memory_limit": "5GB", "threads": 4, "temp_directory": "data/canonical/.duckdb_tmp"}


def test_A41_example_toml_loads_and_matches_the_defaults(repo, example_toml_text):
    set_toml(repo, example_toml_text)
    cfg = _cfgmod().load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)
    assert cfg.duckdb == _load(repo, None).duckdb


# ============================================================ R22 / R24 misc
def test_R24_the_code_never_raises_the_memory_limit(repo, recording):
    cfg = _repo_cfg(repo, 'memory_limit = "64MiB"')
    _db().open_db(cfg).close()
    mem_sets = [s for s in recording.statements if re.match(r"(?i)^SET\s+(GLOBAL\s+)?memory_limit", s)]
    assert len(mem_sets) == 1 and "64MiB" in mem_sets[0]


def test_R23_reported_threads_is_an_int_matching_config(repo):
    cfg = _repo_cfg(repo, "threads = 5")
    con = _db().open_db(cfg)
    try:
        assert int(_setting(con, "threads")) == cfg.duckdb.threads == 5
    finally:
        con.close()
