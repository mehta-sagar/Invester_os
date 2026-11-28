"""S1-T8: `backfill status` (R19, A36) and `investos check backfill` (R20, A37)."""
from __future__ import annotations

import io
import json
import re
from datetime import timedelta

import pytest

from _backfill_helpers import BfEnv, NIGHT1, day, ledger_line, one, ts_text


# ------------------------------------------------------------------ status
@pytest.fixture
def s(tmp_path, monkeypatch):
    e = BfEnv(tmp_path, ids=["announcements"])
    e.table_path.write_text(e.table_path.read_text().replace("2026-11-01", "2025-10-01"))
    e.write_probes(["announcements"], announcements={"earliest_date": "2025-10-25"})       # 400 one-day windows to 2026-11-28
    import investos.backfill.cli as bc
    monkeypatch.setattr(bc, "_load_config", lambda: e.cfg)
    monkeypatch.setattr(bc, "_now", lambda: e.now)
    monkeypatch.setattr(bc, "_make_browser", lambda cfg: pytest.fail("status must not build a browser"))
    return e


def plant_done(e, n=120, rows=10):
    d0 = day("2025-10-25")
    e.put_lines("announcements", *[ledger_line(start=(d0 + timedelta(days=i)).isoformat(),
                                               end=(d0 + timedelta(days=i)).isoformat(), rows=rows,
                                               ts="2026-11-20T10:00:00.000000Z") for i in range(n)])


def counters(e, **nights):
    d = e.ledger_dir / ".state" / "backfill"
    d.mkdir(parents=True, exist_ok=True)
    for k, v in nights.items():
        (d / f"night.{k.replace('_', '-')}.count").write_text(f"{v}\n")


def line_of(out, eid="announcements"):
    return [l for l in out.splitlines() if l.startswith(eid)][0]


def test_A36_status_prints_done_total_rows_and_the_eta_in_nights(s):
    plant_done(s)
    counters(s, **{"2026_11_20": 144})                                         # 120 windows at 1.2 requests each
    code, out, err = s.cli("backfill", "status")
    assert code == 0, err
    l = line_of(out)
    assert "120/400" in l and "1200" in l and "1 night (~0.2 h)" in l
    assert "confirmed" in l


def test_A36_status_shows_unsplittable_failed_and_the_last_error(s):
    plant_done(s)
    s.put_lines("announcements",
                ledger_line(start="2026-02-01", end="2026-02-01", event="unsplittable", ts="2026-11-21T10:00:00.000000Z"),
                ledger_line(start="2026-02-02", end="2026-02-02", event="unsplittable", ts="2026-11-21T10:00:01.000000Z"),
                ledger_line(start="2026-02-03", end="2026-02-03", event="failed", reason="boom one", ts="2026-11-21T11:00:00.000000Z"),
                ledger_line(start="2026-02-04", end="2026-02-04", event="failed", reason="boom two", ts="2026-11-22T11:00:00.000000Z"))
    code, out, _ = s.cli("backfill", "status")
    l = line_of(out)
    assert re.search(r"unsplittable\W+2\b", l) and re.search(r"failed\W+2\b", l) and "boom two" in l and "boom one" not in l
    assert "2026-11-22" in l


def test_A36_status_with_nothing_measured_says_unknown(s):
    code, out, _ = s.cli("backfill", "status")
    l = line_of(out)
    assert "unknown" in l and "0/400" in l


def test_A36_status_with_a_host_block_says_blocked_until_the_next_ist_day(s):
    plant_done(s)
    s.plant_block("www.nseindia.in", source="backfill_announcements")
    code, out, _ = s.cli("backfill", "status")
    assert "blocked until 2026-11-30" in line_of(out) or "blocked until 2026-11-29" in line_of(out)


def test_A36_status_footer_has_the_sum_the_night_counter_and_the_next_window(s):
    plant_done(s)
    counters(s, **{"2026_11_29": 600})
    code, out, _ = s.cli("backfill", "status")
    last = out.strip().splitlines()[-1]
    assert "1200" in last and "600" in last
    assert "00:30" in last


def test_A36_status_makes_no_network_call_and_starts_no_browser_and_json_parses(s):
    plant_done(s)
    code, out, _ = s.cli("backfill", "status", "--json")
    assert code == 0
    data = json.loads(out)
    assert "announcements" in json.dumps(data) and not s.driver.launches and s.driver.n_requests() == 0


def test_A36_status_lists_endpoints_in_table_order(tmp_path, monkeypatch):
    e = BfEnv(tmp_path)
    import investos.backfill.cli as bc
    monkeypatch.setattr(bc, "_load_config", lambda: e.cfg)
    monkeypatch.setattr(bc, "_now", lambda: e.now)
    code, out, _ = e.cli("backfill", "status")
    names = [l.split()[0] for l in out.splitlines() if l.split() and l.split()[0] in e.eps()]
    assert names == list(e.eps())
    assert all("to_probe" in l for l in out.splitlines() if l.split() and l.split()[0] in e.eps())


# ------------------------------------------------------------------ check backfill
@pytest.fixture
def c(tmp_path, monkeypatch):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-11-24")                                              # windows 24..28
    assert e.run().status == "complete"
    import investos.check.backfill_check as m
    monkeypatch.setattr(m, "_clock", lambda: e.now)
    e.mod, e.mp = m, monkeypatch
    return e


def bcheck(e, argv=("backfill",)):
    from investos.check.cli import main
    buf = io.StringIO()
    code = main(list(argv), cfg=e.cfg, stdout=buf)
    rep, d = "", e.cfg.zones["reports"] / "checks" if hasattr(e.cfg.zones["reports"], "joinpath") else None
    from pathlib import Path
    d = Path(e.cfg.zones["reports"]) / "checks"
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        for r in json.loads(p.read_text()).get("results", []):
            rep += "\n".join([r.get("name", ""), r.get("message", "")] + list(r.get("details", []))) + "\n"
        p.unlink()
    return code, buf.getvalue(), rep


def lines_of(e):
    return e.ledger_path("announcements").read_text().splitlines()


def test_A37_the_untouched_clean_state_passes_with_exit_0(c):
    code, out, rep = bcheck(c)
    assert code == 0, (out, rep)
    assert "FAILED" not in out


def test_A37_a_stale_probe_alone_is_only_a_warning(c):
    c.write_probes(["announcements"], announcements={"earliest_date": "2026-11-20", "probed_at": ts_text(c.now - timedelta(days=45))})
    code, out, rep = bcheck(c)
    assert code == 0 and "stale_probe" in rep


def test_A37_a_blocked_line_within_7_days_is_only_a_warning(c):
    p = c.ledger_path("announcements")
    old = p.read_text()
    p.write_text("")
    c.put_lines("announcements", ledger_line(start="2026-11-24", end="2026-11-24", event="blocked",
                                             reason="403", ts=ts_text(c.now - timedelta(days=2))))
    with p.open("a") as fh:
        fh.write(old)
    code, out, rep = bcheck(c)
    assert code == 0 and "WARNING" in rep and "block" in rep.lower()


def plant_ghost(c):
    c.put_lines("announcements", ledger_line(start="2005-01-03", end="2005-01-03", event="planned"))


def plant_unrecorded_ledger(c):
    c.put_lines("announcements", ledger_line(start="2026-11-25", end="2026-11-25", sha="b" * 64, ts="2026-11-29T02:00:00.000000Z",
                                             logical_key="announcements/2026-11-25_2026-11-25-nokey"))


def plant_unrecorded_manifest(c):
    ls = lines_of(c)
    c.ledger_path("announcements").write_text("\n".join(ls[:-1]) + "\n")      # the last window lost its ledger line


def plant_overlap(c):
    c.put_lines("announcements", ledger_line(start="2026-11-25", end="2026-11-26", sha="c" * 64,
                                             logical_key="announcements/2026-11-25_2026-11-26", ts="2026-11-29T02:00:00.000000Z"))


def plant_gap(c):
    ls = [json.loads(l) for l in lines_of(c)]
    for o in ls:
        if o["start"] == "2026-11-26":
            o["event"], o["sha256"], o["logical_key"], o["rows"] = "superseded", "", "", None
    c.ledger_path("announcements").write_text("\n".join(json.dumps(o, sort_keys=True, separators=(",", ":")) for o in ls) + "\n")


def plant_rows_mismatch(c):
    ls = [json.loads(l) for l in lines_of(c)]
    for o in ls:
        o["rows"] = 4
    c.ledger_path("announcements").write_text("\n".join(json.dumps(o, sort_keys=True, separators=(",", ":")) for o in ls) + "\n")


def plant_bytes_changed(c):
    from pathlib import Path
    for p in Path(c.cfg.zones["raw"]).rglob("*"):
        if p.is_file() and p.suffix not in (".jsonl", ".json") and "backfill_announcements" in str(p):
            p.write_bytes(p.read_bytes() + b" ")
            return
    raise AssertionError("no stored raw found to tamper with")


def plant_cap_window(c):
    c.write_probes(["announcements"], announcements={"earliest_date": "2026-11-24", "row_cap": 5})


def plant_unprobed(c):
    c.write_probes(["announcements"], announcements={"earliest_date": "2026-11-24", "status": "needs_review", "reasons": ["x"]})


def plant_budget(c):
    d = c.ledger_dir / ".state" / "backfill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "night.2026-11-29.count").write_text("100001\n")


def plant_stealth(c, tmp_path=None):
    from pathlib import Path
    bad = Path(c.cfg.repo_root) / "bad_backfill_module.py"
    bad.write_text("import random\n")
    c.mp.setattr(c.mod, "source_files", lambda: [bad])


CASES = [("ghost_window", plant_ghost), ("unrecorded_fetch", plant_unrecorded_ledger),
         ("unrecorded_fetch", plant_unrecorded_manifest), ("overlap", plant_overlap), ("gap", plant_gap),
         ("rows_mismatch", plant_rows_mismatch), ("cap_window_trusted", plant_cap_window),
         ("unprobed_fetch", plant_unprobed), ("budget_exceeded", plant_budget), ("stealth_string", plant_stealth)]


@pytest.mark.planted
@pytest.mark.parametrize("code_word,plant", CASES, ids=[f"{w}-{p.__name__}" for w, p in CASES])
def test_A37_each_planted_failure_is_found_with_its_code_and_exit_1(c, code_word, plant):
    plant(c)
    code, out, rep = bcheck(c)
    assert code == 1, (out, rep)
    assert code_word in rep, rep


@pytest.mark.planted
def test_A37_changed_stored_bytes_fail_the_manifest_verify(c):
    plant_bytes_changed(c)
    code, out, rep = bcheck(c)
    assert code == 1 and re.search(r"verify|integrity|sha|corrupt|changed", rep, re.I), rep


def test_A37_the_check_is_read_only(c):
    from pathlib import Path
    def tree():
        return {str(p): p.read_bytes() for p in Path(c.cfg.zones["ledger"]).rglob("*") if p.is_file()}
    plant_ghost(c)
    before = tree()
    bcheck(c)
    assert tree() == before


@pytest.mark.planted
def test_A37_planted_run_is_caught_and_works_on_empty_real_data(tmp_path, monkeypatch):
    e = BfEnv(tmp_path)
    import investos.check.backfill_check as m
    monkeypatch.setattr(m, "_clock", lambda: e.now)
    code, out, rep = bcheck(e, ("backfill", "--planted"))
    assert code == 0 and "caught" in out, (out, rep)
    assert not e.ledger_dir.joinpath("backfill", "plant_ghost.windows.jsonl").exists()


def test_A37_the_check_is_registered_with_the_builtin_checks():
    from investos.check.cli import load_builtin_checks
    from investos.check.registry import DEFAULT_REGISTRY
    load_builtin_checks()
    assert "backfill" in DEFAULT_REGISTRY.names()
