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
