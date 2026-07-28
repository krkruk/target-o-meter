"""Blackbox system test: the accept → persist → aggregate round-trip (S-03).

V-Model system tier (per ``.agents/skills/system-test/``): the app is a black
box. We boot a real ``runserver`` subprocess, drive it over HTTP, assert on the
responses + the captured stderr (no traceback), AND read the throwaway SQLite
file the server wrote to confirm records landed on disk. We never reach into
the app's internals during the test; seeding goes through the app's own
``manage.py shell`` (the app's CLI surface) before boot.

The round-trip covered (the substance of manual items 2.6/2.7/5.4/5.5):

  1. POST /v1/scoring/jobs carries distance + weapon_type → 201, a ScoringJob
     row exists on disk carrying both (FR-009).
  2. POST /v1/scoring/results for a succeeded job → 201, an AcceptedResult row
     lands on disk snapshotting the holes + score_average (FR-010). A second
     POST → 200 with the same result_id (idempotent).
  3. GET /v1/scores/aggregations → hero stats reflect the accepted result
     (total_shots = sum of holes, best_result, last_session_average) (FR-012).
  4. anon POST/GET → 401 (the auth gate holds on the real stack).

This is the per-phase completeness verifier for S-03: it exercises every new
route end-to-end against the assembled system, not just the Django test client.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_IMG = (
    _REPO_ROOT / "src" / "domains" / "vision" / "tests" / "fixtures" / "12.jpg"
)


def _count_rows(server, table: str, where: str = "") -> int:
    """Read the throwaway SQLite file the server wrote (read-only — the skill's
    blackbox contract allows reading the DB to verify state changed).

    Settings writes to ``<run_dir>/db.sqlite3`` (NOT the conftest's
    ``server.db_path`` attribute, which is a stale label) — resolve the real
    path from the run dir."""
    db_path = server.run_dir / "db.sqlite3"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table} {where}")
        return cur.fetchone()[0]
    finally:
        conn.close()


def _read_db(server, query: str):
    """Run a read-only SQL query against the throwaway DB (blackbox: read only)."""
    db_path = server.run_dir / "db.sqlite3"
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


# Seed script: create a SUCCEEDED ScoringJob owned by the dev-bypass user, so
# the HTTP test can accept it over the real stack. Runs via ``manage.py shell``
# against the throwaway DB BEFORE the server boots (the app's own CLI surface —
# the skill permits seeding through the app, just not writing via ORM tools
# during the test body).
_SEED_SUCCEEDED_JOB = """
import os
import django
django.setup()
from src.domains.identity.models import User
from src.domains.vision.models import ScoringJob

# Resolve the dev-bypass user's UUID so the seeded job is owned by the same
# user the live server's auth will authenticate.
sub = os.environ['DEV_AUTH_BYPASS_SUB']
user, _ = User.objects.get_or_create(sub=sub, defaults={'nick': 'seed'})
job = ScoringJob.objects.create(
    user_uuid=user.id,
    status=ScoringJob.Status.SUCCEEDED,
    input_path='uploads/seed.jpg',
    target_type='air_pistol',
    caliber_hint='9x19mm',
    distance=25,
    weapon_type='sport_pistol',
    result={
        'ok': True,
        'holes': [
            {'x': 500, 'y': 510, 'score': 9, 'confidence': 0.95},
            {'x': 520, 'y': 500, 'score': 10, 'confidence': 0.97},
        ],
        'target_type': 'air_pistol', 'detector': 'mock', 'notes': 'seed',
    },
    marked_image_path='uploads/seed_marked.png',
)
print('SEED_JOB_ID', job.id)
"""


def test_accept_persist_aggregate_round_trip_on_live_server(
    runserver_factory, tmp_path, monkeypatch
) -> None:
    """The full S-03 round-trip against the assembled system: POST a job
    carrying distance+weapon_type, accept a seeded succeeded job, read the
    SQLite file to confirm the AcceptedResult row landed, then aggregate."""
    # The dev-bypass user's UUID is derived from DEV_AUTH_BYPASS_SUB. The seed
    # script (run via manage.py shell BEFORE boot) resolves the same UUID by
    # creating/reading the User row for that sub, so the seeded SUCCEEDED job
    # and the live auth share an owner.
    bypass_sub = "auth0|accept-persist-blackbox"

    server = runserver_factory(
        extra_env={
            "DEV_AUTH_BYPASS_SUB": bypass_sub,
            "VISION_DETECTOR": "mock",
        },
        seed_script=_SEED_SUCCEEDED_JOB,
    )

    # --- 1. POST /v1/scoring/jobs carries distance + weapon_type (FR-009) ---
    server.get("/")
    token = server._client.cookies.get("csrftoken", "")
    assert token, "no csrftoken cookie seeded by /"

    files = {"file": ("12.jpg", _FIXTURE_IMG.read_bytes(), "image/jpeg")}
    data = {
        "target_type": "air_pistol", "caliber_hint": "9x19mm",
        "distance": "25", "weapon_type": "sport_pistol",
    }
    post = server.post(
        "/v1/scoring/jobs", files=files, data=data, headers={"X-CSRFToken": token},
    )
    assert post.status_code == 201, post.text
    assert post.json()["status"] == "queued"
    # The ScoringJob row landed on disk carrying distance + weapon_type.
    n_jobs = _count_rows(server, "vision_scoringjob")
    assert n_jobs >= 1
    # The most recent job carries the forwarded params (read from the DB file).
    rows = _read_db(server, "SELECT distance, weapon_type FROM vision_scoringjob "
                            "ORDER BY created_at DESC LIMIT 1")
    row = rows[0]
    assert row == (25, "sport_pistol"), f"expected (25, sport_pistol), got {row}"
    server.assert_no_traceback()

    # --- 2. POST /v1/scoring/results accepts the seeded succeeded job (FR-010) ---
    # Read the seeded job's id from the DB file (it was created pre-boot by the
    # seed script).
    seeded = _read_db(server, "SELECT id FROM vision_scoringjob "
                              "WHERE status='succeeded' LIMIT 1")
    assert seeded, "seed script did not create a succeeded ScoringJob"
    job_id = seeded[0][0]

    accept_body = {
        "job_id": job_id,
        "target_type": "air_pistol",
        "caliber_hint": "9x19mm",
        "distance": 25,
        "weapon_type": "sport_pistol",
        "holes": [
            {"x": 500, "y": 510, "score": 9, "confidence": 0.95},
            {"x": 520, "y": 500, "score": 10, "confidence": 0.97},
        ],
    }
    accept = server.post(
        "/v1/scoring/results",
        json=accept_body,
        headers={"X-CSRFToken": token, "Content-Type": "application/json"},
    )
    assert accept.status_code == 201, accept.text
    result_id = accept.json()["result_id"]
    assert accept.json()["score_average"] == pytest.approx(9.5)
    # The AcceptedResult row landed on disk (FR-010 persistence).
    assert _count_rows(server, "vision_acceptedresult") == 1

    # Idempotent re-POST → 200, same result_id.
    accept2 = server.post(
        "/v1/scoring/results",
        json=accept_body,
        headers={"X-CSRFToken": token, "Content-Type": "application/json"},
    )
    assert accept2.status_code == 200, accept2.text
    assert accept2.json()["result_id"] == result_id
    # Still exactly one row.
    assert _count_rows(server, "vision_acceptedresult") == 1
    server.assert_no_traceback()

    # --- 3. GET /v1/scores/aggregations reflects the accepted result (FR-012) ---
    agg = server.get("/v1/scores/aggregations")
    assert agg.status_code == 200, agg.text
    hero = agg.json()["hero"]
    # total_shots = sum of holes across the accepted result = 2.
    assert hero["total_shots"] == 2
    assert hero["best_result"] == pytest.approx(9.5)
    # last_session_average = today's mean = 9.5.
    assert hero["last_session_average"] == pytest.approx(9.5)
    assert len(agg.json()["recent"]) == 1
    server.assert_no_traceback()


def test_aggregation_anon_401_on_live_server(runserver_factory) -> None:
    """The auth gate holds on the real stack: anon GET /v1/scores/aggregations → 401."""
    server = runserver_factory(extra_env={"VISION_DETECTOR": "mock"})
    response = server.get("/v1/scores/aggregations")
    assert response.status_code == 401
    server.assert_no_traceback()


def test_accept_anon_401_on_live_server(runserver_factory) -> None:
    """Anon POST /v1/scoring/results → 401 (the auth gate before the body runs)."""
    server = runserver_factory(extra_env={"VISION_DETECTOR": "mock"})
    server.get("/")
    token = server._client.cookies.get("csrftoken", "")
    response = server.post(
        "/v1/scoring/results",
        json={"job_id": str(uuid4()), "target_type": "air_pistol", "holes": [
            {"x": 1, "y": 1, "score": 9, "confidence": 0.9}]},
        headers={"X-CSRFToken": token, "Content-Type": "application/json"},
    )
    assert response.status_code == 401
    server.assert_no_traceback()
