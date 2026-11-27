"""Check execution, JSON report, and the planted-failure harness (spec R3-R9)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from investos.core.config import ZONE_KEYS, Config
from investos.core.log import get_logger

from .registry import CheckContext, CheckRegistry, CheckResult

MAX_MESSAGE = 200
MAX_DETAILS = 50
_log = get_logger("check")


@dataclass(frozen=True)
class Outcome:
    name: str
    status: str
    message: str
    details: tuple[str, ...]
    duration_ms: int
    plant: str                      # registered | missing
    phase: str | None = None        # planted mode only: baseline | plant | none

    def to_dict(self, planted: bool) -> dict:
        d = {"name": self.name, "status": self.status, "message": self.message,
             "details": list(self.details), "duration_ms": self.duration_ms, "plant": self.plant}
        if planted:
            d["phase"] = self.phase
        return d


@dataclass(frozen=True)
class Report:
    run_id: str
    mode: str                       # check | planted
    started_at: str
    finished_at: str
    selected: tuple[str, ...]
    results: tuple[Outcome, ...]

    @property
    def ok(self) -> bool:
        good = "caught" if self.mode == "planted" else "pass"
        return all(r.status == good for r in self.results)

    def to_dict(self) -> dict:
        planted = self.mode == "planted"
        return {"schema": 1, "run_id": self.run_id, "mode": self.mode,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "selected": list(self.selected), "ok": self.ok,
                "results": [r.to_dict(planted) for r in self.results]}


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _one_line(s, limit: int = MAX_MESSAGE) -> str:
    s = " ".join(str(s).replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return s[:limit]


def _cap_details(details) -> tuple[str, ...]:
    lines = [_one_line(d, 1000) for d in details]
    if len(lines) > MAX_DETAILS:
        extra = len(lines) - (MAX_DETAILS - 1)
        lines = lines[:MAX_DETAILS - 1] + [f"... and {extra} more"]
    return tuple(lines)


def _exc_text(e: BaseException) -> str:
    return _one_line(f"{type(e).__name__}: {e}")


def _call(fn, ctx: CheckContext) -> tuple[CheckResult | None, str | None]:
    """Run one check. Returns (result, None) or (None, error text)."""
    try:
        r = fn(ctx)
    except Exception as e:  # noqa: BLE001 - a raising check is an `error`, never a crash
        return None, _exc_text(e)
    if not isinstance(r, CheckResult):
        return None, _one_line(f"TypeError: check returned {type(r).__name__}, expected CheckResult")
    if not isinstance(r.ok, bool):
        return None, _one_line(f"TypeError: CheckResult.ok is {type(r.ok).__name__}, expected bool")
    return r, None


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def run_checks(registry: CheckRegistry, ctx: CheckContext, names: Sequence[str] | None = None, *,
               clock: Callable[[], datetime] | None = None,
               timer: Callable[[], float] | None = None) -> Report:
    clock = clock or _default_clock
    timer = timer or time.perf_counter
    selected = tuple(registry.names() if names is None else names)
    started = clock()
    results: list[Outcome] = []
    for name in selected:
        fn = registry.get(name)
        t0 = timer()
        r, err = _call(fn, ctx)
        ms = max(0, int((timer() - t0) * 1000))
        plant = "registered" if registry.plant_of(name) else "missing"
        if err is not None:
            results.append(Outcome(name, "error", err, (), ms, plant))
        else:
            results.append(Outcome(name, "pass" if r.ok else "fail", _one_line(r.message),
                                   _cap_details(r.details), ms, plant))
    return Report(ctx.run_id, "check", _fmt(started), _fmt(clock()), selected, tuple(results))


# ---- planted harness ---------------------------------------------------

def _digest(cfg: Config, run_id: str) -> dict[str, tuple]:
    """(mode, size, sha256) for every file/dir of all real zones and migrations, except this
    run's own report file (reports/checks/<run_id>.json)."""
    roots = [cfg.zones[k] for k in ZONE_KEYS] + [cfg.migrations_dir]
    own_report = os.path.join(str(cfg.zones["reports"]), "checks", f"{run_id}.json")
    out: dict[str, tuple] = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for n in [*dirnames, *filenames]:
                p = os.path.join(dirpath, n)
                if p == own_report:
                    continue
                try:
                    st = os.lstat(p)
                    h = None
                    if stat.S_ISREG(st.st_mode):
                        with open(p, "rb") as fh:
                            h = hashlib.file_digest(fh, "sha256").hexdigest()
                    out[p] = (st.st_mode, st.st_size, h)
                except OSError as e:
                    out[p] = ("unreadable", str(e), None)
        try:
            st = os.lstat(root)
            out[str(root)] = (st.st_mode, None, None)
        except OSError:
            out[str(root)] = ("missing", None, None)
    return out


def _make_writable(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for n in [*dirnames, *filenames]:
            p = os.path.join(dirpath, n)
            try:
                if not os.path.islink(p):
                    os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR
                             | ((stat.S_IRUSR | stat.S_IXUSR) if os.path.isdir(p) else 0))
            except OSError:
                pass
    try:
        os.chmod(root, os.stat(root).st_mode | stat.S_IRWXU)
    except OSError:
        pass


def _cleanup(tmp: Path) -> None:
    try:
        _make_writable(tmp)
        shutil.rmtree(tmp)
    except OSError as e:
        _log.warning("temp_cleanup_failed", path=str(tmp), error=str(e))


def _temp_copy(cfg: Config, tmp_parent: Path | None,
               needs: frozenset[str] | None = None) -> tuple[Path, Config]:
    """Copy the zones the check needs (all when `needs` is None); the others become empty directories."""
    tmp = Path(tempfile.mkdtemp(prefix="investos-planted-",
                                dir=str(tmp_parent) if tmp_parent is not None else None))
    try:
        zones = {}
        for k in ZONE_KEYS:
            zones[k] = tmp / k
            if needs is None or k in needs:
                shutil.copytree(cfg.zones[k], zones[k], symlinks=False)
            else:
                zones[k].mkdir()
        mig = tmp / "migrations"
        if needs is None or "migrations" in needs:
            shutil.copytree(cfg.migrations_dir, mig, symlinks=False)
        else:
            mig.mkdir()
    except BaseException:
        _cleanup(tmp)
        raise
    duck = dataclasses.replace(cfg.duckdb, temp_directory=zones["canonical"] / ".duckdb_tmp")
    return tmp, dataclasses.replace(cfg, zones=zones, migrations_dir=mig, duckdb=duck)


def _planted_one(registry: CheckRegistry, cfg: Config, name: str, run_id: str,
                 timer: Callable[[], float], tmp_parent: Path | None) -> Outcome:
    t0 = timer()

    def out(status, message, phase, details=()):
        return Outcome(name, status, _one_line(message), _cap_details(details),
                       max(0, int((timer() - t0) * 1000)), "registered", phase)

    setup, plant = registry.plant_of(name)
    fn = registry.get(name)
    try:
        tmp, tcfg = _temp_copy(cfg, tmp_parent, registry.plant_zones(name))
    except OSError as e:
        return out("error", f"cannot create temp copy: {_exc_text(e)}", "baseline")
    try:
        ctx = CheckContext(tcfg, run_id)
        if setup is not None:
            try:
                setup(ctx)
            except Exception as e:  # noqa: BLE001
                return out("error", f"setup failed: {_exc_text(e)}", "baseline")
        r, err = _call(fn, ctx)
        if err is not None:
            return out("baseline_failed", f"baseline error: {err}", "baseline")
        if not r.ok:
            return out("baseline_failed", f"baseline failed: {r.message}", "baseline", r.details)
        try:
            plant(ctx)
        except Exception as e:  # noqa: BLE001
            return out("error", f"plant failed: {_exc_text(e)}", "plant")
        r, err = _call(fn, ctx)
        if err is not None:
            return out("caught", f"error: {err}", "plant")
        if r.ok:
            return out("missed", "check still passed after the plant", "plant")
        return out("caught", r.message, "plant", r.details)
    finally:
        _cleanup(tmp)


def run_planted(registry: CheckRegistry, cfg: Config, names: Sequence[str] | None = None, *,
                clock: Callable[[], datetime] | None = None,
                timer: Callable[[], float] | None = None,
                tmp_parent: Path | None = None) -> Report:
    from investos.core.log import get_run_id
    clock = clock or _default_clock
    timer = timer or time.perf_counter
    selected = tuple(registry.names() if names is None else names)
    run_id = get_run_id()
    started = clock()
    before = _digest(cfg, run_id)
    results: list[Outcome] = []
    for name in selected:
        registry.get(name)
        if registry.plant_of(name) is None:
            results.append(Outcome(name, "refused", "no plant registered", (), 0, "missing", "none"))
            continue
        results.append(_planted_one(registry, cfg, name, run_id, timer, tmp_parent))
    after = _digest(cfg, run_id)
    if before != after:
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        det = _cap_details(changed)
        results = [Outcome(r.name, "error", "real data modified", det, r.duration_ms, r.plant, "plant")
                   for r in results]
    return Report(run_id, "planted", _fmt(started), _fmt(clock()), selected, tuple(results))


# ---- report ------------------------------------------------------------

def write_report(report: Report, cfg: Config) -> Path:
    """reports/checks/<run_id>.json, canonical JSON, atomic. Raises OSError if it cannot be written."""
    d = cfg.zones["reports"] / "checks"
    d.mkdir(exist_ok=True)
    dest = d / f"{report.run_id}.json"
    text = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{report.run_id}.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return dest
