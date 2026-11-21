"""S0-T2 acceptance tests A21..A30 for investos.core.db (rules R13-R19)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from _core_helpers import BOOTSTRAP_SQL, make_repo, set_toml, VALID_TOML

REAL_BOOTSTRAP = Path(__file__).resolve().parents[3] / "db" / "migrations" / "0000_bootstrap.sql"


def _db():
    from investos.core import db
    return db


def _cfg(repo):
    from investos.core import config
    return config.load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


def _mig(d: Path, name: str, sql: str | bytes = "") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(sql if isinstance(sql, bytes) else sql.encode())
    return p


def _tables(con):
    return {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}


def _versions(con):
    return [r[0] for r in con.execute("select version from schema_migrations order by version").fetchall()]


@pytest.fixture
def mdir(tmp_path):
    d = tmp_path / "migs"
    d.mkdir()
    return d


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


# ---------------------------------------------------------------- R13
def test_A21_open_db_fresh(repo):
    db = _db()
    cfg = _cfg(repo)
    con = db.open_db(cfg)
    try:
        assert cfg.db_path == repo.resolve() / "data" / "canonical" / "investos.duckdb"
        assert cfg.db_path.exists()
        rows = con.execute("select version, name, sha256, applied_at from schema_migrations").fetchall()
        assert len(rows) == 1
        v, name, sha, at = rows[0]
        assert (v, name) == ("0000", "0000_bootstrap.sql")
        assert sha == hashlib.sha256((repo / "db" / "migrations" / "0000_bootstrap.sql").read_bytes()).hexdigest()
        assert at is not None
        assert con.execute("select value from meta where key='schema_family'").fetchone()[0] == "investos"
    finally:
        con.close()
    # the real repo ships the bootstrap migration (spec: added only if skeleton lacks it)
    assert REAL_BOOTSTRAP.exists()
    assert "schema_family" in REAL_BOOTSTRAP.read_text()


@pytest.mark.planted
def test_A22_read_only(repo):
    db = _db()
    cfg = _cfg(repo)
    with pytest.raises(db.MigrationError):
        db.open_db(cfg, read_only=True)
    assert not cfg.db_path.exists()

    db.open_db(cfg).close()
    ro = db.open_db(cfg, read_only=True)
    try:
        assert ro.execute("select count(*) from schema_migrations").fetchone()[0] == 1
        with pytest.raises(duckdb.Error):
            ro.execute("create table z(a int)")
    finally:
        ro.close()


# ---------------------------------------------------------------- R14 planted
@pytest.mark.planted
@pytest.mark.parametrize("files", [
    ["0000_a.sql", "0002_c.sql"],     # gap
    ["0000_a.sql", "0000_b.sql"],     # duplicate
    ["0000_a.sql", "bad-name.sql"],   # bad name
])
def test_A23_discovery_errors_before_apply(con, mdir, files):
    db = _db()
    for f in files:
        _mig(mdir, f, "CREATE TABLE a(i INT);" if f == "0000_a.sql" else "SELECT 1;")
    with pytest.raises(db.MigrationError):
        db.apply_migrations(con, mdir)
    assert "a" not in _tables(con)
    if "schema_migrations" in _tables(con):
        assert con.execute("select count(*) from schema_migrations").fetchone()[0] == 0


# ---------------------------------------------------------------- R15
def test_A24_apply_in_order_records_sha(con, mdir):
    db = _db()
    _mig(mdir, "0001_x.sql", "CREATE TABLE x(i INT);")
    _mig(mdir, "0000_bootstrap.sql", BOOTSTRAP_SQL)
    applied = db.apply_migrations(con, mdir)
    assert applied == ["0000_bootstrap.sql", "0001_x.sql"]
    rows = dict(con.execute("select name, sha256 from schema_migrations").fetchall())
    for name in applied:
        assert rows[name] == hashlib.sha256((mdir / name).read_bytes()).hexdigest()
    assert {"meta", "x"} <= _tables(con)


@pytest.mark.planted
def test_A25_failed_migration_rolls_back(con, mdir):
    db = _db()
    _mig(mdir, "0000_bootstrap.sql", BOOTSTRAP_SQL)
    _mig(mdir, "0001_bad.sql", "CREATE TABLE t1(i INT);\nTHIS IS NOT VALID SQL;\n")
    _mig(mdir, "0002_ok.sql", "CREATE TABLE ok(i INT);")
    with pytest.raises(db.MigrationError) as e:
        db.apply_migrations(con, mdir)
    assert "0001_bad.sql" in str(e.value)
    assert "t1" not in _tables(con)
    assert "ok" not in _tables(con)
    assert _versions(con) == ["0000"]


# ---------------------------------------------------------------- R16 planted
def _edit_byte(p: Path):
    p.write_bytes(p.read_bytes() + b"-- x\n")


def _edit_trailing_space(p: Path):
    p.write_bytes(p.read_bytes().rstrip(b"\n") + b" \n")


def _edit_crlf(p: Path):
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))


def _delete(p: Path):
    p.unlink()


@pytest.mark.planted
@pytest.mark.parametrize("edit", [_edit_byte, _edit_trailing_space, _edit_crlf, _delete],
                         ids=["byte", "trailing_space", "crlf", "deleted"])
@pytest.mark.parametrize("pending", [False, True], ids=["nopending", "pending"])
def test_A26_tamper_refusal(con, mdir, edit, pending):
    db = _db()
    _mig(mdir, "0000_bootstrap.sql", BOOTSTRAP_SQL)
    x = _mig(mdir, "0001_x.sql", "CREATE TABLE x(i INT);\n")
    db.apply_migrations(con, mdir)
    edit(x)
    if pending:
        _mig(mdir, "0002_y.sql", "CREATE TABLE y(i INT);")
    with pytest.raises(db.MigrationError) as e:
        db.apply_migrations(con, mdir)
    assert "0001_x.sql" in str(e.value)
    assert "y" not in _tables(con)
    assert _versions(con) == ["0000", "0001"]


# ---------------------------------------------------------------- R17 planted
@pytest.mark.planted
def test_A27_no_backfill(con, mdir):
    db = _db()
    names = {
        "0000_bootstrap.sql": BOOTSTRAP_SQL,
        "0001_a.sql": "CREATE TABLE a(i INT);",
        "0002_late.sql": "CREATE TABLE late(i INT);",
        "0003_c.sql": "CREATE TABLE c(i INT);",
    }
    for n, s in names.items():
        _mig(mdir, n, s)
    con.execute("""CREATE TABLE schema_migrations(
        version VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
        sha256 VARCHAR NOT NULL, applied_at TIMESTAMP NOT NULL)""")
    for n in ("0000_bootstrap.sql", "0001_a.sql", "0003_c.sql"):
        con.execute("insert into schema_migrations values (?,?,?,now()::timestamp)",
                    [n[:4], n, hashlib.sha256((mdir / n).read_bytes()).hexdigest()])
    with pytest.raises(db.MigrationError):
        db.apply_migrations(con, mdir)
    assert "late" not in _tables(con)
    assert _versions(con) == ["0000", "0001", "0003"]


# ---------------------------------------------------------------- R18
def test_A28_idempotent(con, mdir):
    db = _db()
    _mig(mdir, "0000_bootstrap.sql", BOOTSTRAP_SQL)
    _mig(mdir, "0001_x.sql", "CREATE TABLE x(i INT);")
    assert db.apply_migrations(con, mdir) == ["0000_bootstrap.sql", "0001_x.sql"]
    before = con.execute("select * from schema_migrations order by version").fetchall()
    assert db.apply_migrations(con, mdir) == []
    assert db.apply_migrations(con, mdir) == []
    assert con.execute("select * from schema_migrations order by version").fetchall() == before


# ---------------------------------------------------------------- R19
def test_A29_migration_problems_healthy_read_only(repo):
    db = _db()
    cfg = _cfg(repo)
    con = db.open_db(cfg)
    try:
        rows_before = con.execute("select * from schema_migrations order by version").fetchall()
        mtime_before = cfg.db_path.stat().st_mtime_ns
        assert db.migration_problems(con, cfg.migrations_dir) == []
        # pending-but-contiguous file is not a problem
        _mig(cfg.migrations_dir, "0001_next.sql", "CREATE TABLE nxt(i INT);")
        assert db.migration_problems(con, cfg.migrations_dir) == []
        assert con.execute("select * from schema_migrations order by version").fetchall() == rows_before
        assert "nxt" not in _tables(con)
        assert cfg.db_path.stat().st_mtime_ns == mtime_before
    finally:
        con.close()


@pytest.mark.planted
@pytest.mark.parametrize("case", ["edited", "missing", "badname"])
def test_A29b_migration_problems_detects(con, mdir, case):
    db = _db()
    boot = _mig(mdir, "0000_bootstrap.sql", BOOTSTRAP_SQL)
    db.apply_migrations(con, mdir)
    if case == "edited":
        boot.write_bytes(boot.read_bytes() + b" ")
        target = "0000_bootstrap.sql"
    elif case == "missing":
        boot.unlink()
        target = "0000_bootstrap.sql"
    else:
        _mig(mdir, "bad-name.sql", "SELECT 1;")
        target = "bad-name.sql"
    problems = db.migration_problems(con, mdir)
    assert problems and all(isinstance(p, str) for p in problems)
    assert any(target in p for p in problems)


# ---------------------------------------------------------------- Edge
def test_A30_empty_migrations_dir(tmp_path):
    db = _db()
    repo = make_repo(tmp_path / "r", bootstrap=False)
    cfg = _cfg(repo)
    con = db.open_db(cfg)
    try:
        assert con.execute("select count(*) from schema_migrations").fetchone()[0] == 0
        assert db.apply_migrations(con, cfg.migrations_dir) == []
        assert db.migration_problems(con, cfg.migrations_dir) == []
    finally:
        con.close()
