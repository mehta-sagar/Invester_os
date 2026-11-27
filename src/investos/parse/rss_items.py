"""RSS item extractor (spec collect_rss R5, R6, R14-R16, R15a; brief S1-T6).

The only module that reads feed XML. Two pure steps: ``parse_feed`` (one body to items) and ``extract_items``
(stored bodies in manifest order to item rows, each item once at its first sighting). No I/O, no clock, no
network. stdlib only: ``xml.etree.ElementTree`` after a hard pre-check that refuses any DTD or entity
declaration, so nothing is ever expanded or resolved.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from investos.core import log

__all__ = ["FeedParseError", "ParsedItem", "ItemRow", "parse_feed", "extract_items", "MAX_BODY_BYTES"]

MAX_BODY_BYTES = 8 * 1024 * 1024
IST = ZoneInfo("Asia/Kolkata")
_UTC = timezone.utc
_DTD_RE = re.compile(rb"<!\s*(?:doctype|entity)", re.I)
_ENC_RE = re.compile(rb"^\s*<\?xml[^>]*encoding\s*=\s*[\"']\s*(utf-?(?:16|32)|ucs-?[24])", re.I)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]*>")
_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
_MAX_TITLE, _MAX_SUMMARY = 1000, 2000
_IN_HOSTS = {"nsearchives.nseindia.in": "nsearchives.nseindia.com", "www.nseindia.in": "www.nseindia.com"}
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_ZONE = r"(?P<zone>Z|GMT|UTC|UT|[+-]\d{2}:?\d{2})"
_TIME = r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.\d+)?)?"
_ISO_RE = re.compile(rf"^(?P<y>\d{{4}})-(?P<mo>\d{{2}})-(?P<d>\d{{2}})(?:[T ]{_TIME})?\s*{_ZONE}?$", re.I)
_TXT_RE = re.compile(
    rf"^(?:[A-Za-z]{{3,9}},?\s+)?(?P<d>\d{{1,2}})[-\s]+(?P<mon>[A-Za-z]{{3,9}}),?[-\s]+(?P<y>\d{{4}})"
    rf"(?:\s+{_TIME})?\s*{_ZONE}?$", re.I)


class FeedParseError(Exception):
    """The body is not a usable RSS 2.0 feed (or is refused before parsing)."""


@dataclass(frozen=True)
class ParsedItem:
    item_id: str
    guid: str | None
    link: str | None
    title: str
    pub_raw: str | None
    pub_ist: datetime | None
    pub_status: str                    # ok | date_only | missing | unparseable
    summary: str


@dataclass(frozen=True)
class ItemRow:
    feed_id: str
    item_id: str
    guid: str | None
    link: str | None
    title: str
    pub_raw: str | None
    pub_ist: datetime | None
    pub_status: str
    summary: str
    first_seen_at: datetime
    first_seen_sha256: str
    first_seen_key: str
    pub_after_seen: bool


# --------------------------------------------------------------------------- dates (R15, R15a)
def _parse_pub(text: str) -> tuple[datetime | None, str]:
    s = text.strip()
    m = _ISO_RE.match(s)
    if m is not None:
        y, mo, d = int(m["y"]), int(m["mo"]), int(m["d"])
    else:
        m = _TXT_RE.match(s)
        if m is None:
            return None, "unparseable"
        mo = _MONTHS.get(m["mon"][:3].lower(), 0)
        y, d = int(m["y"]), int(m["d"])
    try:
        if m["h"] is None:
            return datetime(y, mo, d, 23, 59, 59, tzinfo=IST), "date_only"
        hh, mi, ss = int(m["h"]), int(m["mi"]), int(m["s"] or 0)
        naive = datetime(y, mo, d, hh, mi, ss)
    except ValueError:
        return None, "unparseable"
    zone = m["zone"]
    if zone is None:
        return naive.replace(tzinfo=IST), "ok"                  # no zone: IST wall time, never UTC (trap T14)
    if zone.upper() in ("Z", "GMT", "UTC", "UT"):
        tz = _UTC
    else:
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    return naive.replace(tzinfo=tz).astimezone(IST), "ok"


# --------------------------------------------------------------------------- text helpers
def _clean(text: str | None, cap: int) -> str:
    if not text:
        return ""
    text = _CTRL_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()[:cap]


def _child_text(el: ET.Element, tag: str) -> str | None:
    c = el.find(tag)
    if c is None:
        return None
    return "".join(c.itertext())


def _canon_link(link: str) -> str:
    try:
        parts = urlsplit(link)
        host = (parts.hostname or "").lower()
    except ValueError:
        return link
    com = _IN_HOSTS.get(host)
    if com is None:
        return link
    netloc = com + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# --------------------------------------------------------------------------- parse_feed (R5, R6, R15, R16)
def _check_raw(body: bytes) -> None:
    if not isinstance(body, (bytes, bytearray)):
        raise FeedParseError("body is not bytes")
    if len(body) > MAX_BODY_BYTES:
        raise FeedParseError(f"body over {MAX_BODY_BYTES} bytes")
    if not body.strip():
        raise FeedParseError("empty body")
    if b"\x00" in body:
        raise FeedParseError("NUL byte in body")
    if _DTD_RE.search(body):
        raise FeedParseError("DOCTYPE or ENTITY declaration refused")
    if body[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff") or body[:2] in (b"\xff\xfe", b"\xfe\xff") \
            or _ENC_RE.match(body[:200]):
        raise FeedParseError("unsupported text encoding (only 8-bit encodings are read)")


def parse_feed(body: bytes) -> list[ParsedItem]:
    _check_raw(body)
    try:
        root = ET.fromstring(bytes(body))
    except ET.ParseError as e:
        raise FeedParseError(f"not well-formed XML: {e}") from None
    except (ValueError, LookupError) as e:                       # unknown encoding name and the like
        raise FeedParseError(f"unreadable XML: {e}") from None
    name = root.tag.rsplit("}", 1)[-1] if isinstance(root.tag, str) else str(root.tag)
    if root.tag != "rss":
        raise FeedParseError(f"unsupported feed format {name}")
    channel = root.find("channel")
    if channel is None:
        raise FeedParseError("rss without channel")
    items: list[ParsedItem] = []
    seen: set[str] = set()
    for el in channel.findall("item"):
        it = _parse_item(el)
        if it.item_id in seen:
            continue                                            # same id twice in one body: the first stays
        seen.add(it.item_id)
        items.append(it)
    return items


def _parse_item(el: ET.Element) -> ParsedItem:
    guid_raw = _child_text(el, "guid")
    guid = guid_raw.strip() if guid_raw and guid_raw.strip() else None
    link_raw = _child_text(el, "link")
    link = link_raw.strip() if link_raw and link_raw.strip() else None
    title = _clean(_child_text(el, "title"), _MAX_TITLE)
    desc = _child_text(el, "description")
    summary = _clean(_TAG_RE.sub(" ", desc), _MAX_SUMMARY) if desc else ""
    pub_text = _child_text(el, "pubDate")
    if pub_text is None or not pub_text.strip():
        pub_text = _child_text(el, _DC_DATE)
    pub_raw = pub_text.strip() if pub_text and pub_text.strip() else None
    if pub_raw is None:
        pub_ist, status = None, "missing"
    else:
        pub_ist, status = _parse_pub(pub_raw)
    if guid:
        item_id = guid
    elif link:
        item_id = _canon_link(link)
    else:
        item_id = "h:" + hashlib.sha256((title + "\n" + (pub_raw or "")).encode("utf-8")).hexdigest()
    return ParsedItem(item_id=item_id, guid=guid, link=link, title=title, pub_raw=pub_raw, pub_ist=pub_ist,
                      pub_status=status, summary=summary)


# --------------------------------------------------------------------------- extract_items (R14)
def extract_items(rows: Iterable[tuple[object, bytes]]) -> Iterator[ItemRow]:
    """Stored bodies in manifest order -> one ItemRow per (feed_id, item_id) at its first sighting."""
    logger = log.get_logger("parse.rss_items")
    seen: set[tuple[str, str]] = set()
    for entry, body in rows:
        key = entry.logical_key
        feed_id = key.split("/")[0]
        try:
            items = parse_feed(body)
        except FeedParseError as e:
            logger.warning("item_parse_failed", sha256=entry.sha256, key=key, reason=str(e)[:200])
            continue
        for it in items:
            ident = (feed_id, it.item_id)
            if ident in seen:
                continue
            seen.add(ident)
            seen_at = entry.fetched_at
            after = it.pub_ist is not None and it.pub_ist > seen_at + timedelta(minutes=5)
            yield ItemRow(feed_id=feed_id, item_id=it.item_id, guid=it.guid, link=it.link, title=it.title,
                          pub_raw=it.pub_raw, pub_ist=it.pub_ist, pub_status=it.pub_status, summary=it.summary,
                          first_seen_at=seen_at, first_seen_sha256=entry.sha256, first_seen_key=key,
                          pub_after_seen=after)
