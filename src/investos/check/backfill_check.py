"""Check `backfill`: ledger, plan, manifest, probe, counters and the no-evasion grep, plus its planted failure
(spec collect_backfill R20). Read-only; the clock is injectable through the module-level ``_clock``."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from zoneinfo import ZoneInfo

from investos.acquire.access import AccessState
from investos.acquire.manifest import ManifestError, ManifestStore
from investos.backfill.collector import rows_of
from investos.backfill.endpoints import COLUMNS, EndpointTableError, load_endpoints, table_path
from investos.backfill.ledger import DONE_EVENTS, LedgerError, WindowKey, WindowLedger, ledger_dir
from investos.backfill.planner import read_plan, split, write_plan
from investos.backfill.probe import (PROBE_MAX_AGE_DAYS, ProbeError, ProbeResult, load_probe_file, probe_age_days,
                                     save_probe_results)
from investos.backfill.runner import make_plan, state_dir, survey
from investos.core.config import Config

from .registry import CheckContext, CheckResult, check, plant_for

IST = ZoneInfo("Asia/Kolkata")
_MAX_MSG = 190
_PKG = Path(__file__).resolve().parents[1]
FORBIDDEN = ("stealth", "webdriver", "fake_useragent", "AutomationControlled", "undetected", "patchright", "rebrowser",
             "camoufox", "nodriver", "curl_cffi", "ja3", "impersonat", "userAgent", "user_agent", "extra_http_headers",
             "set_extra_http_headers", "add_init_script", "ignore_default_args", "new_cdp_session",
             "evaluate_on_new_document", "proxy", "socks", "jitter", "import random", "random.", "uniform(", ".mouse",
             ".keyboard", ".click(", ".dblclick(", ".fill(", ".type(", ".press(", ".hover(", ".tap(", ".dispatch_event(",
             "captcha", "CAPTCHA", "apify", "scrapingbee", "brightdata")


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def source_files() -> list[Path]:
    """The files R16 covers: the backfill package and the browser transport."""
    return sorted((_PKG / "backfill").glob("*.py")) + [_PKG / "acquire" / "browser.py"]


# planted run: the one-endpoint table lives in the temp `reports` zone, whose path keys this map
_PLANT_TABLES: dict[Path, Path] = {}


def _table_path(cfg: Config) -> Path:
    return _PLANT_TABLES.get(Path(cfg.zones["reports"]), table_path(cfg.repo_root))


def _descendants(w: WindowKey) -> set[WindowKey]:
    out = {w}
    if w.days >= 2:
        for c in split(w):
            out |= _descendants(c)
    return out


def _is_evidence(k: WindowKey, done: list[WindowKey]) -> bool:
    """A done window inside a done window of the same lineage is audit evidence, not a live window."""
    return any(len(p.part) < len(k.part) and k.part.startswith(p.part) and p.start <= k.start and k.end <= p.end
               for p in done)


def _endpoint_findings(cfg: Config, ep, probes: dict, store: ManifestStore, now: datetime, fails: list[str],
                       warns: list[str]) -> None:
    eid = ep.endpoint_id
    ledger = WindowLedger(cfg, eid, clock=lambda: now)
    try:
        lines = ledger.lines()
        states = ledger.state()
    except LedgerError as e:
        fails.append(f"ledger_invalid {eid}: {e}")
        return
    try:
        plan = read_plan(cfg, eid) or []
    except ValueError as e:
        fails.append(f"ghost_window {eid}: {e}")
        plan = []
    try:
        entries = store.entries(ep.source)
    except (ManifestError, OSError) as e:
        fails.append(f"unrecorded_fetch {eid}: manifest unreadable: {e}")
        return
    mkeys = {e.logical_key: e for e in entries}
    probe = probes.get(eid)
    valid = probe is not None and probe.row_hash == ep.row_hash()

    allowed: set[WindowKey] = set()
    for w in plan:
        allowed |= _descendants(w)
    for k in states:
        if k not in allowed:
            fails.append(f"ghost_window {eid}: {k.logical_key()} is in the ledger but not in the plan")
    ledger_keys = {o["logical_key"] for o in lines if o["logical_key"]}
    done = {k: s for k, s in states.items() if s.event in DONE_EVENTS}
    for k, s in done.items():
        if s.logical_key not in mkeys:
            fails.append(f"unrecorded_fetch {eid}: {s.logical_key} is in the ledger but not in the manifest")
    for key in sorted(set(mkeys) - ledger_keys):
        fails.append(f"unrecorded_fetch {eid}: {key} is in the manifest but not in the ledger (run repairs it)")

    donel = list(done)
    live = sorted((k for k in donel if not _is_evidence(k, donel)), key=lambda k: (k.start, k.end))
    if probe is None or probe.mode != "per_company":
        reach = None
        for k in live:
            if reach is not None and k.start <= reach:
                fails.append(f"overlap {eid}: {k.logical_key()} starts inside an earlier live window")
            reach = k.end if reach is None else max(reach, k.end)
    for w in plan:
        s = states.get(w)
        if s is not None and s.event == "superseded":
            days = {w.start + timedelta(days=i) for i in range(w.days)}
            for k in live:
                if k.part.startswith(w.part) and w.start <= k.start and k.end <= w.end:
                    days -= {k.start + timedelta(days=i) for i in range(k.days)}
            if days:
                fails.append(f"gap {eid}: {w.logical_key()} was superseded but {len(days)} of its days are not covered")

    rows_path = probe.rows_path if probe is not None else ""
    checked = sorted((k for k in done if done[k].logical_key in mkeys), key=lambda k: done[k].logical_key)
    if checked:
        size = min(len(checked), max(5, ceil(len(checked) / 100)))
        for k in checked[::max(1, len(checked) // size)][:size]:
            s = done[k]
            try:
                data = store.read_bytes(ep.source, mkeys[s.logical_key].sha256)
            except (ManifestError, OSError) as e:
                fails.append(f"verify_failed {eid}: {s.logical_key}: stored bytes changed or unreadable: {e}")
                continue
            if mkeys[s.logical_key].sha256 != s.sha256:
                fails.append(f"verify_failed {eid}: {s.logical_key}: the ledger sha is not the manifest sha")
            try:
                n = len(rows_of(data, rows_path))
            except ValueError as e:
                fails.append(f"rows_mismatch {eid}: {s.logical_key}: {e}")
                continue
            if n != s.rows:
                fails.append(f"rows_mismatch {eid}: {s.logical_key}: ledger rows {s.rows}, stored rows {n}")
    if probe is not None and probe.row_cap:
        for k, s in done.items():
            if s.rows is not None and s.rows >= probe.row_cap:
                fails.append(f"cap_window_trusted {eid}: {s.logical_key} holds {s.rows} rows, the cap is {probe.row_cap}")
    if done and not (valid and probe.status == "confirmed"):
        fails.append(f"unprobed_fetch {eid}: windows were fetched but no confirmed probe matches the table row")
    elif valid and probe.status == "confirmed" and probe_age_days(probe, now) > PROBE_MAX_AGE_DAYS:
        try:
            pending = survey(make_plan(cfg, ep, probe, ledger, now.astimezone(IST).date(), write=False), ledger,
                             now.astimezone(IST).date().isoformat()).pending
        except ValueError:
            pending = []
        if pending:
            warns.append(f"WARNING stale_probe {eid}: probed over {PROBE_MAX_AGE_DAYS} days ago, "
                         f"{len(pending)} windows pending")
    recent = [o for o in lines if o["event"] == "blocked"
              and datetime.strptime(o["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) >= now - timedelta(days=7)]
    if recent:
        host = "no block active now"
        try:
            for h in ("www.nseindia.com", "www.nseindia.in"):
                st = AccessState(cfg, ep.source, clock=lambda: now).status(h)
                if st.blocked_today:
                    host = st.reason
        except Exception:
            host = "access state unreadable"
        warns.append(f"WARNING blocked {eid}: {len(recent)} blocked line(s) in the last 7 days; {host}")


@check("backfill")
def check_backfill(ctx: CheckContext) -> CheckResult:
    cfg = ctx.cfg
    now = _clock()
    table = _table_path(cfg)
    if not table.exists():
        return CheckResult(True, "no config/backfill_endpoints.csv; nothing to check",
                           ("WARNING config/backfill_endpoints.csv is missing, no backfill runs",))
    try:
        eps = load_endpoints(table)
    except EndpointTableError as e:
        return CheckResult(False, str(e)[:_MAX_MSG], (str(e),))
    fails: list[str] = []
    warns: list[str] = []
    try:
        probes = load_probe_file(cfg)
    except ProbeError as e:
        probes = {}
        fails.append(f"unprobed_fetch: {e}")
    store = ManifestStore.from_config(cfg)
    for ep in eps.values():
        _endpoint_findings(cfg, ep, probes, store, now, fails, warns)
    d = ledger_dir(cfg)
    for p in sorted(d.glob("*.windows.jsonl")) if d.is_dir() else []:
        name = p.name.removesuffix(".windows.jsonl")
        if name not in eps:
            fails.append(f"ghost_window {name}: a ledger exists for an endpoint that is not in the table")
    budget = max(cfg.backfill.ramp)
    sd = state_dir(cfg)
    for p in sorted(sd.glob("night.*.count")) if sd.is_dir() else []:
        try:
            n = int(p.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            fails.append(f"budget_exceeded: {p.name} is unreadable")
            continue
        if n > budget:
            fails.append(f"budget_exceeded: {p.name} holds {n} requests, the largest night budget is {budget}")
    for f in source_files():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            fails.append(f"stealth_string {f.name}: unreadable: {e}")
            continue
        for s in FORBIDDEN:
            if s in text:
                fails.append(f"stealth_string {f.name}: holds {s!r}")
    counts = f"{len(eps)} endpoints, {len(fails)} failures, {len(warns)} warnings"
    details = tuple(fails + warns)
    if fails:
        codes = sorted({f.split()[0] for f in fails})
        return CheckResult(False, f"{counts}: {', '.join(codes)}"[:_MAX_MSG], details)
    return CheckResult(True, counts, details)


# --------------------------------------------------------------------------- plant
def _plant_row(start: date) -> list[str]:
    row = dict.fromkeys(COLUMNS, "")
    row.update(endpoint_id="plant_ep", page_url="/plant", anchor_url="/plant",
               api_path="/api/plant?from_date={from}&to_date={to}", params="from_date={from}&to_date={to}",
               date_format="DD-MM-YYYY", max_window_days="1", date_field="an_dt", start_date=start.isoformat(),
               known_at_rule="end of IST day", status="to_probe")
    return [row[c] for c in COLUMNS]


def _setup(ctx: CheckContext) -> None:
    """A self-contained healthy copy: one endpoint, a confirmed probe, three stored and ledgered windows."""
    cfg, now = ctx.cfg, _clock()
    today = now.astimezone(IST).date()
    start = today - timedelta(days=3)
    reports = Path(cfg.zones["reports"])
    d = reports / "backfill_plant"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "backfill_endpoints.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLUMNS)
        w.writerow(_plant_row(start))
    _PLANT_TABLES[reports] = d / "backfill_endpoints.csv"
    shutil.rmtree(ledger_dir(cfg), ignore_errors=True)
    shutil.rmtree(state_dir(cfg), ignore_errors=True)
    ep = load_endpoints(_PLANT_TABLES[reports])["plant_ep"]
    probe = ProbeResult(
        endpoint_id="plant_ep", row_hash=ep.row_hash(), probed_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        api_origin="https://www.nseindia.in", rows_path="", date_field="an_dt", date_format="DD-MM-YYYY",
        to_inclusive=True, row_cap=None, max_safe_rows=10, max_window_days=1, earliest_date=start, time_of_day="none",
        undercount=False, mode="", status="confirmed", reasons=(), rows_per_day=2.0)
    save_probe_results(cfg, [probe])
    store = ManifestStore.from_config(cfg)
    ledger = WindowLedger(cfg, "plant_ep", clock=_clock)
    for i in range(3):
        day = start + timedelta(days=i)
        key = WindowKey("plant_ep", day, day)
        body = json.dumps([{"symbol": f"S{j}", "an_dt": day.strftime("%d-%b-%Y")} for j in range(2)]).encode()
        url = f"https://www.nseindia.in/api/plant?from_date={day.strftime('%d-%m-%Y')}&to_date={day.strftime('%d-%m-%Y')}"
        e = store.store("backfill_plant_ep", body, url=url, source_version="backfill-v1", logical_key=key.logical_key(),
                        run_id="plant", fetched_at=now, access_mode="real_browser", request_headers=None,
                        final_url=url, fetch_host="plant")
        ledger.append("fetched", key, rows=2, sha256=e.sha256, logical_key=key.logical_key(), run_id="plant",
                      probe_hash=ep.row_hash())
    write_plan(cfg, ep, probe, today)


@plant_for("backfill", setup=_setup, zones={"reports", "ledger", "raw", "manifests"})
def _plant(ctx: CheckContext) -> None:
    """A ghost window: the ledger names a day long before the plan starts."""
    WindowLedger(ctx.cfg, "plant_ep", clock=_clock).append("planned", WindowKey("plant_ep", date(2005, 1, 3),
                                                                                date(2005, 1, 3)))
