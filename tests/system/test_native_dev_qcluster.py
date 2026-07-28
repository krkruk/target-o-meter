"""Blackbox repro for ISSUE 1: bare-metal ``make dev`` started no qcluster, so
``process_image`` was enqueued onto django-q2's SQLite broker but nothing
consumed it → the job sat at ``status: queued`` forever (UI: "Queued — waiting
for a worker…").

This is the config/infra class (Makefile wiring), not pure logic, so per the
TDD-ability gate it's reproduced here as a blackbox system test, not driven
unit-first. The test proves the qcluster is the missing piece: with NO qcluster
running, a freshly-POSTed job stays ``queued``; once a ``qcluster`` subprocess
is pointed at the same broker DB, the job transitions ``queued → succeeded``.
The Makefile ``dev`` target is the wiring that runs that qcluster in native dev.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "src" / "domains" / "vision" / "tests" / "fixtures" / "12.jpg"
)
_MANAGE_PY = Path(__file__).resolve().parents[2] / "src" / "manage.py"


def _start_qcluster(server) -> subprocess.Popen:
    """Spawn a ``qcluster`` against the SAME broker DB the runserver writes to
    (its ``base_env`` points ``RAILWAY_VOLUME_MOUNT_PATH`` at the run dir, so
    the SQLite DB is shared). Returns the Popen so the test can terminate it."""
    return subprocess.Popen(
        [sys.executable, str(_MANAGE_PY), "qcluster"],
        cwd=str(_MANAGE_PY.parent),
        env=server.base_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _post_job(server) -> str:
    """POST a scoring job on the live runserver; return its job_id."""
    server.get("/")  # seed CSRF cookie
    token = server._client.cookies.get("csrftoken", "")
    files = {"file": ("12.jpg", _FIXTURE.read_bytes(), "image/jpeg")}
    data = {"target_type": "air_pistol", "caliber_hint": "9x19mm"}
    response = server.post(
        "/v1/scoring/jobs",
        files=files,
        data=data,
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def test_job_stays_queued_without_qcluster(runserver_factory) -> None:
    """RED baseline: with NO qcluster consuming the broker, a POSTed job never
    leaves ``queued`` — the reported symptom. Pins the bug the Makefile fix
    addresses."""
    server = runserver_factory(extra_env={
        "DEV_AUTH_BYPASS_SUB": "auth0|noqcluster",
        "VISION_DETECTOR": "mock",
    })
    job_id = _post_job(server)

    deadline = time.monotonic() + 3
    status = "queued"
    while time.monotonic() < deadline:
        body = server.get(f"/v1/scoring/jobs/{job_id}").json()
        status = body["status"]
        if status != "queued":
            break
        time.sleep(0.3)

    assert status == "queued", (
        f"job left 'queued' with NO qcluster running — unexpected (status={status})"
    )
    server.assert_no_traceback()


def test_job_transitions_to_succeeded_with_qcluster(runserver_factory) -> None:
    """GREEN proof: once a ``qcluster`` is pointed at the same broker DB, the
    POSTed job is picked up and transitions to a terminal state (succeeded under
    the MockDetector). This is the behavior the Makefile ``dev`` target must
    deliver by starting a qcluster alongside runserver + Vite."""
    server = runserver_factory(extra_env={
        "DEV_AUTH_BYPASS_SUB": "auth0|withqcluster",
        "VISION_DETECTOR": "mock",
    })
    job_id = _post_job(server)

    qcluster = _start_qcluster(server)
    try:
        deadline = time.monotonic() + 30
        status = "queued"
        while time.monotonic() < deadline:
            body = server.get(f"/v1/scoring/jobs/{job_id}").json()
            status = body["status"]
            if status in {"succeeded", "failed"}:
                break
            time.sleep(0.5)
    finally:
        qcluster.terminate()
        try:
            qcluster.wait(timeout=5)
        except subprocess.TimeoutExpired:
            qcluster.kill()

    assert status == "succeeded", (
        f"job did not reach 'succeeded' within 30s with qcluster running "
        f"(status={status})"
    )
    server.assert_no_traceback()
