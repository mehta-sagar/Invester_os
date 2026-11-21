"""S0-T6 acceptance tests A1, A2, A3 (hash vector), A22 for investos.registry.canon (rules R1, R2)."""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

import pytest

VECTOR_TEXT = (
    '{"payload":{"a":1},"prev_hash":"0000000000000000000000000000000000000000000000000000000000000000",'
    '"seq":1,"ts":"2026-01-01T00:00:00.000000Z"}'
)


def test_A1_canonical_json_stable_across_key_order():
    from investos.registry.canon import canonical_json
    a = {"b": 1, "a": {"y": 2, "x": [True, None, "é"]}}
    b = {"a": {"x": [True, None, "é"], "y": 2}, "b": 1}
    assert list(a) != list(b)
    assert canonical_json(a) == canonical_json(b) == '{"a":{"x":[true,null,"é"],"y":2},"b":1}'


def test_A1b_no_whitespace_and_non_ascii_literal():
    from investos.registry.canon import canonical_json
    s = canonical_json({"k": ["सिग्नल", "é"], "z": {"q": 1}})
    assert " " not in s and "\n" not in s
    assert "सिग्नल" in s and "\\u" not in s


def test_A1c_depth_of_nesting_is_order_independent():
    from investos.registry.canon import canonical_json
    assert canonical_json({"c": {"z": 1, "a": 2}, "a": {"y": {"b": 1, "a": 2}}}) == \
        '{"a":{"y":{"a":2,"b":1}},"c":{"a":2,"z":1}}'


def _deep(n):
    x = 1
    for _ in range(n):
        x = [x]
    return x


@pytest.mark.planted
@pytest.mark.parametrize("bad", [
    pytest.param(0.1, id="float"),
    pytest.param(1.0, id="float_1.0"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"),
    pytest.param(-float("inf"), id="neg_inf"),
    pytest.param((1, 2), id="tuple"),
    pytest.param({1, 2}, id="set"),
    pytest.param(Decimal("1"), id="decimal"),
    pytest.param(b"x", id="bytes"),
    pytest.param(datetime(2026, 1, 1), id="datetime"),
    pytest.param({1: "a"}, id="non_str_key"),
    pytest.param(2**53, id="int_2_53"),
    pytest.param(-(2**53), id="int_neg_2_53"),
    pytest.param("\ud800", id="lone_surrogate"),
    pytest.param(_deep(65), id="nesting_65"),
    pytest.param({"a": [{"b": 0.5}]}, id="nested_float"),
    pytest.param({"a": (1,)}, id="nested_tuple"),
])
def test_A2_forbidden_values_rejected(bad):
    from investos.registry.canon import CanonicalJsonError, canonical_json
    with pytest.raises(CanonicalJsonError):
        canonical_json(bad)


def test_A2b_canonical_json_error_is_value_error():
    from investos.registry.canon import CanonicalJsonError
    assert issubclass(CanonicalJsonError, ValueError)


def test_A2c_allowed_values_and_distinct_types():
    from investos.registry.canon import canonical_json
    assert canonical_json("0.1") == '"0.1"'
    assert canonical_json(2**53 - 1) == str(2**53 - 1)
    assert canonical_json(-(2**53 - 1)) == str(-(2**53 - 1))
    outs = {canonical_json(True), canonical_json(1), canonical_json("1")}
    assert outs == {"true", "1", '"1"'}
    assert len(outs) == 3
    assert canonical_json(None) == "null" and canonical_json([]) == "[]" and canonical_json({}) == "{}"


def test_A3_sha256_hex_matches_hashlib():
    from investos.registry.canon import sha256_hex
    for text in ("", "abc", "सिग्नल"):
        assert sha256_hex(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_A3b_published_vector_hash_input_text():
    from investos.registry.canon import canonical_json, sha256_hex
    obj = {"seq": 1, "ts": "2026-01-01T00:00:00.000000Z", "prev_hash": "0" * 64, "payload": {"a": 1}}
    text = canonical_json(obj)
    assert text == VECTOR_TEXT
    assert sha256_hex(text) == hashlib.sha256(VECTOR_TEXT.encode("utf-8")).hexdigest()


def test_A3c_any_changed_input_changes_hash():
    from investos.registry.canon import canonical_json, sha256_hex
    base = {"seq": 1, "ts": "2026-01-01T00:00:00.000000Z", "prev_hash": "0" * 64, "payload": {"a": 1}}
    h0 = sha256_hex(canonical_json(base))
    variants = [
        {**base, "seq": 2},
        {**base, "ts": "2026-01-01T00:00:00.000001Z"},
        {**base, "prev_hash": "1" + "0" * 63},
        {**base, "payload": {"a": 2}},
        {**base, "payload": {"a": "1"}},
    ]
    hashes = {sha256_hex(canonical_json(v)) for v in variants}
    assert h0 not in hashes and len(hashes) == len(variants)


def test_A22_devanagari_canonical_is_literal_utf8():
    from investos.registry.canon import canonical_json, sha256_hex
    s = canonical_json({"hypothesis": "सिग्नल"})
    assert s == '{"hypothesis":"सिग्नल"}'
    assert sha256_hex(s) == hashlib.sha256('{"hypothesis":"सिग्नल"}'.encode("utf-8")).hexdigest()
