"""Content-addressed raw store plus append-only JSONL manifest (spec: specs/core_manifest.md).

Raw files live at <raw_root>/<source>/<sha[:2]>/<sha> (mode 0444, never rewritten);
one manifest line per store() call goes to <manifests_root>/<source>.jsonl.
POSIX only (fcntl.flock).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,63}", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", re.ASCII)
_FIELDS = ("sha256", "size", "url", "fetched_at", "source", "source_version", "logical_key", "run_id")
_STR_FIELDS = ("url", "source", "source_version", "logical_key", "run_id")
_MAX_KEY = 512


class ManifestError(Exception):
    pass


class ManifestCorruptError(ManifestError):
    pass


class RawIntegrityError(ManifestError):
    pass


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(s: str) -> datetime:
    if not _TS_RE.fullmatch(s):
        raise ValueError(f"bad fetched_at {s!r}")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ManifestEntry:
    sha256: str
    size: int
    url: str
    fetched_at: datetime
    source: str
    source_version: str
    logical_key: str
    run_id: str

    def to_json_line(self) -> str:
        obj = {
            "sha256": self.sha256, "size": self.size, "url": self.url,
            "fetched_at": _fmt_ts(self.fetched_at), "source": self.source,
            "source_version": self.source_version, "logical_key": self.logical_key,
            "run_id": self.run_id,
        }
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Problem:
    kind: str
    source: str
    path: str
    line: int | None
    detail: str


def _check_source(source) -> str:
    if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"invalid source name {source!r}")
    return source


def _check_sha(sha) -> str:
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise ValueError(f"invalid sha256 {sha!r}")
    return sha


def _entry_from_line(raw: bytes) -> ManifestEntry:
    """Strictly parse one manifest line (no newline). Raises ValueError with a reason."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"not UTF-8: {e}") from None
    if not text.strip():
        raise ValueError("blank line")
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise ValueError(f"invalid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object")
    missing = [k for k in _FIELDS if k not in obj]
    extra = [k for k in obj if k not in _FIELDS]
    if missing:
        raise ValueError(f"missing field(s) {missing}")
    if extra:
        raise ValueError(f"unexpected field(s) {extra}")
    size = obj["size"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("size must be a non-negative int")
    for k in _STR_FIELDS + ("sha256", "fetched_at"):
        if not isinstance(obj[k], str):
            raise ValueError(f"{k} must be a string")
    if not _SHA_RE.fullmatch(obj["sha256"]):
        raise ValueError("sha256 must be 64 lowercase hex")
    ts = _parse_ts(obj["fetched_at"])
    return ManifestEntry(obj["sha256"], size, obj["url"], ts, obj["source"],
                         obj["source_version"], obj["logical_key"], obj["run_id"])


def _split_lines(data: bytes) -> list[tuple[int, bytes, bool]]:
    """(1-based line no, content without newline, terminated?) for every physical line."""
    if not data:
        return []
    parts = data.split(b"\n")
    out = [(i + 1, p, True) for i, p in enumerate(parts[:-1])]
    if parts[-1] != b"":
        out.append((len(parts), parts[-1], False))
    return out


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ManifestStore:
    def __init__(self, raw_root: Path, manifests_root: Path, *,
                 clock: Callable[[], datetime] | None = None):
        self._raw_root = Path(raw_root)
        self._man_root = Path(manifests_root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_config(cls, cfg) -> "ManifestStore":
        return cls(cfg.zones["raw"], cfg.zones["manifests"])

    # ---- paths -------------------------------------------------------
    def _manifest_path(self, source: str) -> Path:
        return self._man_root / f"{source}.jsonl"

    def raw_path(self, source: str, sha256: str) -> Path:
        _check_source(source)
        _check_sha(sha256)
        return self._raw_root / source / sha256[:2] / sha256

    # ---- write -------------------------------------------------------
    def store(self, source: str, data: bytes, *, url: str, source_version: str,
              logical_key: str, run_id: str, fetched_at: datetime) -> ManifestEntry:
        _check_source(source)
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("data must be bytes")
        data = bytes(data)
        for name, val in (("url", url), ("source_version", source_version),
                          ("logical_key", logical_key), ("run_id", run_id)):
            if not isinstance(val, str) or not val:
                raise ValueError(f"{name} must be a non-empty str")
        if len(logical_key) > _MAX_KEY:
            raise ValueError(f"logical_key longer than {_MAX_KEY} characters")
        if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None \
                or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be a timezone-aware datetime")
        for root in (self._raw_root, self._man_root):
            if not root.is_dir():
                raise ManifestError(f"root directory missing: {root}")

        fetched_utc = fetched_at.astimezone(timezone.utc)
        sha = hashlib.sha256(data).hexdigest()
        entry = ManifestEntry(sha, len(data), url, fetched_utc, source, source_version,
                              logical_key, run_id)
        line = (entry.to_json_line() + "\n").encode("utf-8")

        self._write_raw(source, sha, data)

        mpath = self._manifest_path(source)
        lock_fd = os.open(self._man_root / f"{source}.jsonl.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            fd = os.open(mpath, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                size = os.fstat(fd).st_size
                if size > 0:
                    last = os.pread(fd, 1, size - 1)
                    if last != b"\n":
                        raise ManifestCorruptError(
                            f"{mpath.name}: last line is not newline-terminated (truncated?)")
                view = memoryview(line)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        return entry

    def _write_raw(self, source: str, sha: str, data: bytes) -> None:
        d = self._raw_root / source / sha[:2]
        dest = d / sha
        if os.path.lexists(dest):
            self._require_same(dest, sha)
            return
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".tmp-{uuid.uuid4().hex}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(tmp, 0o444)
            try:
                os.link(tmp, dest)
            except FileExistsError:
                self._require_same(dest, sha)
                return
            finally:
                pass
            _fsync_dir(d)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    @staticmethod
    def _require_same(dest: Path, sha: str) -> None:
        if _hash_file(dest) != sha:
            raise RawIntegrityError(f"existing raw file {dest} does not match its name")

    # ---- read --------------------------------------------------------
    def read_bytes(self, source: str, sha256: str) -> bytes:
        p = self.raw_path(source, sha256)
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RawIntegrityError(f"raw file {p} does not match its name")
        return data

    def _load(self, source: str) -> list[ManifestEntry]:
        _check_source(source)
        mpath = self._manifest_path(source)
        try:
            data = mpath.read_bytes()
        except FileNotFoundError:
            return []
        out = []
        for no, raw, terminated in _split_lines(data):
            try:
                if not terminated:
                    raise ValueError("last line not newline-terminated (truncated?)")
                out.append(_entry_from_line(raw))
            except ValueError as e:
                raise ManifestCorruptError(f"{mpath.name} line {no}: {e}") from None
        return out

    def entries(self, source: str) -> list[ManifestEntry]:
        return self._load(source)

    def get_all_by_key(self, source: str, logical_key: str) -> list[ManifestEntry]:
        return [e for e in self._load(source) if e.logical_key == logical_key]

    def get_by_key(self, source: str, logical_key: str) -> ManifestEntry | None:
        m = self.get_all_by_key(source, logical_key)
        return m[-1] if m else None

    def get_by_sha(self, source: str, sha256: str) -> list[ManifestEntry]:
        _check_source(source)
        _check_sha(sha256)
        return [e for e in self._load(source) if e.sha256 == sha256]

    # ---- verify ------------------------------------------------------
    def verify(self, source: str | None = None) -> list[Problem]:
        if source is not None:
            return self._verify_source(_check_source(source))
        names: set[str] = set()
        if self._man_root.is_dir():
            for p in self._man_root.iterdir():
                if p.name.endswith(".jsonl") and _SOURCE_RE.fullmatch(p.name[:-6]):
                    names.add(p.name[:-6])
        if self._raw_root.is_dir():
            for p in self._raw_root.iterdir():
                if p.is_dir() and _SOURCE_RE.fullmatch(p.name):
                    names.add(p.name)
        out: list[Problem] = []
        for n in sorted(names):
            out.extend(self._verify_source(n))
        return out

    def _verify_source(self, source: str) -> list[Problem]:
        problems: list[Problem] = []
        seen: set[tuple] = set()

        def add(kind, path, line, detail):
            key = (kind, str(path), line)
            if key not in seen:
                seen.add(key)
                problems.append(Problem(kind, source, str(path), line, detail))

        mpath = self._manifest_path(source)
        try:
            data = mpath.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            data = b""
        except OSError as e:
            add("bad_line", mpath, None, f"unreadable manifest: {e}")
            data = b""
        for no, raw, terminated in _split_lines(data):
            try:
                if not terminated:
                    raise ValueError("last line not newline-terminated (truncated?)")
                e = _entry_from_line(raw)
            except ValueError as ex:
                add("bad_line", mpath, no, str(ex))
                continue
            rp = self._raw_root / source / e.sha256[:2] / e.sha256
            try:
                st = os.lstat(rp)
            except OSError:
                add("missing_file", rp, no, "raw file absent")
                continue
            if stat.S_ISREG(st.st_mode) and st.st_size != e.size:
                add("size_mismatch", rp, no, f"file size {st.st_size} != manifest size {e.size}")

        base = self._raw_root / source
        if base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames.sort()
                for fn in sorted(filenames):
                    if fn.startswith(".tmp-"):
                        continue
                    p = Path(dirpath) / fn
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    is_hex = bool(_SHA_RE.fullmatch(fn))
                    in_place = is_hex and Path(dirpath) == base / fn[:2]
                    if not in_place:
                        add("misplaced_file", p, None, "not at <source>/<sha[:2]>/<sha>")
                    if is_hex:
                        try:
                            if _hash_file(p) != fn:
                                add("hash_mismatch", p, None, "content hash differs from file name")
                        except OSError as ex:
                            add("hash_mismatch", p, None, f"unreadable: {ex}")
                    if st.st_mode & 0o222:
                        add("writable_file", p, None, f"mode {stat.S_IMODE(st.st_mode):04o}")
        return problems
