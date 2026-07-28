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

from pathlib import Path
from unittest.mock import patch

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
    client, user_sub, tmp_path
) -> None:
    """After ``process_image`` succeeds, the GET response carries
    ``marked_image_url`` populated from ``job.marked_image_path`` via
    ``ScoringStorage()._storage.url(...)`` (NOT the global ``default_storage``
    — under FS dev the marked path is relative to ``MEDIA_ROOT/scoring``, so
    ``default_storage`` would resolve against the wrong root; see the comment
    in ``services._job_to_dto``). Pins Phase 4.0's DTO field surfacing."""
    from src.domains.vision.detectors.mock_detector import MockDetector
    from src.domains.vision.services import process_image

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
