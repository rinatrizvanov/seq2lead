"""A localhost HTTP server for the standalone ranking interface.

Standard library only. The server binds to a loopback address and refuses any
other, because this has no authentication, no TLS and no rate limiting, and is a
single-user local tool rather than a service.
"""

from __future__ import annotations

import json
import socket
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from seq2lead.web.api import (
    Session,
    browse,
    bundle_summary,
    compound_detail,
    depict,
    ranking_csv,
    ranking_payload,
    run_ranking,
)
from seq2lead.web.jobs import JobQueue, JobState
from seq2lead.web.ui import PAGE

DEFAULT_PORT = 8765
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
#: A pasted sequence is bounded so a stray paste cannot exhaust memory. Far above
#: any real protein, and the bundle's own refusal point still applies.
MAX_BODY_BYTES = 4 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "seq2lead"
    sys_version = ""

    def __init__(self, *args: Any, session: Session, queue: JobQueue, **kw: Any) -> None:
        self.session, self.queue = session, queue
        super().__init__(*args, **kw)

    # ------------------------------------------------------------- plumbing

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path.startswith("/api/depict"):
            return  # one line per thumbnail drowns the useful output
        print(f"  {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # No external resources are loaded, so lock the page down to itself.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError(f"request body is larger than {MAX_BODY_BYTES:,} bytes")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ------------------------------------------------------------- routing

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/bundle":
                self._json(bundle_summary(self.session))
            elif url.path == "/api/library":
                self._json(
                    browse(
                        self.session,
                        page=_page_arg(q, "page", 1, low=1, high=10**6),
                        page_size=_page_arg(q, "page_size", 24, low=1, high=96),
                        query=q.get("q", ""),
                        **_windows(q),
                    )
                )
            elif url.path == "/api/depict":
                self._send(
                    200,
                    depict(
                        self.session,
                        _page_arg(q, "row", 0, low=0, high=10**9),
                        width=_page_arg(q, "w", 260, low=80, high=900),
                        height=_page_arg(q, "h", 200, low=60, high=900),
                    ).encode("utf-8"),
                    "image/svg+xml",
                )
            elif url.path == "/api/compound":
                self._json(
                    compound_detail(self.session, _page_arg(q, "row", 0, low=0, high=10**9))
                )
            elif url.path.startswith("/api/job/"):
                self._job(url.path.rsplit("/", 1)[-1])
            else:
                self._error(404, f"no such path: {url.path}")
        except (FilterError, ValueError, KeyError, IndexError) as exc:
            self._error(400, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        try:
            if url.path == "/api/rank":
                self._start_ranking(self._body())
            elif url.path.endswith("/cancel") and url.path.startswith("/api/job/"):
                job = self.queue.get(url.path.split("/")[3])
                if job is None:
                    self._error(404, "no such job")
                    return
                job.cancel()
                self._json(job.to_dict())
            else:
                self._error(404, f"no such path: {url.path}")
        except ValueError as exc:
            self._error(400, str(exc))

    # --------------------------------------------------------------- work

    def _start_ranking(self, request: dict[str, Any]) -> None:
        if not str(request.get("sequence") or "").strip():
            self._error(400, "no sequence supplied")
            return
        session = self.session
        job = self.queue.submit(lambda j: run_ranking(session, j, request))
        self._json(job.to_dict(queue_position=self.queue.position(job.id)), code=202)

    def _job(self, token: str) -> None:
        want_csv = token.endswith(".csv")
        job_id = token[:-4] if want_csv else token
        job = self.queue.get(job_id)
        if job is None:
            self._error(404, "no such job")
            return
        if want_csv:
            if job.state is not JobState.DONE:
                self._error(409, f"job is {job.state.value}, not done")
                return
            body = ranking_csv(job.result)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header(
                "Content-Disposition", f'attachment; filename="seq2lead_ranking_{job_id}.csv"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        payload = job.to_dict(queue_position=self.queue.position(job_id))
        if job.state is JobState.DONE:
            payload["result"] = ranking_payload(job.result, self.session)
        self._json(payload)


class FilterError(ValueError):
    """A filter value cannot be used, and saying so beats filtering to nothing."""


def _number(value: str | None, name: str) -> float | None:
    """Parse one filter bound, refusing anything that cannot mean a measurement.

    Silence was the old failure here. `nan`, `inf` and `1e400` all parsed and
    then matched nothing, so the page came back empty with no indication that the
    filter, not the library, was the reason; and a typo like `abc` was discarded
    entirely, so the filter silently did not apply.
    """
    import math

    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FilterError(
            f"{name}={value!r} is not a number. Leave it empty to disable this bound."
        ) from exc
    if not math.isfinite(parsed):
        raise FilterError(
            f"{name}={value!r} is not a finite number. A non-finite bound matches "
            "nothing, which looks like an empty library rather than a bad filter."
        )
    if parsed < 0:
        raise FilterError(f"{name}={value!r} is negative; molecular weight and TPSA are not.")
    return parsed


def _windows(q: dict[str, str]) -> dict[str, float | None]:
    """Parse all four bounds and check each window is the right way round."""
    values = {
        name: _number(q.get(name), name) for name in ("min_mw", "max_mw", "min_tpsa", "max_tpsa")
    }
    for low, high, label in (
        ("min_mw", "max_mw", "molecular weight"),
        ("min_tpsa", "max_tpsa", "TPSA"),
    ):
        lo, hi = values[low], values[high]
        if lo is not None and hi is not None and lo > hi:
            raise FilterError(
                f"the {label} window is inverted: {low}={lo:g} is above {high}={hi:g}, "
                "so nothing can satisfy it. Swap them."
            )
    return values


def _page_arg(q: dict[str, str], name: str, default: int, *, low: int, high: int) -> int:
    raw = q.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise FilterError(f"{name}={raw!r} is not a whole number") from exc
    if not low <= value <= high:
        raise FilterError(f"{name}={value} is outside the allowed range {low}-{high}")
    return value


def serve(
    bundle_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    device: str | None = None,
    preload: bool = False,
) -> ThreadingHTTPServer:
    """Build and bind the server. Refuses any non-loopback host."""
    from seq2lead.inference.bundle import load_bundle

    if host not in LOOPBACK:
        raise ValueError(
            f"refusing to bind to {host!r}. This interface has no authentication, no "
            "TLS and no rate limiting, and is not a hosted service. Bind to "
            "127.0.0.1 and use an SSH tunnel if you need it from elsewhere."
        )
    session = Session.open(load_bundle(bundle_path), device)
    if preload:
        session.encoder.load()
    queue = JobQueue()
    handler = partial(_Handler, session=session, queue=queue)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.session = session  # type: ignore[attr-defined]
    httpd.queue = queue  # type: ignore[attr-defined]
    return httpd


def run(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    thread.join()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
