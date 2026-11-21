"""S0-T2 acceptance tests A14..A20 for investos.core.log (rules R9-R12)."""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

FIXED = datetime(2026, 11, 21, 9, 30, 0, tzinfo=timezone.utc)
KEYS = ["ts", "level", "run_id", "task", "event", "fields"]


@pytest.fixture
def log():
    """Fresh copy of the module so per-process state (run_id, sink) is reset per test."""
    import importlib
    from investos.core import log as m
    return importlib.reload(m)


def _lines(sink: io.StringIO):
    return sink.getvalue().splitlines()


def test_A14_one_line_key_order(log):
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("collect.bse").info("fetched", key="2026-11-20", size=1234)
    raw = sink.getvalue()
    assert raw.endswith("\n") and raw.count("\n") == 1
    obj = json.loads(raw)
    assert list(obj) == KEYS
    assert obj["fields"] == {"key": "2026-11-20", "size": 1234}
    assert obj["level"] == "INFO" and obj["task"] == "collect.bse" and obj["event"] == "fetched"
    assert obj["ts"] == "2026-11-21T09:30:00.000000Z"
    assert raw == json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"


@pytest.mark.planted
def test_A15_reserved_field_names_do_not_override(log):
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("real.task").info("x", ts="evil", level="DEBUG", task="evil", run_id="evil")
    obj = json.loads(_lines(sink)[0])
    assert obj["ts"] == "2026-11-21T09:30:00.000000Z"
    assert obj["level"] == "INFO"
    assert obj["task"] == "real.task"
    assert obj["run_id"] != "evil"
    assert obj["fields"] == {"ts": "evil", "level": "DEBUG", "task": "evil", "run_id": "evil"}


def test_A16_run_id_shared_and_replaceable(log):
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("a").info("e1")
    log.get_logger("b").info("e2")
    ids = [json.loads(l)["run_id"] for l in _lines(sink)]
    assert len(ids) == 2 and ids[0] == ids[1]
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", ids[0])
    assert ids[0] == log.get_run_id() == log.get_run_id()

    sink2 = io.StringIO()
    log.configure_logging(sink2, clock=lambda: FIXED, run_id="fixed-1")
    log.get_logger("a").info("e3")
    assert json.loads(_lines(sink2)[0])["run_id"] == "fixed-1"
    assert log.get_run_id() == "fixed-1"


def test_A17_clock_converted_to_utc(log):
    sink = io.StringIO()
    tz = timezone(timedelta(hours=5, minutes=30))
    log.configure_logging(sink, clock=lambda: datetime(2026, 11, 21, 15, 0, 0, tzinfo=tz))
    log.get_logger("t").info("e")
    assert json.loads(_lines(sink)[0])["ts"] == "2026-11-21T09:30:00.000000Z"


@pytest.mark.planted
def test_A17b_naive_clock_rejected(log):
    sink = io.StringIO()
    with pytest.raises(ValueError):
        log.configure_logging(sink, clock=lambda: datetime(2026, 11, 21, 9, 30, 0))
        log.get_logger("t").info("e")


def test_A18_level_filtering(log):
    sink = io.StringIO()
    log.configure_logging(sink, level="WARNING", clock=lambda: FIXED)
    lg = log.get_logger("t")
    lg.debug("d")
    lg.info("i")
    assert sink.getvalue() == ""
    lg.warning("w")
    lg.error("e")
    assert [json.loads(l)["level"] for l in _lines(sink)] == ["WARNING", "ERROR"]


@pytest.mark.planted
def test_A18b_unknown_level_rejected(log):
    with pytest.raises(ValueError):
        log.configure_logging(io.StringIO(), level="LOUD")


@pytest.mark.planted
def test_A19_unserialisable_and_newlines(log):
    class Weird:
        def __repr__(self):
            return "<Weird obj>"

    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("t").info("multi\nline", s="a\nb", st={"x"}, w=Weird())
    raw = sink.getvalue()
    assert raw.count("\n") == 1 and raw.endswith("\n")
    assert len(raw.splitlines()) == 1
    obj = json.loads(raw)
    assert obj["event"] == "multi\nline"
    assert obj["fields"]["s"] == "a\nb"
    assert "\\n" in raw
    assert obj["fields"]["st"] == repr({"x"})
    assert obj["fields"]["w"] == "<Weird obj>"


def test_A20_non_ascii_written_literally(log, tmp_path):
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("t").info("résumé", note="₹100")
    raw = sink.getvalue()
    assert "résumé" in raw and "₹100" in raw
    assert "\\u00e9" not in raw

    # Path sink: UTF-8, append mode
    p = tmp_path / "run.log"
    p.write_text("existing\n", encoding="utf-8")
    log.configure_logging(p, clock=lambda: FIXED)
    log.get_logger("t").info("résumé")
    data = p.read_bytes().decode("utf-8")
    assert data.startswith("existing\n") and "résumé" in data


def test_A20b_sink_parent_missing_raises(log, tmp_path):
    with pytest.raises(FileNotFoundError):
        log.configure_logging(tmp_path / "no_such_dir" / "x.log")
