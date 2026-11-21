"""Shared fakes/fixtures for S0-T4 (collector base) tests.

Module under test: investos.acquire.base (spec says investos.collect.base; decision 0001 maps it).
investos imports happen inside functions only, so each test fails individually while the module is absent.
Test files pull the fixtures in with `from _collector_helpers import env, logs  # noqa: F401`.
"""
from __future__ import annotations

import hashlib
import io
import json
import stat
import warnings
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

SOURCE = "demo_src"
SOURCE_VERSION = "demo-v1"
NOW = datetime(2026, 11, 21, 9, 30, 0, tzinfo=timezone.utc)
CSV = b"date,close\n2026-11-17,100\n"
CSV_HEADER_ONLY = b"date,close\n"
D17, D18, D19 = "2026-11-17", "2026-11-18", "2026-11-19"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def url_of(key: str, host: str = "example.test") -> str:
    return f"https://{host}/f/{key}.csv"


def rr(a: str = D17, b: str | None = None):
    from investos.acquire.base import RunRange
    return RunRange(date.fromisoformat(a), date.fromisoformat(b or a))


def resp(status: int = 200, body: bytes = CSV, headers=None, url: str = ""):
    from investos.acquire.base import Response
    return Response(status=status, headers=headers or {}, body=body, url=url)


def terr(msg: str = "timeout"):
    from investos.acquire.base import TransportError
    return TransportError(msg)


# ---------------------------------------------------------------- fakes
class FakeSleeper:
    def __init__(self, mono: "FakeMono"):
        self.sleeps: list[float] = []
        self._mono = mono

    def __call__(self, secs: float) -> None:
        self.sleeps.append(secs)
        self._mono.t += secs


class FakeMono:
    """Monotonic clock that advances only when the sleeper is called."""
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeTransport:
    """url -> scripted list of Response | BaseException. Last item repeats forever."""
    def __init__(self, mono: FakeMono):
        self.script: dict[str, list] = {}
        self.calls: list[dict] = []
        self._mono = mono

    def set(self, url: str, *items) -> None:
        self.script[url] = list(items)

    def get(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout, "t": self._mono.t})
        if url not in self.script:
            raise AssertionError(f"unscripted request {url}")
        items = self.script[url]
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def n(self, url: str | None = None) -> int:
        return len(self.calls if url is None else [c for c in self.calls if c["url"] == url])


def default_check(self, key, r):
    from investos.acquire.base import BodyVerdict, Outcome
    b = r.body
    if b == b"NO FILE":
        return BodyVerdict(Outcome.MISSING_HOLIDAY, "no file for key")
    if b.startswith(b"date,"):
        if len(b.splitlines()) > 1:
            return BodyVerdict(Outcome.FETCHED)
        return BodyVerdict(Outcome.FETCHED_EMPTY, "header only")
    return BodyVerdict(Outcome.UNREADABLE, "check failed: unrecognised body")


def demo_class(**attrs):
    """DemoCollector subclass. attrs become class attributes. Special attrs:
    fixed_keys (list) overrides keys(); url_fn(key)->str; verdict_fn(self,key,resp)->verdict."""
    from investos.acquire.base import Collector

    def keys(self, run_range):
        if getattr(self, "fixed_keys", None) is not None:
            return list(self.fixed_keys)
        out, d = [], run_range.start
        from datetime import timedelta
        while d <= run_range.end:
            out.append(d.isoformat())
            d += timedelta(days=1)
        return out

    def url(self, key):
        fn = getattr(self, "url_fn", None)
        return fn(key) if fn else url_of(key)

    def check_body(self, key, r):
        fn = getattr(self, "verdict_fn", None)
        return fn(self, key, r) if fn else default_check(self, key, r)

    ns = {"source": SOURCE, "source_version": SOURCE_VERSION, "keys": keys, "url": url, "check_body": check_body}
    for k, v in attrs.items():
        ns[k] = staticmethod(v) if k == "url_fn" else v
    return type("DemoCollector", (Collector,), ns)


class Env:
    def __init__(self, tmp: Path, hosts=None, interval: float = 0.35):
        from investos.acquire.manifest import ManifestStore
        from investos.core.config import Config, RateLimits
        self.tmp = tmp
        self.raw, self.man = tmp / "raw", tmp / "manifests"
        self.raw.mkdir()
        self.man.mkdir()
        zones = {k: tmp / k for k in ("raw", "manifests", "canonical", "ledger", "reports", "registry")}
        self.cfg = Config(repo_root=tmp, timezone="Asia/Kolkata", zones=zones, migrations_dir=tmp / "mig",
                          rate_limits=RateLimits(default_interval_s=interval, hosts=hosts or {}))
        self.store = ManifestStore(self.raw, self.man)
        self.mono = FakeMono()
        self.sleeper = FakeSleeper(self.mono)
        self.transport = FakeTransport(self.mono)
        self.clock = lambda: NOW
        self.sidecar_dir = self.man / f"{SOURCE}.runs"

    def fresh_transport(self):
        self.transport = FakeTransport(self.mono)
        self.sleeper.sleeps.clear()
        return self.transport

    def make(self, cls=None, *, run_id="run-1", **kw):
        cls = cls or demo_class()
        return cls(transport=self.transport, manifest=kw.pop("manifest", self.store), cfg=self.cfg,
                   run_id=run_id, clock=self.clock, monotonic=self.mono, sleeper=self.sleeper, **kw)

    def script_ok(self, *keys, body=CSV):
        for k in keys:
            self.transport.set(url_of(k), resp(200, body))

    # ---- inspection
    def manifest_lines(self) -> list[str]:
        p = self.man / f"{SOURCE}.jsonl"
        return p.read_text().splitlines() if p.exists() else []

    def raw_files(self) -> list[Path]:
        return sorted(p for p in self.raw.rglob("*") if p.is_file())

    def sidecar(self, result) -> dict:
        return json.loads(Path(result.sidecar_path).read_text())

    def gaps(self, result) -> dict:
        return {g["key"]: g for g in self.sidecar(result)["gaps"]}


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def logs():
    """Capture structured log output. Returns callable -> list of parsed records."""
    from investos.core import log
    saved = dict(log._state)
    buf = io.StringIO()
    log.configure_logging(buf, level="DEBUG", run_id="log-run-0001")

    def records():
        return [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
    yield records
    log._state.update(saved)


def events(records, name=None, level=None):
    return [r for r in records if (name is None or r["event"] == name) and (level is None or r["level"] == level)]


# ---------------------------------------------------------------- zip builders
def make_zip(members: dict[str, bytes] | list[tuple[str, bytes]], *, compress=True) -> bytes:
    items = list(members.items()) if isinstance(members, dict) else members
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED) as z:
            for name, data in items:
                z.writestr(name, data)
    return buf.getvalue()


def make_symlink_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        zi = zipfile.ZipInfo("link.txt")
        zi.create_system = 3
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(zi, "/etc/passwd")
    return buf.getvalue()


def make_encrypted_flag_zip() -> bytes:
    """A zip whose single member has general-purpose bit 0 (encrypted) set in local+central headers."""
    b = bytearray(make_zip({"secret.csv": b"date,x\n1,2\n"}, compress=False))
    i = b.find(b"PK\x03\x04")
    b[i + 6] |= 0x01
    j = b.find(b"PK\x01\x02")
    b[j + 8] |= 0x01
    return bytes(b)
