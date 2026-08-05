"""System test: the ``/v1/scores`` resource (the ``user-score-dashboard`` change).

Driven through the Django test client (``client.force_login``), mirroring the
existing aggregation + owner-routes system tests. Covers:

  - ``GET /v1/scores`` — paginated list; 401 anonymous; per-user isolation;
    page_size clamp to 50; total/total_pages correct.
  - ``GET /v1/scores/{id}`` — detail happy; 404 unknown; 404 not-mine; 401 anon.
  - ``PATCH /v1/scores/{id}`` — 200 happy (average recomputed); 404 unknown;
    404 not-mine; 401 anon; 403 no-CSRF.
  - ``DELETE /v1/scores/{id}`` — 204 happy + storage-gone (the upload file +
    job deliverables removed under ``tmp_path``); 404 unknown; 404 not-mine;
    401 anon; 403 no-CSRF.
"""
from __future__ import annotations

import json
import uuid

import pytest
from django.test import override_settings

from src.domains.identity.test_utils import make_user
from src.domains.vision.models import ScoringJob
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.test_utils import days_ago, make_accepted_result


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _login_as(client, user) -> None:
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")


def _csrf(client) -> str:
    """Seed the ``csrftoken`` cookie (via ``/``) and return it."""
    client.get("/")
    return client.cookies["csrftoken"].value


def _patch(client, result_id, body: dict, *, csrf: str | None = None):
    kw: dict = {"content_type": "application/json", "data": json.dumps(body)}
    if csrf is not None:
        kw["HTTP_X_CSRFTOKEN"] = csrf
    return client.patch(f"/v1/scores/{result_id}", **kw)


def _delete(client, result_id, *, csrf: str | None = None):
    kw: dict = {}
    if csrf is not None:
        kw["HTTP_X_CSRFTOKEN"] = csrf
    return client.delete(f"/v1/scores/{result_id}", **kw)


# ---------------------------------------------------------------------------
# GET /v1/scores — paginated list
# ---------------------------------------------------------------------------


def test_get_scores_returns_401_anonymous(client) -> None:
    """No session → session_auth falsy → 401 before the body runs."""
    assert client.get("/v1/scores").status_code == 401


def test_get_scores_returns_paginated_shape(client, user_sub) -> None:
    """Authed user → 200 with the paginated ``ScoreListOut`` shape, newest first."""
    user = make_user(sub=user_sub, nick="alba")
    _login_as(client, user)
    make_accepted_result(user_uuid=user.id, created_at=days_ago(1))
    newest = make_accepted_result(user_uuid=user.id, created_at=days_ago(0))

    response = client.get("/v1/scores")
    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body.keys()) == {"items", "page", "page_size", "total", "total_pages"}
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] == 2
    assert body["total_pages"] == 1
    # Newest first.
    assert body["items"][0]["result_id"] == str(newest.id)


def test_get_scores_isolates_users(client, user_sub) -> None:
    """User A's results do not appear in user B's list."""
    user_a = make_user(sub=user_sub, nick="bran")
    user_b = make_user(sub="auth0|other-scores-user", nick="cary")
    _login_as(client, user_a)
    make_accepted_result(user_uuid=user_a.id, created_at=days_ago(0))

    client.logout()
    _login_as(client, user_b)
    response = client.get("/v1/scores")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_get_scores_clamps_page_size_to_50(client, user_sub) -> None:
    """page_size > 50 clamps to 50."""
    user = make_user(sub=user_sub, nick="dane")
    _login_as(client, user)
    make_accepted_result(user_uuid=user.id, created_at=days_ago(0))

    response = client.get("/v1/scores?page=1&page_size=500")
    assert response.status_code == 200
    assert response.json()["page_size"] == 50


def test_get_scores_aggregations_still_resolves_after_list_route(client, user_sub) -> None:
    """Route registration order matters: ``GET /v1/scores/aggregations`` (literal)
    MUST be registered BEFORE ``GET /v1/scores/{result_id}`` (param) or it would
    be shadowed (422 on UUID parse of "aggregations"). This pins the order — the
    home Dashboard's aggregation call must keep resolving after the new detail
    route lands."""
    user = make_user(sub=user_sub, nick="emma")
    _login_as(client, user)
    response = client.get("/v1/scores/aggregations")
    assert response.status_code == 200, response.content


# ---------------------------------------------------------------------------
# GET /v1/scores/{id} — detail
# ---------------------------------------------------------------------------


def test_get_score_detail_returns_401_anonymous(client) -> None:
    assert client.get(f"/v1/scores/{uuid.uuid4()}").status_code == 401


def test_get_score_detail_returns_dto_with_holes(client, user_sub) -> None:
    """Owner → 200 with the full snapshot including ``holes``."""
    user = make_user(sub=user_sub, nick="finn")
    _login_as(client, user)
    ar = make_accepted_result(user_uuid=user.id, created_at=days_ago(0))

    response = client.get(f"/v1/scores/{ar.id}")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["result_id"] == str(ar.id)
    assert len(body["holes"]) == len(ar.holes)


def test_get_score_detail_returns_404_unknown(client, user_sub) -> None:
    """Unknown id → 404 (not 500, not 403)."""
    user = make_user(sub=user_sub, nick="gale")
    _login_as(client, user)
    response = client.get(f"/v1/scores/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_score_detail_returns_404_for_other_users_result(client, user_sub) -> None:
    """Another user's result → 404 (ID-prober learns nothing)."""
    user_a = make_user(sub=user_sub, nick="harper")
    user_b = make_user(sub="auth0|other-detail-user", nick="ivory")
    _login_as(client, user_a)
    ar_b = make_accepted_result(user_uuid=user_b.id, created_at=days_ago(0))

    response = client.get(f"/v1/scores/{ar_b.id}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /v1/scores/{id} — Modify (recompute average)
# ---------------------------------------------------------------------------


def _patch_body(scores: list[int]) -> dict:
    """A JSON PATCH body with holes scoring ``scores`` (x/y/confidence filled)."""
    return {
        "holes": [
            {"x": i, "y": i, "score": s, "confidence": 1.0}
            for i, s in enumerate(scores)
        ],
    }


def test_patch_score_returns_401_anonymous(client) -> None:
    assert _patch(client, uuid.uuid4(), _patch_body([10])).status_code == 401


def test_patch_score_recomputes_average(client, user_sub) -> None:
    """Authed owner PATCHes holes → 200, ``score_average`` reflects the new mean."""
    user = make_user(sub=user_sub, nick="jules")
    _login_as(client, user)
    csrf = _csrf(client)
    ar = make_accepted_result(
        user_uuid=user.id,
        holes=[{"x": 0, "y": 0, "score": 9, "confidence": 1.0}] * 4,  # avg 9.0
    )

    response = _patch(client, ar.id, _patch_body([10, 10, 10, 10]), csrf=csrf)
    assert response.status_code == 200, response.content
    assert response.json()["score_average"] == pytest.approx(10.0)


def test_patch_score_404_unknown(client, user_sub) -> None:
    """Unknown id → 404."""
    user = make_user(sub=user_sub, nick="kira")
    _login_as(client, user)
    csrf = _csrf(client)
    response = _patch(client, uuid.uuid4(), _patch_body([10]), csrf=csrf)
    assert response.status_code == 404


def test_patch_score_404_for_other_users_result(client, user_sub) -> None:
    """Another user's result → 404."""
    user_a = make_user(sub=user_sub, nick="leo")
    user_b = make_user(sub="auth0|other-patch-user", nick="mira")
    _login_as(client, user_a)
    csrf = _csrf(client)
    ar_b = make_accepted_result(user_uuid=user_b.id, created_at=days_ago(0))

    response = _patch(client, ar_b.id, _patch_body([10]), csrf=csrf)
    assert response.status_code == 404


def test_patch_score_403_without_csrf(client, user_sub) -> None:
    """No CSRF token on a mutating route → 403 (CSRF enforced on SessionAuth)."""
    user = make_user(sub=user_sub, nick="nyx")
    _login_as(client, user)
    client.handler.enforce_csrf_checks = True
    ar = make_accepted_result(user_uuid=user.id, created_at=days_ago(0))

    response = _patch(client, ar.id, _patch_body([10]))  # no csrf
    assert response.status_code == 403


def test_patch_score_422_invalid_target_type(client, user_sub) -> None:
    """impl-review F1: ScoreUpdateIn.target_type is a Literal mirroring
    ScoringJobIn/AcceptResultIn — the BFF is the only gate (the model CharField
    has no choices=). An out-of-vocabulary value → 422, not persisted.
    """
    user = make_user(sub=user_sub, nick="vim")
    _login_as(client, user)
    csrf = _csrf(client)
    ar = make_accepted_result(user_uuid=user.id, created_at=days_ago(0))

    body = _patch_body([10])
    body["target_type"] = "not-a-real-target-type"
    response = _patch(client, ar.id, body, csrf=csrf)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /v1/scores/{id} — hard-delete + best-effort storage cleanup
# ---------------------------------------------------------------------------


def test_delete_score_returns_401_anonymous(client) -> None:
    assert _delete(client, uuid.uuid4()).status_code == 401


def test_delete_score_204_and_removes_storage_objects(client, user_sub, tmp_path) -> None:
    """Authed owner DELETEs → 204; the row is gone; under FS the upload file +
    the job deliverables are removed from disk; the ScoringJob row stays."""
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        user = make_user(sub=user_sub, nick="otto")
        _login_as(client, user)
        csrf = _csrf(client)
        storage = ScoringStorage()
        input_path = storage.save_upload(b"upload-bytes", "photo.jpg")
        marked_path = storage.write_deliverable_bytes(
            uuid.uuid4(), "x_marked.png", b"MARKED",
        )
        job = ScoringJob.objects.create(
            user_uuid=user.id, status=ScoringJob.Status.SUCCEEDED,
            input_path=input_path, marked_image_path=marked_path,
        )
        ar = make_accepted_result(user_uuid=user.id, source_job=job.id)

        # Sanity: the files exist before the delete.
        assert (tmp_path / "scoring" / input_path).exists()
        response = _delete(client, ar.id, csrf=csrf)
        assert response.status_code == 204, response.content

        # Row gone; ScoringJob retained; storage objects gone.
        from src.domains.vision.models import AcceptedResult
        assert not AcceptedResult.objects.filter(id=ar.id).exists()
        assert ScoringJob.objects.filter(id=job.id).exists()
        assert not (tmp_path / "scoring" / input_path).exists()


def test_delete_score_404_unknown(client, user_sub) -> None:
    """Unknown id → 404."""
    user = make_user(sub=user_sub, nick="pax")
    _login_as(client, user)
    csrf = _csrf(client)
    response = _delete(client, uuid.uuid4(), csrf=csrf)
    assert response.status_code == 404


def test_delete_score_404_for_other_users_result(client, user_sub) -> None:
    """Another user's result → 404."""
    user_a = make_user(sub=user_sub, nick="quinn")
    user_b = make_user(sub="auth0|other-delete-user", nick="rhys")
    _login_as(client, user_a)
    csrf = _csrf(client)
    ar_b = make_accepted_result(user_uuid=user_b.id, created_at=days_ago(0))

    response = _delete(client, ar_b.id, csrf=csrf)
    assert response.status_code == 404


def test_delete_score_403_without_csrf(client, user_sub) -> None:
    """No CSRF token → 403."""
    user = make_user(sub=user_sub, nick="sage")
    _login_as(client, user)
    client.handler.enforce_csrf_checks = True
    ar = make_accepted_result(user_uuid=user.id, created_at=days_ago(0))

    response = _delete(client, ar.id)  # no csrf
    assert response.status_code == 403
