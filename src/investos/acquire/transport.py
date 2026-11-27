"""Production HTTP transport (spec collect_base R21). Standard library only.

Built on ``http.client`` (the layer under urllib) so that exactly the headers the caller gives are sent: the
only additions are the protocol-mandatory ``Host`` and ``Connection: close``. No redirect is followed, environment
proxy settings are never read, and the decoded body is capped.
"""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import zlib
from typing import Mapping
from urllib.parse import urlsplit

from investos.acquire.base import Response, TransportError

__all__ = ["UrllibTransport"]

_CHUNK = 64 * 1024
_BR_CHUNK = 4 * 1024
_DEFAULT_MAX = 536870912


class UrllibTransport:
    # the Collector refuses a placeholder contact (".invalid") when the transport is a real network one
    real_network = True

    def __init__(self, *, max_body_bytes: int = _DEFAULT_MAX, allowed_schemes: tuple[str, ...] = ("https",),
                 brotli_module=None) -> None:
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or max_body_bytes < 1:
            raise ValueError("max_body_bytes must be an int >= 1")
        self._max = max_body_bytes
        self._schemes = tuple(s.lower() for s in allowed_schemes)
        if brotli_module is None:
            try:
                import brotli as brotli_module                  # optional; absent means br is never advertised
            except ImportError:
                brotli_module = None
        self._br = brotli_module
        self.accept_encoding = "gzip, deflate, br" if self._br is not None else "gzip, deflate"

    # ------------------------------------------------------------------ request
    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> Response:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in self._schemes:
            raise ValueError(f"URL scheme {parts.scheme!r} is not allowed")
        host = parts.hostname
        if not host:
            raise ValueError(f"URL has no host: {url!r}")
        try:
            port = parts.port
        except ValueError:
            raise ValueError(f"URL has an invalid port: {url!r}") from None
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        deadline = time.monotonic() + 4 * timeout
        if scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        timer: threading.Timer | None = None
        expired = threading.Event()
        try:
            try:
                conn.connect()

                def _expire(sock=conn.sock) -> None:            # whole-call deadline, also while headers drip in
                    expired.set()
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                timer = threading.Timer(max(4 * timeout, 0.001), _expire)
                timer.daemon = True
                timer.start()
                conn.putrequest("GET", target, skip_host=False, skip_accept_encoding=True)
                for name, value in headers.items():
                    conn.putheader(name, value)
                conn.putheader("Connection", "close")
                conn.endheaders()
                r = conn.getresponse()
                raw = self._read_body(r, deadline)
                status = r.status
                hdrs = self._headers(r.getheaders())
            except (OSError, http.client.HTTPException, UnicodeError, socket.timeout) as e:
                if expired.is_set():
                    raise TransportError("timeout: whole request exceeded 4 x timeout") from None
                raise TransportError(f"{type(e).__name__}: {e}") from None
        finally:
            if timer is not None:
                timer.cancel()
            conn.close()
        if expired.is_set():
            raise TransportError("timeout: whole request exceeded 4 x timeout")
        cl = hdrs.get("content-length")
        if cl is not None and cl.strip().isdigit() and int(cl) != len(raw):
            raise TransportError(f"incomplete read: got {len(raw)} of {cl} bytes")
        body, hdrs = self._decode(raw, hdrs)
        return Response(status=status, headers=hdrs, body=body, url=url)

    # ------------------------------------------------------------------ helpers
    def _read_body(self, r: http.client.HTTPResponse, deadline: float) -> bytes:
        buf = bytearray()
        while True:
            if time.monotonic() > deadline:
                raise TransportError("timeout: whole request exceeded 4 x timeout")
            chunk = r.read1(_CHUNK)
            if not chunk:
                return bytes(buf)
            buf += chunk
            if len(buf) > self._max:
                raise TransportError("body too large")

    @staticmethod
    def _headers(pairs) -> dict[str, str]:
        out: dict[str, list[str]] = {}
        for k, v in pairs:
            out.setdefault(k.lower(), []).append(v)
        return {k: ", ".join(v) for k, v in out.items()}

    def _decode(self, raw: bytes, hdrs: dict[str, str]) -> tuple[bytes, dict[str, str]]:
        enc = hdrs.get("content-encoding", "").strip().lower()
        if enc in ("gzip", "x-gzip"):
            body = self._inflate(raw, 16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            wrapped = len(raw) >= 2 and (raw[0] & 0x0F) == 8 and ((raw[0] << 8) | raw[1]) % 31 == 0
            body = self._inflate(raw, zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
        elif enc == "br" and self._br is not None:
            body = self._brotli(raw)
        else:
            return raw, hdrs                                    # identity, or an encoding we cannot decode (R22)
        hdrs = {k: v for k, v in hdrs.items() if k not in ("content-encoding", "content-length")}
        return body, hdrs

    def _inflate(self, raw: bytes, wbits: int) -> bytes:
        out = bytearray()
        data = raw
        while data:
            d = zlib.decompressobj(wbits)
            try:
                out += d.decompress(data, self._max + 1 - len(out))
            except zlib.error as e:
                raise TransportError(f"cannot decode body: {e}") from None
            if len(out) > self._max:
                raise TransportError("body too large")
            if not d.eof:
                raise TransportError("cannot decode body: truncated stream")
            data = d.unused_data                                # further gzip members
        return bytes(out)

    def _brotli(self, raw: bytes) -> bytes:
        try:
            dec = self._br.Decompressor()
            out = bytearray()
            for i in range(0, len(raw), _BR_CHUNK):
                out += dec.process(raw[i:i + _BR_CHUNK])
                if len(out) > self._max:
                    raise TransportError("body too large")
            fin = getattr(dec, "is_finished", None)
            if fin is not None and not fin():
                raise TransportError("cannot decode body: truncated stream")
            return bytes(out)
        except TransportError:
            raise
        except Exception as e:
            raise TransportError(f"cannot decode body: {e}") from None
