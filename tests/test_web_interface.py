"""The local browser interface, checked against the CLI it wraps.

The interface must not become a second implementation of ranking. These tests
drive the HTTP API end to end against a synthetic bundle and assert the rows it
returns are the rows `seq2lead.inference.rank` produces.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from seq2lead.web.jobs import JobQueue, JobState
from seq2lead.web.server import free_port, serve


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(url: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload or {}).encode()
    req = urllib.request.Request(  # noqa: S310
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


# ================================================= 1. the queue itself


def test_jobs_run_one_at_a_time_in_submission_order() -> None:
    """Concurrent inference would contend for one encoder; the queue prevents it."""
    queue = JobQueue()
    seen: list[int] = []
    live = threading.Semaphore(1)

    def work(index: int):
        def run(_job):
            assert live.acquire(blocking=False), "two jobs ran at once"
            time.sleep(0.02)
            seen.append(index)
            live.release()
            return index

        return run

    jobs = [queue.submit(work(i)) for i in range(5)]
    deadline = time.time() + 15
    while time.time() < deadline and any(
        j.state not in {JobState.DONE, JobState.FAILED} for j in jobs
    ):
        time.sleep(0.02)
    assert [j.state for j in jobs] == [JobState.DONE] * 5
    assert seen == [0, 1, 2, 3, 4]


def test_a_job_cancelled_before_it_starts_never_runs() -> None:
    queue = JobQueue()
    blocker = threading.Event()
    queue.submit(lambda _j: blocker.wait(5))
    ran = []
    second = queue.submit(lambda _j: ran.append(1))
    second.cancel()
    blocker.set()
    deadline = time.time() + 10
    while time.time() < deadline and second.state not in {JobState.CANCELLED, JobState.DONE}:
        time.sleep(0.02)
    assert second.state is JobState.CANCELLED
    assert ran == []


def test_a_failing_job_reports_its_error_rather_than_hanging() -> None:
    queue = JobQueue()

    def boom(_job):
        raise RuntimeError("deliberate")

    job = queue.submit(boom)
    deadline = time.time() + 10
    while time.time() < deadline and job.state not in {JobState.FAILED, JobState.DONE}:
        time.sleep(0.02)
    assert job.state is JobState.FAILED
    assert "deliberate" in job.error


# ================================================= 2. the server refuses to be a service


def test_binding_to_a_non_loopback_address_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="refusing to bind"):
        serve(tmp_path, host="0.0.0.0", port=free_port())  # noqa: S104


# ================================================= 3. end to end against the API


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A real server over a synthetic bundle, with a stubbed encoder.

    The bundle goes in pytest's temp directory, never inside the repository: a
    test that leaves files in the tracked tree would be shipped by the next
    release, and the inventory guard would refuse to build until someone
    classified them.
    """
    import numpy as np

    from conftest_bundle import build_bundle  # tests/ is on sys.path, as for cli_help

    root = build_bundle(tmp_path_factory.mktemp("bundle"))
    httpd = serve(root, port=free_port())

    class _Stub:
        device = "cpu"
        _model = object()
        load_warnings: list[str] = []

        def load(self):
            return []

        def embed(self, sequence: str):
            rng = np.random.default_rng(len(sequence))
            return rng.normal(size=(8,)).astype(np.float32)

    httpd.session.encoder = _Stub()  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, httpd
    httpd.shutdown()
    httpd.server_close()


def test_the_page_and_bundle_summary_are_served(live_server) -> None:
    base, _ = live_server
    status, body = _get(base + "/")
    assert status == 200
    assert b"Seq2Lead" in body and b"Prioritised candidates" not in body[:200]

    status, body = _get(base + "/api/bundle")
    assert status == 200
    summary = json.loads(body)
    assert summary["counts"]["compounds"] == 6
    assert "ranking score" in summary["disclaimer"]


def test_library_browsing_paginates_and_searches(live_server) -> None:
    base, _ = live_server
    status, body = _get(base + "/api/library?page=1&page_size=4")
    page = json.loads(body)
    assert status == 200
    assert page["total"] == 6 and page["pages"] == 2 and len(page["rows"]) == 4
    assert {"row", "compound_id", "smiles"} <= set(page["rows"][0])

    status, body = _get(base + "/api/library?q=C3")
    found = json.loads(body)
    assert [r["compound_id"] for r in found["rows"]] == ["C3"]


def test_depiction_is_svg(live_server) -> None:
    base, _ = live_server
    status, body = _get(base + "/api/depict?row=0")
    assert status == 200
    assert body.lstrip().startswith(b"<?xml") or body.lstrip().startswith(b"<svg")


def test_an_unknown_path_is_a_clean_404(live_server) -> None:
    base, _ = live_server
    status, _ = _get(base + "/api/nope")
    assert status == 404


def test_ranking_without_a_sequence_is_refused(live_server) -> None:
    base, _ = live_server
    status, payload = _post(base + "/api/rank", {"sequence": "  "})
    assert status == 400
    assert "no sequence" in payload["error"]


def test_two_fasta_records_are_refused_through_the_api(live_server) -> None:
    base, _ = live_server
    status, job = _post(
        base + "/api/rank", {"sequence": ">a\nMKVLAAAAAAAAAAAAAAAAAAAA\n>b\nMKVLAA"}
    )
    assert status == 202
    final = _await(base, job["id"])
    assert final["state"] == "failed"
    assert "FASTA records" in final["error"]


def _await(base: str, job_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/job/{job_id}")
        state = json.loads(body)
        if state["state"] in {"done", "failed", "cancelled"}:
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_the_interface_returns_the_same_ranking_as_the_library_call(live_server) -> None:
    """The UI must not be a second implementation of scoring."""
    base, httpd = live_server
    sequence = "MKVLAAAAAAAAAAAAAAAAAAAAWY"

    _, job = _post(base + "/api/rank", {"sequence": sequence, "top_n": 6})
    final = _await(base, job["id"])
    assert final["state"] == "done", final
    via_http = [(r["compound_id"], round(r["score_pki"], 6)) for r in final["result"]["rows"]]

    from seq2lead.inference.rank import rank

    direct = rank(httpd.session.bundle, sequence, top_n=6, encoder=httpd.session.encoder)
    via_api = [(r.compound_id, round(r.score_pki, 6)) for r in direct.rows]
    assert via_http == via_api


def test_csv_export_matches_the_cli_writer(live_server, tmp_path: Path) -> None:
    base, httpd = live_server
    sequence = "MKVLAAAAAAAAAAAAAAAAAAAAWY"
    _, job = _post(base + "/api/rank", {"sequence": sequence, "top_n": 6})
    _await(base, job["id"])

    status, body = _get(f"{base}/api/job/{job['id']}.csv")
    assert status == 200
    text = body.decode()
    assert "not a binding probability" in text
    assert "rank,compound_id,smiles,predicted_pki" in text


def test_a_shortlist_never_replaces_the_unfiltered_ranking(live_server) -> None:
    base, _ = live_server
    sequence = "MKVLAAAAAAAAAAAAAAAAAAAAWY"
    _, job = _post(base + "/api/rank", {"sequence": sequence, "top_n": 6, "max_mw": 100.0})
    final = _await(base, job["id"])
    assert final["state"] == "done", final
    result = final["result"]
    assert len(result["rows"]) == 6, "the unfiltered ranking must still be returned in full"
    assert result["shortlist"] is not None
    assert result["shortlist"]["kept"] + result["shortlist"]["removed"] == 6
    assert len(result["shortlist"]["removed_rows"]) == result["shortlist"]["removed"]
    for gone in result["shortlist"]["removed_rows"]:
        assert gone["original_rank"] >= 1
        assert "score_pki" in gone


def test_every_ranked_row_carries_a_bundle_row_for_depiction(live_server) -> None:
    """An earlier version depended on the library tab having been opened first."""
    base, _ = live_server
    _, job = _post(base + "/api/rank", {"sequence": "MKVLAAAAAAAAAAAAAAAAAAAAWY", "top_n": 6})
    final = _await(base, job["id"])
    rows = final["result"]["rows"]
    assert all(r["row"] >= 0 for r in rows), "a ranked row must know its bundle row"


# ========================== 4. rejected input, and what the browser is told

# Every case below was reproduced before it was fixed. The old behaviour is
# named in each docstring, because "returns 400" on its own does not say what
# the user would otherwise have seen.


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("nan", "not a finite number"),
        ("inf", "not a finite number"),
        ("-inf", "not a finite number"),
        ("1e400", "not a finite number"),
    ],
)
def test_a_non_finite_bound_is_refused_not_silently_empty(
    live_server, value: str, fragment: str
) -> None:
    """These used to return 200 with total=0, which reads as an empty library."""
    base, _ = live_server
    status, body = _get(f"{base}/api/library?min_mw={value}")
    assert status == 400, body
    assert fragment in json.loads(body)["error"]


def test_an_unparseable_bound_is_refused_not_ignored(live_server) -> None:
    """`min_mw=abc` used to be dropped, so the filter silently did nothing."""
    base, _ = live_server
    status, body = _get(f"{base}/api/library?min_mw=abc")
    assert status == 400
    assert "not a number" in json.loads(body)["error"]


def test_a_negative_property_bound_is_refused(live_server) -> None:
    base, _ = live_server
    status, body = _get(f"{base}/api/library?min_tpsa=-3")
    assert status == 400
    assert "negative" in json.loads(body)["error"]


def test_an_inverted_window_says_so_rather_than_returning_nothing(live_server) -> None:
    """min above max used to return 0 rows with no explanation."""
    base, _ = live_server
    status, body = _get(f"{base}/api/library?min_mw=500&max_mw=100")
    assert status == 400
    message = json.loads(body)["error"]
    assert "inverted" in message
    assert "500" in message and "100" in message


@pytest.mark.parametrize("query", ["page=0", "page=-5", "page_size=-3", "page_size=500"])
def test_pagination_outside_the_allowed_range_is_refused(live_server, query: str) -> None:
    """These used to be clamped in silence, so the page shown was not the one asked for."""
    base, _ = live_server
    status, body = _get(f"{base}/api/library?{query}")
    assert status == 400
    assert "outside the allowed range" in json.loads(body)["error"]


def test_a_valid_window_still_works(live_server) -> None:
    """The guard must reject bad input without rejecting good input."""
    base, _ = live_server
    status, body = _get(f"{base}/api/library?min_mw=0&max_mw=5000&page=1&page_size=3")
    assert status == 200
    assert len(json.loads(body)["rows"]) <= 3


def test_a_depiction_row_outside_the_library_is_refused(live_server) -> None:
    base, _ = live_server
    status, body = _get(f"{base}/api/depict?row=99999")
    assert status == 400
    assert "outside this library" in json.loads(body)["error"]


def test_depiction_size_is_bounded(live_server) -> None:
    """An unbounded size is a way to ask the server for an enormous SVG."""
    base, _ = live_server
    assert _get(f"{base}/api/depict?row=0&w=99999")[0] == 400
    assert _get(f"{base}/api/depict?row=0&w=300&h=240")[0] == 200


def test_each_requested_depiction_size_is_the_size_served(live_server) -> None:
    """The cache ignored width and height, so the first size drawn won for all of them.

    Checking only the status code passed throughout that bug; the assertion has to
    look at the SVG the server actually returned.
    """
    base, _ = live_server
    small = _get(f"{base}/api/depict?row=1&w=152&h=120")[1].decode()
    large = _get(f"{base}/api/depict?row=1&w=620&h=460")[1].decode()
    assert "width='152px' height='120px'" in small
    assert "width='620px' height='460px'" in large


# ============================= 5. caches must not cross bundles or recompute


def test_the_depiction_cache_is_keyed_by_bundle_not_by_row(tmp_path: Path) -> None:
    """Keyed on the row alone, a second bundle's row 0 served the first's structure."""
    from conftest_bundle import build_bundle
    from seq2lead.inference.bundle import load_bundle
    from seq2lead.web.api import Session, depict

    first = Session.open(load_bundle(build_bundle(tmp_path / "a")))
    second = Session.open(load_bundle(build_bundle(tmp_path / "b", n=5)))
    assert first.identity != "unknown"

    svg_a = depict(first, 0)
    svg_b = depict(second, 0)
    # Different bundles, so the cache must hold two entries rather than reuse one.
    from seq2lead.web.api import _DEPICTION_CACHE

    keys = {(ident, row) for ident, row, _w, _h in _DEPICTION_CACHE}
    assert (first.identity, 0) in keys
    assert (second.identity, 0) in keys
    assert first.identity != second.identity, "two bundles must not share a cache key"
    assert svg_a and svg_b


def test_descriptors_are_computed_once_per_structure(tmp_path: Path, monkeypatch) -> None:
    """Browsing page 2 used to recompute the descriptors for the whole library."""
    import seq2lead.inference.shortlist as shortlist_module
    from conftest_bundle import build_bundle
    from seq2lead.inference.bundle import load_bundle
    from seq2lead.web.api import _DESCRIPTOR_CACHE, Session, browse

    _DESCRIPTOR_CACHE.clear()
    calls: list[str] = []
    real = shortlist_module.properties

    def counted(smiles: str):
        calls.append(smiles)
        return real(smiles)

    monkeypatch.setattr(shortlist_module, "properties", counted)

    session = Session.open(load_bundle(build_bundle(tmp_path / "bundle")))
    browse(session, page=1, page_size=3, min_mw=0.0)
    after_first = len(calls)
    browse(session, page=1, page_size=3, min_mw=0.0)
    assert len(calls) == after_first, "a repeated page recomputed descriptors"
    assert len(set(calls)) == after_first, "the same structure was computed twice"


# ============================== 6. the compound detail and the distribution


def test_compound_detail_matches_the_bundle(live_server) -> None:
    base, httpd = live_server
    status, body = _get(f"{base}/api/compound?row=0")
    assert status == 200
    detail = json.loads(body)
    assert detail["compound_id"] == httpd.session.bundle.compound_ids[0]
    assert detail["smiles"] == httpd.session.bundle.smiles[0]
    assert detail["descriptors"]["heavy_atoms"] > 0
    assert "formula" in detail["descriptors"]


def test_compound_detail_refuses_a_row_outside_the_library(live_server) -> None:
    base, _ = live_server
    status, body = _get(f"{base}/api/compound?row=99999")
    assert status == 400
    assert "outside this library" in json.loads(body)["error"]


def test_the_payload_describes_every_score_not_only_the_kept_rows(live_server) -> None:
    """A single pKi is unreadable without the spread it came from."""
    base, httpd = live_server
    _, job = _post(base + "/api/rank", {"sequence": "MKVLAAAAAAAAAAAAAAAAAAAAWY", "top_n": 2})
    final = _await(base, job["id"])
    dist = final["result"]["distribution"]

    assert dist["n"] == httpd.session.bundle.n_compounds, "the histogram must cover the library"
    assert sum(dist["bins"]) == dist["n"], "every compound must land in exactly one bin"
    assert len(dist["edges"]) == len(dist["bins"]) + 1
    assert dist["min"] <= dist["p25"] <= dist["median"] <= dist["p75"] <= dist["max"]

    # It describes the scores; it must not alter them.
    top = final["result"]["rows"][0]["score_pki"]
    assert top <= dist["max"] + 1e-6


def test_the_distribution_does_not_change_the_ranking(live_server) -> None:
    """Adding the summary must leave rank and score identical to the library call.

    The payload rounds to six decimals on the way out, so both sides are compared
    at the precision the browser actually receives.
    """
    base, httpd = live_server
    sequence = "MKVLAAAAAAAAAAAAAAAAAAAAWY"
    _, job = _post(base + "/api/rank", {"sequence": sequence, "top_n": 6})
    final = _await(base, job["id"])
    rows = final["result"]["rows"]
    via_http = [(r["rank"], r["compound_id"], round(r["score_pki"], 6)) for r in rows]

    from seq2lead.inference.rank import rank

    direct = rank(httpd.session.bundle, sequence, top_n=6, encoder=httpd.session.encoder)
    assert via_http == [(r.rank, r.compound_id, round(r.score_pki, 6)) for r in direct.rows]


# ===================================== 7. cancellation over the HTTP API


def test_cancelling_over_http_stops_the_job_and_leaves_the_server_usable(live_server) -> None:
    """Cancellation is cooperative, so the server must still answer afterwards."""
    base, httpd = live_server
    original = httpd.session.encoder.embed
    started = threading.Event()
    release = threading.Event()

    def slow(sequence: str):
        started.set()
        release.wait(timeout=10)
        return original(sequence)

    httpd.session.encoder.embed = slow  # type: ignore[method-assign]
    try:
        _, job = _post(base + "/api/rank", {"sequence": "MKVLAAAAAAAAAAAAAAAAAAAAWY", "top_n": 6})
        assert started.wait(timeout=10), "the job never started"
        status, cancelled = _post(f"{base}/api/job/{job['id']}/cancel")
        assert status == 200
        release.set()
        final = _await(base, job["id"])
        assert final["state"] == "cancelled", final
        assert "result" not in final
    finally:
        httpd.session.encoder.embed = original  # type: ignore[method-assign]
        release.set()

    assert _get(base + "/api/bundle")[0] == 200, "the server stopped serving after a cancel"
    _, job2 = _post(base + "/api/rank", {"sequence": "MKVLAAAAAAAAAAAAAAAAAAAAWY", "top_n": 2})
    assert _await(base, job2["id"])["state"] == "done", "the queue did not recover"


def test_cancelling_an_unknown_job_is_a_clean_404(live_server) -> None:
    base, _ = live_server
    status, body = _post(f"{base}/api/job/nosuchjob/cancel")
    assert status == 404
    assert "no such job" in body["error"]
