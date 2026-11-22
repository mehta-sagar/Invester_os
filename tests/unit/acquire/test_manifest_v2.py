"""S1-T2 acceptance tests A24-A34 and rules R15-R21 for manifest schema v2 (specs/core_manifest.md,
"Stage 1 amendment"). Red on the Stage 0 code; green once v2 is built."""
from __future__ import annotations

import ast
import inspect
import json
import multiprocessing as mp
import os
import fcntl
from dataclasses import fields
from pathlib import Path

import pytest

from _acq_helpers import FETCHED, canon, kinds, lines_of, manifest_path, put, raw_file, sha, snap
from _v2_helpers import (MODES, V1_FIELDS, V2_FIELDS, bad_line_numbers, put_raw, put2, v1_obj, v2_obj,
                         worker_v2_reader, worker_v2_writer, write_lines)

REPO = Path(__file__).resolve().parents[3]
LOOKUPS = [
    ("get_by_key", lambda st: st.get_by_key("bse", "2026-11-20")),
    ("get_all_by_key", lambda st: st.get_all_by_key("bse", "2026-11-20")),
    ("get_by_sha", lambda st: st.get_by_sha("bse", sha(b"mid"))),
    ("entries", lambda st: st.entries("bse")),
]
GOOD = [b"first", b"mid", b"last"]


@pytest.fixture
def v2_control(tmp_path_factory):
    """Positive control: a fully valid v2 store() call must succeed, so that rejection tests cannot pass
    merely because the old store() raises TypeError on unknown keyword arguments."""
    from investos.acquire.manifest import ManifestStore
    base = tmp_path_factory.mktemp("control")
    (base / "raw").mkdir()
    (base / "man").mkdir()
    ManifestStore(base / "raw", base / "man").store(
        "ctl", b"control", url="https://c.test/f", source_version="v", logical_key="k", run_id="r",
        fetched_at=FETCHED, access_mode="honest", request_headers={"User-Agent": "u"},
        final_url="https://c.test/f", fetch_host="h")
    yield


def _tree(*roots):
    return [snap(r) for r in roots]


# ============================================================ A24 (R15): v1 files still read
def test_A24_v1_lines_read_as_schema_1(mk, roots):
    raw, man = roots
    texts = [canon(v1_obj(b, key=f"k{i}")) for i, b in enumerate(GOOD)]
    write_lines(man, "bse", texts, raw_root=raw, payloads=GOOD)
    st = mk()
    ents = st.entries("bse")
    assert len(ents) == 3
    for e, text in zip(ents, texts):
        assert e.schema == 1
        assert e.access_mode is None and e.request_headers is None
        assert e.final_url is None and e.fetch_host is None
        assert e.to_json_line().rstrip("\n") == text           # byte-for-byte, no schema key added
    assert st.get_by_key("bse", "k1").access_mode is None
    assert st.get_by_sha("bse", sha(b"mid"))[0].schema == 1
    assert st.verify() == []


def test_A24_edge_10000_v1_lines_plus_one_v2(mk, roots):
    raw, man = roots
    lines = [canon(v1_obj(b"x", key=f"k{i}")) for i in range(10_000)]
    lines.append(canon(v2_obj(b"x", key="new")))
    write_lines(man, "bse", lines)
    st = mk()
    assert len(st.entries("bse")) == 10_001
    old = st.get_by_key("bse", "k5")
    assert old.schema == 1 and old.access_mode is None
    new = st.get_by_key("bse", "new")
    assert new.schema == 2 and new.access_mode == "honest"


# ============================================================ A25 (R15, planted): strict v2 reading
def _bad_v2_variants():
    d = b"mid"
    g = v2_obj(d)

    def without(k):
        o = dict(g)
        o.pop(k)
        return canon(o)
    out = {
        "schema_1": canon({**g, "schema": 1}),
        "schema_3": canon({**g, "schema": 3}),
        "schema_str": canon({**g, "schema": "2"}),
        "schema_true": canon({**g, "schema": True}),
        "schema_null": canon({**g, "schema": None}),
        "schema_float": canon({**g, "schema": 2.0}),
        "no_access_mode": without("access_mode"),
        "no_request_headers": without("request_headers"),
        "no_final_url": without("final_url"),
        "no_fetch_host": without("fetch_host"),
        "final_url_int": canon({**g, "final_url": 5}),
        "final_url_empty": canon({**g, "final_url": ""}),
        "fetch_host_int": canon({**g, "fetch_host": 5}),
        "fetch_host_empty": canon({**g, "fetch_host": ""}),
        "extra_field": canon({**g, "foo": "bar"}),
        "v1_shape_with_access_mode": canon({**v1_obj(d), "access_mode": "honest"}),
        "v1_shape_with_headers": canon({**v1_obj(d), "request_headers": {}}),
        "access_mode_stealth": canon({**g, "access_mode": "stealth"}),
        "access_mode_upper": canon({**g, "access_mode": "Honest"}),
        "access_mode_null": canon({**g, "access_mode": None}),
        "headers_list": canon({**g, "request_headers": ["a"]}),
        "headers_str": canon({**g, "request_headers": "a: b"}),
        "header_value_int": canon({**g, "request_headers": {"A": 1}}),
        "header_value_null": canon({**g, "request_headers": {"A": None}}),
        "null_headers_with_honest": canon({**g, "request_headers": None, "access_mode": "honest"}),
        "null_headers_with_scoped": canon({**g, "request_headers": None, "access_mode": "scoped_exception"}),
    }
    return out


BAD_V2 = _bad_v2_variants()


@pytest.mark.planted
@pytest.mark.parametrize("variant", sorted(BAD_V2))
def test_A25_bad_v2_line_rejected_by_every_lookup_and_verify(mk, roots, variant):
    raw, man = roots
    good = [canon(v2_obj(b, key="2026-11-20")) for b in GOOD]
    lines = [good[0], BAD_V2[variant], good[1], good[2]]
    write_lines(man, "bse", lines, raw_root=raw, payloads=GOOD + [b"mid"])
    st = mk()
    from investos.acquire.manifest import ManifestCorruptError
    for name, fn in LOOKUPS:
        with pytest.raises(ManifestCorruptError) as ei:
            fn(st)
        assert "line 2" in str(ei.value) and "bse.jsonl" in str(ei.value), (name, str(ei.value))
    problems = st.verify("bse")
    assert bad_line_numbers(problems) == [2], problems
    assert all(p.kind == "bad_line" for p in problems if p.line == 2)


@pytest.mark.planted
@pytest.mark.parametrize("val", [1, 3, "2", True, None, 2.0])
def test_A25_unsupported_schema_message(mk, roots, val):
    raw, man = roots
    write_lines(man, "bse", [canon({**v2_obj(b"mid"), "schema": val})])
    from investos.acquire.manifest import ManifestCorruptError
    with pytest.raises(ManifestCorruptError, match="unsupported schema"):
        mk().entries("bse")


@pytest.mark.planted
def test_A25_stage0_rules_still_hold_on_v2_lines(mk, roots):
    """R15: every R8 condition also applies to v2 lines (sha upper-case, fetched_at w/o Z, size str, blank)."""
    raw, man = roots
    from investos.acquire.manifest import ManifestCorruptError
    g = v2_obj(b"mid")
    for bad in (canon({**g, "sha256": g["sha256"].upper()}), canon({**g, "fetched_at": g["fetched_at"][:-1]}),
                canon({**g, "size": "5"}), ""):
        write_lines(man, "bse", [canon(g), bad, canon(g)])
        st = mk()
        with pytest.raises(ManifestCorruptError, match="line 2"):
            st.entries("bse")
        assert bad_line_numbers(st.verify("bse")) == [2]


def test_A25_v1_line_with_extra_field_still_corrupt(mk, roots):
    raw, man = roots
    write_lines(man, "bse", [canon({**v1_obj(b"mid"), "foo": 1})])
    from investos.acquire.manifest import ManifestCorruptError
    with pytest.raises(ManifestCorruptError):
        mk().entries("bse")


def test_R15_valid_edges_accepted(mk, roots):
    """Empty headers allowed for all modes; null headers only for real_browser; fragment in final_url kept."""
    raw, man = roots
    lines = [canon(v2_obj(b"a", key="a", access_mode=m, request_headers={})) for m in MODES]
    lines.append(canon(v2_obj(b"b", key="b", access_mode="real_browser", request_headers=None)))
    lines.append(canon(v2_obj(b"c", key="c", final_url="https://x.test/p#frag")))
    write_lines(man, "bse", lines, raw_root=raw, payloads=[b"a", b"b", b"c"])
    st = mk()
    ents = st.entries("bse")
    assert [e.access_mode for e in ents[:4]] == list(MODES) + ["real_browser"]
    assert ents[3].request_headers is None and ents[0].request_headers == {}
    assert ents[4].final_url == "https://x.test/p#frag"
    assert st.verify("bse") == []


def test_R15_cookie_redaction_form_in_a_line(mk, roots):
    raw, man = roots
    ok = [{"Cookie": "<redacted>"}, {"Cookie": "a=<redacted>"}, {"Cookie": "a=<redacted>; b=<redacted>"},
          {"cookie": "a=<redacted>"}]
    write_lines(man, "bse", [canon(v2_obj(b"mid", key=f"k{i}", request_headers=h)) for i, h in enumerate(ok)],
                raw_root=raw, payloads=[b"mid"])
    st = mk()
    assert len(st.entries("bse")) == 4 and st.verify("bse") == []


# ============================================================ A26 (R16): writer writes v2 only
def test_A26_store_writes_thirteen_field_v2_line(mk, roots):
    raw, man = roots
    st = mk()
    hdr = {"User-Agent": "u", "Accept-Encoding": "identity"}
    e = st.store("bse", b"hello", url="https://example.test/f", source_version="v1", logical_key="2026-11-20",
                 run_id="run-1", fetched_at=FETCHED, access_mode="honest", request_headers=hdr,
                 final_url="https://example.test/f", fetch_host="mac-1")
    text = manifest_path(man, "bse").read_bytes().decode("utf-8")
    assert text.endswith("\n") and text.count("\n") == 1
    obj = json.loads(text)
    assert set(obj) == V2_FIELDS and len(obj) == 13
    assert obj["schema"] == 2 and type(obj["schema"]) is int
    assert obj["access_mode"] == "honest" and obj["fetch_host"] == "mac-1"
    assert obj["request_headers"] == hdr and obj["final_url"] == "https://example.test/f"
    assert text == json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    assert e.schema == 2 and e.access_mode == "honest" and e.fetch_host == "mac-1"
    assert dict(e.request_headers) == hdr
    back = mk().entries("bse")[0]
    assert back == e
    assert e.to_json_line().rstrip("\n") == text.rstrip("\n")
    assert back.to_json_line().rstrip("\n") == text.rstrip("\n")


def test_A26_mixed_v1_v2_v1_file_order_and_latest_wins(mk, roots):
    raw, man = roots
    lines = [canon(v1_obj(b"a", key="K")), canon(v2_obj(b"b", key="K")), canon(v1_obj(b"c", key="K")),
             canon(v1_obj(b"d", key="J"))]
    write_lines(man, "bse", lines, raw_root=raw, payloads=[b"a", b"b", b"c", b"d"])
    st = mk()
    assert [e.schema for e in st.entries("bse")] == [1, 2, 1, 1]
    assert [e.schema for e in st.get_all_by_key("bse", "K")] == [1, 2, 1]
    assert st.get_by_key("bse", "K").sha256 == sha(b"c")
    assert st.get_by_sha("bse", sha(b"b"))[0].schema == 2
    assert st.verify("bse") == []


def test_A26_v2_appended_after_v1_in_same_file_and_cache(mk, roots):
    """R15/R20: a store that already indexed a v1-only file sees an appended v2 line (cache extended)."""
    raw, man = roots
    write_lines(man, "bse", [canon(v1_obj(b"a", key="old"))], raw_root=raw, payloads=[b"a"])
    st = mk()
    assert st.get_by_key("bse", "old").schema == 1
    e = put2(st, "bse", b"new", logical_key="newk")
    assert st.get_by_key("bse", "newk") == e and e.schema == 2
    assert st.get_by_key("bse", "old").access_mode is None
    st2 = mk()
    assert [x.schema for x in st2.entries("bse")] == [1, 2]
    assert st2.verify() == []


def test_R16_no_v1_line_is_ever_written(mk, roots):
    raw, man = roots
    st = mk()
    for i, m in enumerate(MODES):
        put2(st, "bse", f"p{i}".encode(), logical_key=f"k{i}", access_mode=m)
    put2(st, "bse", b"nb", logical_key="nb", access_mode="real_browser", request_headers=None)
    for l in lines_of(manifest_path(man, "bse")):
        o = json.loads(l)
        assert o["schema"] == 2 and set(o) == V2_FIELDS


def test_R16_manifest_entry_has_the_five_new_fields():
    from investos.acquire.manifest import ManifestEntry
    names = [f.name for f in fields(ManifestEntry)]
    for n in ("schema", "access_mode", "request_headers", "final_url", "fetch_host"):
        assert n in names


def test_R16_entry_headers_are_read_only_copy(mk):
    st = mk()
    hdr = {"User-Agent": "u"}
    e = put2(st, "bse", b"x", request_headers=hdr)
    hdr["Evil"] = "1"
    hdr["User-Agent"] = "changed"
    assert dict(e.request_headers) == {"User-Agent": "u"}
    with pytest.raises((TypeError, AttributeError)):
        e.request_headers["Evil"] = "1"
    back = mk().entries("bse")[0]
    with pytest.raises((TypeError, AttributeError)):
        back.request_headers["Evil"] = "1"


# ============================================================ A27 (R16, planted): required arguments and validation
NEW_ARGS = ["access_mode", "request_headers", "final_url", "fetch_host"]


def test_A27_signature_four_new_keyword_only_required():
    from investos.acquire.manifest import ManifestStore
    sig = inspect.signature(ManifestStore.store)
    for n in NEW_ARGS:
        p = sig.parameters[n]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty, n


def _kw(**over):
    kw = dict(url="https://example.test/f", source_version="v1", logical_key="k", run_id="r", fetched_at=FETCHED,
              access_mode="honest", request_headers={"User-Agent": "u"}, final_url="https://example.test/f",
              fetch_host="h")
    kw.update(over)
    return kw


@pytest.mark.planted
@pytest.mark.parametrize("missing", NEW_ARGS)
@pytest.mark.usefixtures("v2_control")
def test_A27_missing_new_argument_is_typeerror_and_writes_nothing(mk, roots, missing):
    raw, man = roots
    st = mk()
    before = _tree(raw, man)
    kw = _kw()
    kw.pop(missing)
    with pytest.raises(TypeError):
        st.store("bse", b"x", **kw)
    assert _tree(raw, man) == before


INVALID_ARGS = [
    ("mode_stealth", dict(access_mode="stealth"), ValueError),
    ("mode_upper", dict(access_mode="HONEST"), ValueError),
    ("mode_empty", dict(access_mode=""), ValueError),
    ("mode_none", dict(access_mode=None), (ValueError, TypeError)),
    ("headers_list", dict(request_headers=["a"]), TypeError),
    ("headers_str", dict(request_headers="User-Agent: u"), TypeError),
    ("headers_pairs", dict(request_headers=[("a", "b")]), TypeError),
    ("none_with_honest", dict(request_headers=None, access_mode="honest"), ValueError),
    ("none_with_scoped", dict(request_headers=None, access_mode="scoped_exception"), ValueError),
]


@pytest.mark.planted
@pytest.mark.parametrize("name,over,exc", INVALID_ARGS, ids=[x[0] for x in INVALID_ARGS])
@pytest.mark.usefixtures("v2_control")
def test_A27_invalid_arguments_rejected_and_nothing_written(mk, roots, name, over, exc):
    raw, man = roots
    st = mk()
    before = _tree(raw, man)
    with pytest.raises(exc):
        st.store("bse", b"x", **_kw(**over))
    assert _tree(raw, man) == before
    assert not list(raw.rglob(".tmp-*")) and not list(raw.rglob("*")) and not list(man.iterdir())


def test_A27_none_headers_with_real_browser_written_as_null(mk, roots):
    raw, man = roots
    st = mk()
    e = put2(st, "bse", b"x", access_mode="real_browser", request_headers=None)
    assert e.request_headers is None and e.access_mode == "real_browser"
    obj = json.loads(manifest_path(man, "bse").read_text())
    assert obj["request_headers"] is None
    assert '"request_headers":null' in manifest_path(man, "bse").read_text()
    assert mk().entries("bse")[0].request_headers is None
    assert mk().verify() == []


@pytest.mark.parametrize("mode", MODES)
def test_A27_empty_headers_allowed_for_every_mode_and_differs_from_null(mk, roots, mode):
    raw, man = roots
    st = mk()
    e = put2(st, "bse", b"x", access_mode=mode, request_headers={})
    assert dict(e.request_headers) == {}
    assert '"request_headers":{}' in manifest_path(man, "bse").read_text()
    assert mk().verify() == []


@pytest.mark.parametrize("mode", MODES)
def test_R16_each_access_mode_round_trips(mk, roots, mode):
    raw, man = roots
    st = mk()
    put2(st, "bse", b"x", access_mode=mode)
    assert mk().entries("bse")[0].access_mode == mode
    assert mk().verify() == []


@pytest.mark.usefixtures("v2_control")
def test_R16_validation_happens_before_filesystem_access(tmp_path):
    """A bad argument raises ValueError/TypeError even when raw_root is missing (not ManifestError)."""
    from investos.acquire.manifest import ManifestStore
    man = tmp_path / "man"
    man.mkdir()
    st = ManifestStore(tmp_path / "no-raw", man)
    with pytest.raises(ValueError):
        st.store("bse", b"x", **_kw(access_mode="stealth"))
    with pytest.raises(ValueError):
        st.store("bse", b"x", **_kw(final_url="ftp://x"))
    with pytest.raises(TypeError):
        st.store("bse", b"x", **_kw(request_headers=["a"]))
    assert list(man.iterdir()) == []


@pytest.mark.usefixtures("v2_control")
def test_R16_source_is_validated_first(mk):
    st = mk()
    with pytest.raises(ValueError, match="source"):
        st.store("A", b"x", **_kw(access_mode="stealth"))


# ============================================================ A28 (R17, planted): header record rules
def _hdrs(n):
    return {f"X-H{i}": "v" for i in range(n)}


BAD_HEADERS = [
    ("name_space", {"Bad Name": "v"}, ValueError),
    ("name_colon", {"Bad:Name": "v"}, ValueError),
    ("name_empty", {"": "v"}, ValueError),
    ("name_129", {"N" * 129: "v"}, ValueError),
    ("value_cr", {"A": "x\ry"}, ValueError),
    ("value_lf", {"A": "x\ny"}, ValueError),
    ("value_nul", {"A": "x\x00y"}, ValueError),
    ("value_4097", {"A": "v" * 4097}, ValueError),
    ("value_int", {"A": 1}, (ValueError, TypeError)),
    ("value_none", {"A": None}, (ValueError, TypeError)),
    ("count_65", _hdrs(65), ValueError),
    ("total_17k", {f"X-{i}": "v" * 4000 for i in range(5)}, ValueError),
    ("dup_case", {"User-Agent": "a", "user-agent": "b"}, ValueError),
    ("authorization", {"Authorization": "Bearer x"}, ValueError),
    ("authorization_lc", {"authorization": "Bearer x"}, ValueError),
    ("proxy_authorization", {"Proxy-Authorization": "x"}, ValueError),
    ("cookie2", {"Cookie2": "x"}, ValueError),
    ("set_cookie", {"Set-Cookie": "a=b"}, ValueError),
    ("set_cookie_upper", {"SET-COOKIE": "a=b"}, ValueError),
]


@pytest.mark.planted
@pytest.mark.parametrize("name,hdrs,exc", BAD_HEADERS, ids=[x[0] for x in BAD_HEADERS])
@pytest.mark.usefixtures("v2_control")
def test_A28_bad_header_sets_rejected_and_nothing_written(mk, roots, name, hdrs, exc):
    raw, man = roots
    st = mk()
    before = _tree(raw, man)
    with pytest.raises(exc):
        st.store("bse", b"x", **_kw(request_headers=hdrs))
    assert _tree(raw, man) == before and not list(man.iterdir())


def test_A28_boundaries_accepted(mk, roots):
    raw, man = roots
    st = mk()
    put2(st, "bse", b"1", logical_key="a", request_headers=_hdrs(64))
    put2(st, "bse", b"2", logical_key="b", request_headers={"N" * 128: "v" * 4096})
    put2(st, "bse", b"3", logical_key="c", request_headers={"X-Tok": "!#$%&'*+-.^_`|~09azAZ"})
    put2(st, "bse", b"4", logical_key="d", request_headers={"X-Q": 'q"uote \\ back é ünï ✓'})
    assert mk().verify() == []
    ents = mk().entries("bse")
    assert len(ents[0].request_headers) == 64
    assert ents[3].request_headers["X-Q"] == 'q"uote \\ back é ünï ✓'      # exact round trip, one line
    assert len(lines_of(manifest_path(man, "bse"))) == 4


def test_R17_header_case_kept_and_order_independent_of_input(mk, roots):
    raw, man = roots
    st = mk()
    e = put2(st, "bse", b"x", request_headers={"user-agent": "u", "ACCEPT": "*/*", "X-Mixed-Case": "V"})
    assert dict(e.request_headers) == {"user-agent": "u", "ACCEPT": "*/*", "X-Mixed-Case": "V"}
    assert dict(mk().entries("bse")[0].request_headers) == dict(e.request_headers)


# ============================================================ A29 (R17, planted): cookie redaction
@pytest.mark.planted
def test_A29_cookie_values_never_reach_disk_or_entry(mk, roots):
    raw, man = roots
    st = mk()
    hdr = {"Cookie": "nsit=SECRET1; nseappid=SECRET2", "Referer": "https://www.nseindia.com/x"}
    orig = dict(hdr)
    e = put2(st, "bse", b"x", access_mode="scoped_exception", request_headers=hdr)
    assert hdr == orig                                           # caller's mapping untouched
    assert e.request_headers["Cookie"] == "nsit=<redacted>; nseappid=<redacted>"
    assert e.request_headers["Referer"] == "https://www.nseindia.com/x"
    data = manifest_path(man, "bse").read_bytes()
    assert b"SECRET1" not in data and b"SECRET2" not in data
    assert b"nsit=<redacted>; nseappid=<redacted>" in data
    for p in man.iterdir():
        assert b"SECRET" not in p.read_bytes()
    back = mk().entries("bse")[0]
    assert back.request_headers["Cookie"] == "nsit=<redacted>; nseappid=<redacted>"
    assert mk().verify() == []


@pytest.mark.planted
def test_A29_non_pair_cookie_becomes_redacted_and_key_case_kept(mk, roots):
    raw, man = roots
    st = mk()
    e = put2(st, "bse", b"x", request_headers={"cookie": "garbage"})
    assert dict(e.request_headers) == {"cookie": "<redacted>"}
    assert b"garbage" not in manifest_path(man, "bse").read_bytes()
    e2 = put2(st, "bse", b"y", logical_key="k2", request_headers={"COOKIE": "a=1"})
    assert dict(e2.request_headers) == {"COOKIE": "a=<redacted>"}
    assert mk().verify() == []


@pytest.mark.planted
def test_A29_cookie_with_quotes_and_unicode_still_redacted(mk, roots):
    raw, man = roots
    st = mk()
    put2(st, "bse", b"x", request_headers={"Cookie": 'tok="SECRET3"; u=é'})
    data = manifest_path(man, "bse").read_bytes()
    assert b"SECRET3" not in data and "é".encode() not in data


# ============================================================ A30 (R18, planted): verify on hand-written lines
def _store_with_extra(mk, roots, extra_obj):
    raw, man = roots
    st = mk()
    put2(st, "bse", b"good-1", logical_key="g1")
    put_raw(raw, "bse", b"mid")
    with open(manifest_path(man, "bse"), "ab") as fh:
        fh.write((canon(extra_obj) + "\n").encode())
    put_raw(raw, "bse", b"good-2")
    return st


A30_BAD = [
    ("raw_cookie_value", {"Cookie": "nsit=SECRET1"}),
    ("raw_cookie_lowercase_name", {"cookie": "nsit=SECRET1; b=<redacted>"}),
    ("no_space_form", {"Cookie": "a=<redacted>;b=<redacted>"}),
    ("cookie_partly_redacted", {"Cookie": "a=<redacted>; b=SECRET"}),
    ("cookie_empty", {"Cookie": ""}),
    ("authorization", {"User-Agent": "u", "Authorization": "Bearer x"}),
    ("set_cookie", {"Set-Cookie": "a=b"}),
    ("sixty_five_headers", _hdrs(65)),
    ("header_crlf", {"A": "x\r\ny"}),
    ("dup_case", {"User-Agent": "a", "user-agent": "b"}),
    ("bad_name", {"Bad Name": "v"}),
    ("value_too_long", {"A": "v" * 4097}),
]


@pytest.mark.planted
@pytest.mark.parametrize("name,hdrs", A30_BAD, ids=[x[0] for x in A30_BAD])
def test_A30_verify_flags_bad_headers_in_hand_written_line(mk, roots, name, hdrs):
    st = _store_with_extra(mk, roots, v2_obj(b"mid", key="hand", request_headers=hdrs))
    probs = st.verify("bse")
    assert [(p.kind, p.line) for p in probs] == [("bad_line", 2)], probs
    from investos.acquire.manifest import ManifestCorruptError
    with pytest.raises(ManifestCorruptError, match="line 2"):
        st.entries("bse")


def test_A30_healthy_mixed_store_is_clean_and_verify_modifies_nothing(mk, roots):
    raw, man = roots
    write_lines(man, "bse", [canon(v1_obj(b"old", key="o"))], raw_root=raw, payloads=[b"old"])
    st = mk()
    put2(st, "bse", b"n1", logical_key="n1", request_headers={"Cookie": "a=1; b=2", "User-Agent": "u"})
    put2(st, "bse", b"n2", logical_key="n2", access_mode="real_browser", request_headers=None)
    put2(st, "nse", b"n3", logical_key="n3")
    before = _tree(raw, man)
    mtimes = {p: p.stat().st_mtime_ns for p in man.iterdir()}
    assert st.verify() == [] and st.verify("bse") == [] and st.verify("nse") == []
    assert _tree(raw, man) == before
    assert {p: p.stat().st_mtime_ns for p in man.iterdir()} == mtimes


def test_A30_verify_never_raises_on_garbage_v2(mk, roots):
    raw, man = roots
    write_lines(man, "bse", [canon(v2_obj(b"mid")), '{"schema":2}', '{"schema":[]}', '{"schema":2,"request_headers":5}'])
    probs = mk().verify("bse")
    assert bad_line_numbers(probs) == [2, 3, 4]


# ============================================================ A31 (R20, S0-FIX A2 regression): 2 processes
def test_A31_reader_never_sees_a_torn_v2_line(roots):
    raw, man = roots
    ctx = mp.get_context("spawn")
    stop, q = ctx.Event(), ctx.Queue()
    reader = ctx.Process(target=worker_v2_reader, args=(str(raw), str(man), "bse", stop, q))
    writer = ctx.Process(target=worker_v2_writer, args=(str(raw), str(man), "bse", 200, 2048))
    reader.start()
    writer.start()
    writer.join(180)
    stop.set()
    reads, errs = q.get(timeout=60)
    reader.join(30)
    assert writer.exitcode == 0 and reader.exitcode == 0
    assert reads > 0
    assert errs == 0, f"{errs} false ManifestCorruptError in {reads} lookups"
    from investos.acquire.manifest import ManifestStore
    st = ManifestStore(raw, man)
    assert st.verify() == []
    ents = st.entries("bse")
    assert len(ents) == 200 and all(e.schema == 2 and len(e.request_headers["X-Pad"]) == 2048 for e in ents)


# ============================================================ A32 (R20, R19, planted): torn v2 tail, runbook, source scan
@pytest.mark.planted
def test_A32_torn_v2_tail_refused_reported_and_repairable_by_runbook(mk, roots):
    raw, man = roots
    st = mk()
    for i in range(3):
        put2(st, "bse", f"p{i}".encode(), logical_key=f"k{i}")
    mp_ = manifest_path(man, "bse")
    data = mp_.read_bytes()
    mp_.write_bytes(data[:-25])                                  # cut the last v2 line in the middle
    before = mp_.read_bytes()
    assert not before.endswith(b"\n")
    from investos.acquire.manifest import ManifestCorruptError
    with pytest.raises(ManifestCorruptError):
        put2(st, "bse", b"more", logical_key="k9")
    assert mp_.read_bytes() == before
    with pytest.raises(ManifestCorruptError):
        mk().entries("bse")
    assert bad_line_numbers(mk().verify("bse")) == [3]
    # runbook steps 4-6: take the lock, truncate to the last complete line, nothing else
    fd = os.open(man / "bse.jsonl.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        cur = mp_.read_bytes()
        os.truncate(mp_, cur.rfind(b"\n") + 1)
    finally:
        os.close(fd)
    fresh = mk()
    assert fresh.verify("bse") == []
    assert [e.logical_key for e in fresh.entries("bse")] == ["k0", "k1"]
    put2(fresh, "bse", b"again", logical_key="k2")               # store works again
    assert fresh.verify("bse") == []


def test_A32_unterminated_tail_under_held_lock_waits_then_reads(mk, roots):
    """R20: reader finding an unterminated v2 tail waits on LOCK_SH and re-reads before judging."""
    import threading
    import time
    raw, man = roots
    st = mk()
    put2(st, "bse", b"a", logical_key="a")
    mp_ = manifest_path(man, "bse")
    full = (canon(v2_obj(b"b", key="b")) + "\n").encode()
    fd = os.open(man / "bse.jsonl.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    with open(mp_, "ab") as fh:
        fh.write(full[:40])                                      # writer mid-append
    put_raw(raw, "bse", b"b")
    res = {}

    def reader():
        res["probs"] = mk().verify("bse")
    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.4)
    assert t.is_alive(), "reader did not wait for the writer's lock"
    with open(mp_, "ab") as fh:
        fh.write(full[40:])
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    t.join(30)
    assert res["probs"] == []


def test_A32_runbook_has_v2_paragraph():
    text = (REPO / "docs" / "runbooks" / "manifest-torn-tail.md").read_text(encoding="utf-8")
    assert "schema" in text and "v2" in text


def test_A32_source_scan_only_v1_entries_serialise_without_schema():
    src = (REPO / "src" / "investos" / "acquire" / "manifest.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # the ManifestEntry.to_json_line serialiser branches on schema; nothing else builds a line dict w/o it
    assert "schema" in src
    callers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "dumps" \
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "json":
                    callers.add(fn.name)
    assert "store" not in callers, "store must serialise through ManifestEntry.to_json_line"
    assert "to_json_line" in callers


def test_A32_v1_entry_serialises_without_schema_v2_with(mk, roots):
    raw, man = roots
    write_lines(man, "bse", [canon(v1_obj(b"a"))], raw_root=raw, payloads=[b"a"])
    e1 = mk().entries("bse")[0]
    assert "schema" not in json.loads(e1.to_json_line())
    e2 = put2(mk(), "nse", b"b")
    o2 = json.loads(e2.to_json_line())
    assert o2["schema"] == 2 and set(o2) == V2_FIELDS


# ============================================================ A34 (R21, planted): final_url and fetch_host
BAD_URLS = [("empty", "", ValueError), ("ftp", "ftp://x", ValueError), ("newline", "https://x/\n", ValueError),
            ("tab", "https://x/\t", ValueError), ("del", "https://x/\x7f", ValueError),
            ("no_scheme", "example.test/f", ValueError), ("len_2049", "https://x/" + "a" * (2049 - 10), ValueError),
            ("int", 5, TypeError), ("none", None, (ValueError, TypeError))]
BAD_HOSTS = [("empty", "", ValueError), ("leading_space", " mac", ValueError), ("trailing_space", "mac ", ValueError),
             ("tab", "a\tb", ValueError), ("newline", "a\nb", ValueError), ("nul", "a\x00b", ValueError),
             ("len_256", "h" * 256, ValueError), ("int", 5, TypeError), ("none", None, (ValueError, TypeError))]


@pytest.mark.planted
@pytest.mark.parametrize("name,val,exc", BAD_URLS, ids=[x[0] for x in BAD_URLS])
@pytest.mark.usefixtures("v2_control")
def test_A34_bad_final_url_rejected_and_nothing_written(mk, roots, name, val, exc):
    raw, man = roots
    st = mk()
    before = _tree(raw, man)
    with pytest.raises(exc):
        st.store("bse", b"x", **_kw(final_url=val))
    assert _tree(raw, man) == before and not list(man.iterdir())


@pytest.mark.planted
@pytest.mark.parametrize("name,val,exc", BAD_HOSTS, ids=[x[0] for x in BAD_HOSTS])
@pytest.mark.usefixtures("v2_control")
def test_A34_bad_fetch_host_rejected_and_nothing_written(mk, roots, name, val, exc):
    raw, man = roots
    st = mk()
    before = _tree(raw, man)
    with pytest.raises(exc):
        st.store("bse", b"x", **_kw(fetch_host=val))
    assert _tree(raw, man) == before and not list(man.iterdir())


def test_A34_boundaries_and_unicode_round_trip_verbatim(mk, roots):
    raw, man = roots
    st = mk()
    url = "https://x.test/" + "a" * (2048 - 15)
    host = "h" * 255
    put2(st, "bse", b"1", logical_key="a", final_url=url, fetch_host=host)
    put2(st, "bse", b"2", logical_key="b", final_url="http://x.test/ü?q=é#f", fetch_host="Sagar-का-Mac")
    put2(st, "bse", b"3", logical_key="c", final_url="https://x.test/p?token=abc", fetch_host="a b")
    ents = mk().entries("bse")
    assert ents[0].final_url == url and ents[0].fetch_host == host
    assert ents[1].final_url == "http://x.test/ü?q=é#f" and ents[1].fetch_host == "Sagar-का-Mac"
    assert ents[2].final_url == "https://x.test/p?token=abc"        # stored verbatim, no redaction
    o = json.loads(lines_of(manifest_path(man, "bse"))[1])
    assert o["final_url"] == "http://x.test/ü?q=é#f" and o["fetch_host"] == "Sagar-का-Mac"
    assert len(lines_of(manifest_path(man, "bse"))) == 3
    assert mk().verify() == []


def test_A34_real_browser_fragment_kept_in_final_url(mk, roots):
    raw, man = roots
    st = mk()
    e = put2(st, "bse", b"x", url="https://www.nseindia.com/p", access_mode="real_browser",
             request_headers=None, final_url="https://www.nseindia.com/p#section-2")
    assert e.final_url == "https://www.nseindia.com/p#section-2" and e.url == "https://www.nseindia.com/p"
    assert mk().entries("bse")[0].final_url == "https://www.nseindia.com/p#section-2"


@pytest.mark.planted
@pytest.mark.parametrize("over", [
    {"final_url": "ftp://x"}, {"final_url": "https://x/\u0001"}, {"final_url": "https://x/" + "a" * 2040},
    {"fetch_host": " mac"}, {"fetch_host": "a\tb"}, {"fetch_host": "h" * 256}, {"fetch_host": "mac "},
], ids=["ftp", "ctrl", "len", "lead_space", "tab", "len256", "trail_space"])
def test_A34_verify_flags_hand_written_r21_violation(mk, roots, over):
    st = _store_with_extra(mk, roots, v2_obj(b"mid", key="hand", **over))
    assert [(p.kind, p.line) for p in st.verify("bse")] == [("bad_line", 2)]


# ============================================================ edge cases and cross-cutting
def test_edge_two_sources_share_no_state(mk, roots):
    raw, man = roots
    st = mk()
    put2(st, "bse", b"a")
    write_lines(man, "nse", [canon(v1_obj(b"v1", source="nse"))], raw_root=raw, payloads=[b"v1"])
    assert st.get_by_key("nse", "2026-11-20").schema == 1
    assert st.get_by_key("bse", "2026-11-20").schema == 2
    write_lines(man, "nse", ["{bad json"])
    from investos.acquire.manifest import ManifestCorruptError
    with pytest.raises(ManifestCorruptError):
        st.entries("nse")
    assert st.get_by_key("bse", "2026-11-20").schema == 2 and st.verify("bse") == []


def test_edge_v2_one_physical_line_with_awkward_text(mk, roots):
    raw, man = roots
    st = mk()
    put2(st, "bse", b"x", url="https://x.test/a\"b", logical_key="k\"\n\\é",
         request_headers={"X-A": 'va"l\\ué ✓'}, final_url="https://x.test/a\"b", fetch_host="hé\"")
    assert len(lines_of(manifest_path(man, "bse"))) == 1
    e = mk().entries("bse")[0]
    assert e.logical_key == "k\"\n\\é" and e.request_headers["X-A"] == 'va"l\\ué ✓' and e.fetch_host == "hé\""


def test_edge_concurrent_threads_write_v2(mk, roots):
    import threading
    raw, man = roots
    st = mk()
    errs = []

    def run(w):
        try:
            for i in range(20):
                put2(st, "bse", f"t{w}-{i}".encode(), logical_key=f"k{w}-{i}", request_headers={"X": str(w)})
        except BaseException as e:      # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(w,)) for w in range(4)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert not errs and len(mk().entries("bse")) == 80 and mk().verify() == []


def test_A6_public_api_unchanged_by_v2():
    """test_A6 pin (test_manifest_write.py) stays as is: the spec adds no public module names and no
    ManifestStore methods (ManifestEntry gains fields; store() gains arguments)."""
    import investos.acquire.manifest as m
    own = {n for n in dir(m) if not n.startswith("_") and callable(getattr(m, n))
           and getattr(getattr(m, n), "__module__", None) == m.__name__}
    assert own == {"ManifestError", "ManifestCorruptError", "RawIntegrityError", "ManifestStore",
                   "ManifestEntry", "Problem"}
    assert {n for n in dir(m.ManifestStore) if not n.startswith("_")} == {
        "from_config", "store", "get_by_key", "get_all_by_key", "get_by_sha", "entries", "raw_path",
        "read_bytes", "verify"}
