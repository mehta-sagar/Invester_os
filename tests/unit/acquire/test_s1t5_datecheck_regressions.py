"""S1-T5 review regressions (reports/S1-T5/review.md D2): R41 key validation also applies to waived adapters."""
from __future__ import annotations

from datetime import date

import pytest

from _collector_helpers import env, rr  # noqa: F401
from _datecheck_helpers import FRI, waived_class


@pytest.mark.parametrize("bad", ["20261120", "2026-W47-5", "2026-11-20T00:00", "abc"])
def test_D2_waived_adapter_bad_key_fails_before_http(env, bad):
    cls = waived_class(fixed_keys=[FRI, bad])
    with pytest.raises(ValueError):
        env.make(cls).run(rr(FRI))
    assert env.transport.n() == 0
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.iterdir()) == []


def test_D2_waived_adapter_two_keys_naming_one_date_are_refused(env):
    def ld(self, k):
        return date.fromisoformat(k) if "-" in k else date(int(k[:4]), int(k[4:6]), int(k[6:]))
    cls = waived_class(fixed_keys=["20261120", FRI], logical_date=ld)
    with pytest.raises(ValueError):
        env.make(cls).run(rr(FRI))
    assert env.transport.n() == 0
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.iterdir()) == []
