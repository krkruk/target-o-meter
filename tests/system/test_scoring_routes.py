"""System test: ``POST /v1/scoring/jobs`` + ``GET /v1/scoring/jobs/{job_id}``.

The repo's first multipart system test (AGENTS.md §4). Two styles mirroring the
existing suite:

  - **Django test client** (``client.force_login``) for the status-code matrix
    (201/401/404/422, per-job ownership 404 not 403, atomicity rollback). Fast.
  - **Live ``runserver``** (``runserver_factory``) for the real CSRF-cookie +
    multipart path that the SPA will actually drive (mirror of
    ``test_spa_auth_seam.py``).

The MockDetector round-trip is exercised by **patching**
``src.domains.vision.services.DetectorFactory.build`` → ``MockDetector`` and
running ``process_image`` **synchronously** inside the test (no q2 worker
needed): the POST creates the ``ScoringJob`` + enqueues, then the test runs
the task body directly to drive ``queued → succeeded``, then GET reads it
back. This mirrors ``test_services_q2.py`` and keeps the test deterministic
without standing up a q2 cluster.

Uses ``override_settings(MEDIA_ROOT=tmp_path)`` so the ``FileSystemStorage``
default branch writes uploads + deliverables under pytest's tmp, not the
developer's ``src/``.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.test import override_settings

from src.domains.identity.test_utils import make_owner, make_user
from src.domains.vision.models import ScoringJob


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


FIXTURE_12 = (
    Path(__file__).resolve().parents[2]
    / "src" / "domains" / "vision" / "tests" / "fixtures" / "12.jpg"
)


def _login_as(client, user) -> None:
    """Authenticate the test client as ``user`` (populates ``request.user``)."""
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")


def _seed_csrf(client) -> str:
    """Hit ``/`` so the ``csrftoken`` cookie is set, then return its value."""
    client.get("/")
    return client.cookies["csrftoken"].value


def _multipart_post(client, path: str, *, file_bytes: bytes, file_name: str = "12.jpg",
                    target_type: str = "air_pistol", caliber_hint: str | None = None,
                    distance: int | None = None, weapon_type: str | None = None,
                    csrf: str | None = None):
    """POST a multipart form. Mirrors what the SPA's ``createScoringJob`` does:
    a real ``multipart/form-data`` body with the file + form fields + X-CSRFToken.

    Form field names are flat (``target_type``, NOT ``details.target_type``) —
    ninja's ``Form[Schema]`` flattens the schema's fields to their alias names
    (see ``ninja/signature/details.py::_model_flatten_map``). S-03 renamed the
    S-02 mock field ``distance_m`` → ``distance`` (now a real column) and added
    ``weapon_type`` (FR-009)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    data: dict = {
        "file": SimpleUploadedFile(file_name, file_bytes, content_type="image/jpeg"),
        "target_type": target_type,
    }
    if caliber_hint is not None:
        data["caliber_hint"] = caliber_hint
    if distance is not None:
        data["distance"] = str(distance)
    if weapon_type is not None:
        data["weapon_type"] = weapon_type

    kw: dict = {"data": data}
    if csrf is not None:
        kw["HTTP_X_CSRFTOKEN"] = csrf
    return client.post(path, **kw)


# ---------------------------------------------------------------------------
# POST /v1/scoring/jobs — status-code matrix
# ---------------------------------------------------------------------------


def test_post_scoring_jobs_returns_401_anonymous(client, user_sub) -> None:
    """No session → ``session_auth`` falsy → 401 (before the body runs)."""
    response = _multipart_post(client, "/v1/scoring/jobs",
                               file_bytes=FIXTURE_12.read_bytes())
    assert response.status_code == 401


def test_post_scoring_jobs_creates_job_for_user_role(
    client, user_sub, tmp_path
) -> None:
    """Authed User role with valid image bytes → 201, ``{job_id, status}``,
    and a ``ScoringJob`` row exists owned by the authed user with status queued."""
    user = make_user(sub=user_sub, nick="alice")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        response = _multipart_post(
            client, "/v1/scoring/jobs",
            file_bytes=FIXTURE_12.read_bytes(),
            target_type="air_pistol",
            caliber_hint="9x19mm",
            distance=25,
            weapon_type="sport_pistol",
            csrf=csrf,
        )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "queued"
    assert "job_id" in body

    job = ScoringJob.objects.get(id=body["job_id"])
    assert job.status == ScoringJob.Status.QUEUED
    assert job.user_uuid == user.id
    assert job.target_type == "air_pistol"
    assert job.caliber_hint == "9x19mm"
    # S-03 FR-009: distance + weapon_type forwarded onto the ScoringJob row.
    assert job.distance == 25
    assert job.weapon_type == "sport_pistol"


def test_post_scoring_jobs_allows_owner_role(
    client, owner_sub, tmp_path
) -> None:
    """Both Owner and User roles can upload (PRD FR-006/FR-007). The upload
    route uses ``session_auth`` only — ``require_owner`` is NOT applied."""
    owner = make_owner(owner_sub)
    _login_as(client, owner)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        response = _multipart_post(
            client, "/v1/scoring/jobs", file_bytes=FIXTURE_12.read_bytes(), csrf=csrf,
        )

    assert response.status_code == 201, response.content
    job = ScoringJob.objects.get(id=response.json()["job_id"])
    assert job.user_uuid == owner.id


def test_post_scoring_jobs_rejects_missing_file(client, user_sub, tmp_path) -> None:
    """Missing file → 422 (django-ninja request-schema validation)."""
    user = make_user(sub=user_sub, nick="bob")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        response = client.post(
            "/v1/scoring/jobs",
            data={"target_type": "air_pistol"},
            HTTP_X_CSRFTOKEN=csrf,
        )
    assert response.status_code == 422


def test_post_scoring_jobs_rejects_invalid_target_type(
    client, user_sub, tmp_path
) -> None:
    """``target_type=banana`` → 422 at the BFF boundary.

    The ``Literal["air_pistol", "precision_pistol"]`` on the request DTO guards
    against the silent-save/async-blowup the ORM's choice-less ``CharField``
    would otherwise allow (an invalid value would save cleanly and only fail
    inside ``process_image`` when the worker runs).
    """
    user = make_user(sub=user_sub, nick="carol")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        response = _multipart_post(
            client, "/v1/scoring/jobs",
            file_bytes=FIXTURE_12.read_bytes(),
            target_type="banana",
            csrf=csrf,
        )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Atomicity: BFF raise after enqueue rolls the ScoringJob row back.
# ---------------------------------------------------------------------------


def test_post_scoring_jobs_rolls_back_on_enqueue_failure(
    client, user_sub, tmp_path
) -> None:
    """If ``schedule_image_processing`` raises after the row is created, the
    BFF's outer ``transaction.atomic`` rolls the row back — no orphan queued
    job is left behind.

    The RuntimeError is NOT swallowed by the BFF (no generic try/except around
    the orchestration) — it propagates as an unhandled server error, and the
    Django test client re-raises it. The load-bearing assertion is the
    rollback: after the exception, no ``ScoringJob`` row for this upload
    survives. Mirrors the assertion shape at ``test_services_q2.py:135-161``
    but at the BFF layer (the service's own atomic block is a nested savepoint).
    """
    user = make_user(sub=user_sub, nick="dave")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    existing_count = ScoringJob.objects.count()

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with patch(
            "src.bff.routers.scoring_routes.schedule_image_processing",
            side_effect=RuntimeError("q2 broker unreachable"),
        ):
            with pytest.raises(RuntimeError, match="q2 broker unreachable"):
                _multipart_post(
                    client, "/v1/scoring/jobs",
                    file_bytes=FIXTURE_12.read_bytes(), csrf=csrf,
                )

    # No new ScoringJob row survived the rollback — the BFF's outer
    # ``transaction.atomic`` undid the row + the q2 task enqueue together.
    assert ScoringJob.objects.count() == existing_count


# ---------------------------------------------------------------------------
# GET /v1/scoring/jobs/{job_id} — read accessor + ownership enforcement.
# ---------------------------------------------------------------------------


def test_get_scoring_job_404_for_nonexistent_id(client, user_sub) -> None:
    """A made-up job id → 404 (the ``PermissionError`` from ``get_job`` maps
    to 404, not 403, so an ID-prober can't distinguish "exists, not mine"
    from "doesn't exist")."""
    user = make_user(sub=user_sub, nick="erin")
    _login_as(client, user)

    response = client.get("/v1/scoring/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_scoring_job_404_for_other_users_job(
    client, owner_sub, user_sub, tmp_path
) -> None:
    """A job owned by another user → 404 (NOT 403 — ``PermissionError``
    mapping from ``get_job``)."""
    owner = make_owner(owner_sub)
    other = make_user(sub=user_sub, nick="frank")
    _login_as(client, owner)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        post = _multipart_post(
            client, "/v1/scoring/jobs", file_bytes=FIXTURE_12.read_bytes(), csrf=csrf,
        )
    job_id = post.json()["job_id"]

    # Log in as the other user and try to read the owner's job.
    client.logout()
    _login_as(client, other)
    response = client.get(f"/v1/scoring/jobs/{job_id}")
    assert response.status_code == 404


def test_get_scoring_job_returns_marked_image_url_when_succeeded(
    client, user_sub, tmp_path, monkeypatch
) -> None:
    """After ``process_image`` succeeds, the GET response carries
    ``marked_image_url`` populated from ``job.marked_image_path`` via
    ``ScoringStorage()._storage.url(...)`` (NOT the global ``default_storage``
    — under FS dev the marked path is relative to ``MEDIA_ROOT/scoring``, so
    ``default_storage`` would resolve against the wrong root; see the comment
    in ``services._job_to_dto``). Pins Phase 4.0's DTO field surfacing."""
    from src.domains.vision.detectors.mock_detector import MockDetector
    from src.domains.vision.services import process_image

    # S-03: pin the mock's random pattern so the hole-count assertion is stable.
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "42")
    monkeypatch.setenv("MOCK_DETECTOR_HOLE_COUNT", "5")

    user = make_user(sub=user_sub, nick="grace")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        post = _multipart_post(
            client, "/v1/scoring/jobs", file_bytes=FIXTURE_12.read_bytes(), csrf=csrf,
        )
        job_id = post.json()["job_id"]

        # Drive the q2 task body synchronously (no worker needed) with the
        # MockDetector so the job transitions queued → succeeded.
        with patch(
            "src.domains.vision.services.DetectorFactory.build",
            return_value=MockDetector(),
        ):
            process_image(job_id)

        response = client.get(f"/v1/scoring/jobs/{job_id}")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "succeeded"
    # MockDetector's fixed 5-hole pattern.
    assert len(body["result"]["holes"]) == 5
    # The marked image URL is surfaced (Phase 4.0).
    assert body["marked_image_url"]


def test_get_scoring_job_marked_image_url_none_while_queued(
    client, user_sub, tmp_path
) -> None:
    """``marked_image_url`` stays ``None`` until ``process_image`` writes the
    deliverable and flips status=SUCCEEDED — a queued/running job has no marked
    image yet, and the GET must not 500 on the missing path."""
    user = make_user(sub=user_sub, nick="heidi")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        post = _multipart_post(
            client, "/v1/scoring/jobs", file_bytes=FIXTURE_12.read_bytes(), csrf=csrf,
        )
        job_id = post.json()["job_id"]
        response = client.get(f"/v1/scoring/jobs/{job_id}")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "queued"
    assert body["marked_image_url"] is None


# ---------------------------------------------------------------------------
# Live runserver: real CSRF-cookie + multipart path (mirror of
# test_spa_auth_seam.py — the path the SPA actually drives).
# ---------------------------------------------------------------------------


def test_post_scoring_jobs_works_on_live_runserver(
    runserver_factory, tmp_path
) -> None:
    """Multipart POST on the real WSGI stack: seed CSRF cookie via ``GET /``,
    then POST the file + form fields + ``X-CSRFToken`` → 201.

    This is the contract the SPA's ``createScoringJob`` helper relies on: the
    browser sets the multipart ``boundary`` (no ``Content-Type`` header from
    the client), and the CSRF token round-trips through the cookie.
    """
    # runserver_factory boots with its own DB under results/<run-id>/; the
    # MockDetector's deliverables land under BASE_DIR/scoring_storage by
    # default (no MEDIA_ROOT configured), which is writable in the run dir.
    extra_env = {
        "DEV_AUTH_BYPASS_SUB": "auth0|scoring-live",
        "VISION_DETECTOR": "mock",
    }
    server = runserver_factory(extra_env=extra_env)

    # Seed the CSRF cookie (the SPA's index view renders {% csrf_token %}).
    server.get("/")
    token = server._client.cookies.get("csrftoken", "")
    assert token, "no csrftoken cookie seeded by /"

    # Build the multipart body the way httpx does for files + form fields.
    # Field names are flat (ninja's Form[Schema] flattens schema fields).
    files = {"file": ("12.jpg", FIXTURE_12.read_bytes(), "image/jpeg")}
    data = {"target_type": "air_pistol", "caliber_hint": "9x19mm"}

    response = server.post(
        "/v1/scoring/jobs",
        files=files,
        data=data,
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "queued"
    assert "job_id" in response.json()
    server.assert_no_traceback()


# ---------------------------------------------------------------------------
# POST /v1/scoring/results — accept a detection result (S-03 Phase 2)
# ---------------------------------------------------------------------------
#
# Resource-named per the API-design lesson (the accepted-result resource, NOT
# ``/scoring/accept``). Idempotent on re-POST via a unique_together constraint
# (the F1 fix from plan-review). Body is JSON ``AcceptResultIn`` carrying the
# ``job_id`` + confirmed params + the corrected-hole snapshot.

from src.domains.vision.models import AcceptedResult  # noqa: E402


def _seed_succeeded_job(user_uuid, *, target_type="air_pistol",
                        caliber_hint="9x19mm", distance=25,
                        weapon_type="sport_pistol") -> ScoringJob:
    """Construct a SUCCEEDED ScoringJob directly (no detector / no q2). The
    accept path only reads the row + enforces status==SUCCEEDED, so a hand-built
    succeeded row is the cleanest isolation seed (mirrors the plan's
    "directly construct a ScoringJob(status=SUCCEEDED)" option)."""
    return ScoringJob.objects.create(
        user_uuid=user_uuid,
        status=ScoringJob.Status.SUCCEEDED,
        input_path="uploads/seed.jpg",
        target_type=target_type,
        caliber_hint=caliber_hint,
        distance=distance,
        weapon_type=weapon_type,
        result={
            "ok": True,
            "holes": [
                {"x": 500, "y": 510, "score": 9, "confidence": 0.95},
                {"x": 520, "y": 500, "score": 10, "confidence": 0.97},
            ],
            "target_type": target_type,
            "detector": "mock",
            "notes": "seed",
        },
        marked_image_path="uploads/seed_marked.png",
    )


def _accept_payload(job_id, *, holes=None, target_type="air_pistol",
                    distance=25, weapon_type="sport_pistol",
                    caliber_hint="9x19mm") -> dict:
    """Build the JSON body for POST /v1/scoring/results (AcceptResultIn)."""
    return {
        "job_id": str(job_id),
        "target_type": target_type,
        "caliber_hint": caliber_hint,
        "distance": distance,
        "weapon_type": weapon_type,
        "holes": holes if holes is not None else [
            {"x": 500, "y": 510, "score": 9, "confidence": 0.95},
            {"x": 520, "y": 500, "score": 10, "confidence": 0.97},
        ],
    }


def test_post_scoring_results_returns_401_anonymous(client) -> None:
    """No session → session_auth falsy → 401 before the body runs."""
    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(uuid4())),
        content_type="application/json",
    )
    assert response.status_code == 401


def test_post_scoring_results_creates_accepted_result(client, user_sub) -> None:
    """First accept of a succeeded job owned by the user → 201, returns
    AcceptedResultDTO with the snapshot holes + score_average = mean of the
    sent holes (9.5 here). Exactly one AcceptedResult row exists."""
    user = make_user(sub=user_sub, nick="ivy")
    _login_as(client, user)
    csrf = _seed_csrf(client)

    job = _seed_succeeded_job(user.id, target_type="precision_pistol")

    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(
            job.id, target_type="precision_pistol",
            holes=[
                {"x": 100, "y": 100, "score": 8, "confidence": 0.9},
                {"x": 200, "y": 200, "score": 10, "confidence": 0.95},
            ],
        )),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["source_job"] == str(job.id)
    assert body["target_type"] == "precision_pistol"
    assert len(body["holes"]) == 2
    assert body["score_average"] == pytest.approx(9.0)  # mean(8, 10)
    assert body["result_id"]

    # Exactly one AcceptedResult row, snapshotting the confirmed params.
    assert AcceptedResult.objects.filter(source_job=job.id).count() == 1
    ar = AcceptedResult.objects.get(source_job=job.id)
    assert ar.user_uuid == user.id
    assert ar.target_type == "precision_pistol"
    assert ar.distance == 25
    assert ar.weapon_type == "sport_pistol"
    assert ar.score_average == pytest.approx(9.0)
    assert len(ar.holes) == 2


def test_post_scoring_results_idempotent_on_repost(client, user_sub) -> None:
    """Re-POST for the same job → 200, returns the SAME result_id (idempotent).
    No duplicate row is created."""
    user = make_user(sub=user_sub, nick="jack")
    _login_as(client, user)
    csrf = _seed_csrf(client)
    job = _seed_succeeded_job(user.id)

    first = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(job.id)),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert first.status_code == 201, first.content
    first_id = first.json()["result_id"]

    second = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(job.id)),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert second.status_code == 200, second.content
    assert second.json()["result_id"] == first_id
    assert AcceptedResult.objects.filter(source_job=job.id).count() == 1


def test_post_scoring_results_preexisting_row_returns_existing(
    client, user_sub,
) -> None:
    """Race regression (plan-review F1) — savepoint recovery path: when an
    ``AcceptedResult`` already exists for the job, ``accept_job`` hits the
    ``unique_together`` IntegrityError, the savepoint rolls back, and the
    service re-fetches + returns the existing row (the 200 idempotent path)
    WITHOUT poisoning the transaction (no ``TransactionManagementError``).

    This is the single-threaded, deterministic expression of the concurrent-
    accept race: the load-bearing guarantee is that the IntegrityError is
    caught *after a savepoint* (so the outer transaction stays queryable) and
    the existing row is returned. The full concurrent HTTP round-trip is
    covered by the blackbox system test against the live ``runserver``
    subprocess (real parallelism under SQLite's busy timeout)."""
    from src.domains.vision.dtos import DetectedHoleDTO
    from src.domains.vision.services import accept_job

    user = make_user(sub=user_sub, nick="kate")
    _login_as(client, user)
    csrf = _seed_csrf(client)
    job = _seed_succeeded_job(user.id)

    # Pre-create the row (simulates the race-winner having already committed).
    AcceptedResult.objects.create(
        user_uuid=user.id, source_job=job.id, target_type="air_pistol",
        caliber_hint="9x19mm", distance=25, weapon_type="sport_pistol",
        holes=[{"x": 1, "y": 1, "score": 7, "confidence": 0.9}],
        score_average=7.0,
    )
    preexisting_id = AcceptedResult.objects.get(source_job=job.id).id

    # Re-POST via the route → must return 200 + the SAME row, no 500.
    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(job.id)),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200, response.content
    assert response.json()["result_id"] == str(preexisting_id)
    # Still exactly one row.
    assert AcceptedResult.objects.filter(source_job=job.id).count() == 1

    # And calling the service directly also recovers (no TransactionManagementError).
    dto, created = accept_job(
        job_id=job.id, user_uuid=user.id, target_type="air_pistol",
        caliber_hint="9x19mm", distance=25, weapon_type="sport_pistol",
        holes=[DetectedHoleDTO(x=2, y=2, score=8, confidence=0.9)],
    )
    assert created is False
    assert dto.result_id == preexisting_id


def test_post_scoring_results_404_for_other_users_job(client, user_sub) -> None:
    """Accept a job owned by another user → 404 (ownership via PermissionError,
    identical to the existing GET ownership map)."""
    owner = make_user(sub="auth0|other-owner", nick="leo")
    intruder = make_user(sub=user_sub, nick="mia")
    job = _seed_succeeded_job(owner.id)  # owned by leo

    _login_as(client, intruder)
    csrf = _seed_csrf(client)
    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(job.id)),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 404
    assert not AcceptedResult.objects.filter(source_job=job.id).exists()


def test_post_scoring_results_404_for_nonexistent_job(client, user_sub) -> None:
    user = make_user(sub=user_sub, nick="nina")
    _login_as(client, user)
    csrf = _seed_csrf(client)
    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(uuid4())),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 404


def test_post_scoring_results_409_for_non_succeeded_job(client, user_sub) -> None:
    """Accept a job still in queued/running/failed → 409 Conflict. The service
    raises StateError when job.status != SUCCEEDED; the route maps it to 409."""
    from src.domains.vision.models import ScoringJob as SJ
    user = make_user(sub=user_sub, nick="oscar")
    _login_as(client, user)
    csrf = _seed_csrf(client)
    job = SJ.objects.create(
        user_uuid=user.id, status=SJ.Status.RUNNING,
        input_path="uploads/seed.jpg", target_type="air_pistol",
    )
    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(job.id)),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 409


def test_post_scoring_results_422_for_empty_holes(client, user_sub) -> None:
    """Empty holes list → 422 (validation; the score snapshot must have >=1
    hole for aggregation to be meaningful)."""
    user = make_user(sub=user_sub, nick="paul")
    _login_as(client, user)
    csrf = _seed_csrf(client)
    job = _seed_succeeded_job(user.id)
    response = client.post(
        "/v1/scoring/results",
        data=json.dumps(_accept_payload(job.id, holes=[])),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 422
