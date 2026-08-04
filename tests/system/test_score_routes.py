"""System test: the ``/v1/scores`` resource (the ``user-score-dashboard`` change).

Driven through the Django test client (``client.force_login``), mirroring the
existing aggregation + owner-routes system tests. Covers:

  - ``GET /v1/scores`` — paginated list; 401 anonymous; per-user isolation;
    page_size clamp to 50; total/total_pages correct.
  - ``GET /v1/scores/{id}`` — detail happy; 404 unknown; 404 not-mine; 401 anon.

PATCH/DELETE routes land in Phase 2 (their tests + the CSRF matrix are appended
here in that phase).
"""
from __future__ import annotations

import uuid

import pytest

from src.domains.identity.test_utils import make_user
from src.domains.vision.test_utils import days_ago, make_accepted_result


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _login_as(client, user) -> None:
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")


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
